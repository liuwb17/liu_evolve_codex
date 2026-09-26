"""Bounded process execution and trusted scoring, with optional Docker isolation."""

import ast
import concurrent.futures
import math
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .io import digest, read_json, save_json
from .problem import Case, judge

OUTPUT_LIMIT = 65536


def terminate(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def execute(command: list[str], input_text: str, cwd: Path, timeout: float) -> dict:
    # API keys and other parent environment secrets never enter a candidate process.
    env = {k: v for k, v in os.environ.items()
           if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC"}}
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=cwd, env=env,
                               start_new_session=os.name != "nt")
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()

    def drain(stream, target):
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            remaining = OUTPUT_LIMIT - len(target)
            target.extend(chunk[:max(0, remaining)])
            if len(chunk) > remaining:
                exceeded.set()

    readers = [threading.Thread(target=drain, args=(stream, target), daemon=True)
               for stream, target in zip((process.stdout, process.stderr), buffers)]
    for reader in readers:
        reader.start()
    def feed():
        try:
            process.stdin.write(input_text.encode())
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    status = "AC"
    try:
        while process.poll() is None:
            if exceeded.is_set():
                status = "OLE"
                break
            if time.monotonic() - started > timeout:
                status = "TLE"
                break
            time.sleep(0.005)
    finally:
        terminate(process)
        process.wait(timeout=10)
        writer.join(timeout=2)
        for reader in readers:
            reader.join(timeout=2)
    if exceeded.is_set():
        status = "OLE"
    if status == "AC" and process.returncode:
        status = "RE"
    for stream in (process.stdout, process.stderr):
        stream.close()
    return {"status": status, "stdout": buffers[0].decode("utf-8", errors="replace"),
            "stderr": buffers[1].decode("utf-8", errors="replace"),
            "seconds": time.monotonic() - started, "returncode": process.returncode}


def summarize(results: list[dict]) -> dict:
    if not results:
        raise ValueError("Cannot evaluate an empty case set")
    scores = [r["score"] / 1e9 for r in results]
    valid = all(r["status"] == "AC" for r in results)
    mean = statistics.mean(scores)
    # Selection heuristic only; not a claimed formal confidence interval.
    stderr = statistics.stdev(scores) / math.sqrt(len(scores)) if len(scores) > 1 else 0.0
    return {"valid": valid, "mean": mean, "min": min(scores), "stderr": stderr,
            "selection_score": mean - 0.5 * stderr if valid else -1.0,
            "max_seconds": max(r["seconds"] for r in results),
            "coverage": statistics.mean(r.get("coverage", 0.0) for r in results),
            "weak_fraction": statistics.mean(r.get("weak_fraction", 1.0) for r in results),
            "cases": results}


class LocalEvaluator:
    def __init__(self, cache: Path, backend="native", timeout=5.0, workers=2,
                 docker_image="python:3.11-slim"):
        self.cache, self.backend, self.timeout = cache, backend, timeout
        self.workers, self.docker_image = workers, docker_image
        self.hits, self.executions = 0, 0
        self._lock = threading.Lock()
        image_id = docker_image
        if backend == "docker":
            result = subprocess.run(["docker", "image", "inspect", docker_image, "--format", "{{.Id}}"],
                                    capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise RuntimeError("Docker image unavailable; start Docker and pull the configured image")
            image_id = result.stdout.strip()
        self.runtime = {"backend": backend, "docker_image_id": image_id if backend == "docker" else None,
                        "host_python": sys.version, "timeout": timeout, "workers": workers}
        self.signature = digest(f"judge-v2|{backend}|{timeout}|{sys.version}|{image_id}")

    def _case(self, code: str, case: Case) -> dict:
        key = digest(code + case.fingerprint + self.signature)
        path = self.cache / (key + ".json")
        if path.exists():
            with self._lock:
                self.hits += 1
            return {**read_json(path), "name": case.name}
        container_name = "aad-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="aad-") as directory:
            folder = Path(directory)
            (folder / "candidate.py").write_text(code, encoding="utf-8")
            if self.backend == "docker":
                command = ["docker", "run", "--rm", "--name", container_name, "--network=none",
                           "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                           "--pids-limit=32", "--memory=512m", "--cpus=1", "--user=65534:65534",
                           "--mount", f"type=bind,source={folder},target=/work,readonly",
                           "--workdir=/work", "-i", self.docker_image,
                           "python", "-I", "-S", "-B", "/work/candidate.py"]
            elif self.backend == "native":
                command = [sys.executable, "-I", "-S", "-B", str(folder / "candidate.py")]
            else:
                raise ValueError(f"Unknown backend: {self.backend}")
            try:
                result = execute(command, case.text, folder, self.timeout)
            finally:
                if self.backend == "docker":
                    subprocess.run(["docker", "rm", "-f", container_name], timeout=15,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result["status"] == "AC":
            result.update(judge(case.text, result["stdout"]))
        else:
            result.update(score=0, message=result["stderr"][-3000:])
        result.update(name=case.name, input_sha256=case.fingerprint)
        save_json(path, result)
        with self._lock:
            self.executions += 1
        return result

    def evaluate(self, code: str, cases: list[Case]) -> dict:
        try:
            ast.parse(code)
        except SyntaxError as error:
            return summarize([{"name": c.name, "status": "CE", "score": 0,
                               "seconds": 0, "message": str(error)} for c in cases])
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(lambda case: self._case(code, case), cases))
        return summarize(results)
