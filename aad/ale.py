"""Adapter for the pinned upstream ALE-Bench public APIs; imported lazily."""

from pathlib import Path

from .io import digest, read_json, save_json
from .problem import Case, judge
from .runner import summarize

ALE_COMMIT = "3da9b12fb5d112dabb3af693d1a42031c95142bc"


def start_session(workers=2):
    try:
        import ale_bench
    except ImportError as error:
        raise RuntimeError("Install ALE-Bench and its Docker images; see docs/ALE_BENCH.md") from error
    return ale_bench.start(problem_id="ahc001", lite_version=False, num_workers=workers,
                           run_visualization_server=False)


class AleEvaluator:
    def __init__(self, root, config):
        self.session = start_session(config.workers)
        self.root, self.config = root, config

    def evaluate(self, code: str, cases: list[Case]):
        # Public API only: never access _private_inputs/private_seeds/standings.
        result = self.session.case_eval([case.text for case in cases], code,
                                        code_language="python", judge_version="202301",
                                        time_limit=self.config.timeout,
                                        skip_local_visualization=True)
        rows = []
        for case, item in zip(cases, result.case_results, strict=True):
            status = {"ACCEPTED": "AC", "COMPILATION_ERROR": "CE", "WRONG_ANSWER": "WA",
                      "TIME_LIMIT_EXCEEDED": "TLE", "RUNTIME_ERROR": "RE",
                      "MEMORY_LIMIT_EXCEEDED": "MLE", "OUTPUT_LIMIT_EXCEEDED": "OLE",
                      "INTERNAL_ERROR": "IE"}.get(item.judge_result.value, item.judge_result.value)
            row = {"name": case.name, "status": status, "score": item.absolute_score,
                   "seconds": item.execution_time, "message": item.message,
                   "stdout": item.output_str or "", "stderr": item.error_str or ""}
            if status == "AC":
                independent = judge(case.text, item.output_str or "")
                if independent["status"] != "AC" or independent["score"] != item.absolute_score:
                    row.update(status="SCORER_MISMATCH", score=0,
                               message="Independent strict judge disagrees with ALE-Bench")
                else:
                    row.update(independent)
            rows.append(row)
        return summarize(rows)

    def close(self):
        try:
            self.session.save(str(self.root / "ale_session.json"))
        finally:
            self.session.close()


def prepare_public_dataset(destination: Path, workers=2):
    """Split official PUBLIC seeds only. Local holdout is NOT the private benchmark."""
    if (destination / "manifest.json").exists():
        raise ValueError("Dataset exists")
    session = start_session(workers)
    try:
        seeds = list(session.public_seeds)
        inputs = session.case_gen(seeds)
        if len(inputs) < 6:
            raise ValueError("Need >=6 full-version public cases")
        boundaries = [int(len(inputs) * 0.6), int(len(inputs) * 0.8)]
        manifest = {"problem": "ahc001", "kind": "ALE-Bench-public-only",
                    "upstream_commit": ALE_COMMIT, "splits": {"train": [], "validation": [], "holdout": []}}
        for i, (seed, text) in enumerate(zip(seeds, inputs, strict=True)):
            split = "train" if i < boundaries[0] else "validation" if i < boundaries[1] else "holdout"
            folder = destination / split
            folder.mkdir(parents=True, exist_ok=True)
            filename = f"{split}/public_{i:04d}.txt"
            (destination / filename).write_text(text, encoding="utf-8")
            manifest["splits"][split].append({"file": filename, "seed": seed, "sha256": digest(text)})
        save_json(destination / "manifest.json", manifest)
        return manifest
    finally:
        session.close()


def official_final(code_path: Path, output: Path, workers=2, private=False):
    code = code_path.read_text(encoding="utf-8")
    receipt = output.with_suffix(".receipt.json")
    if output.exists() or receipt.exists():
        raise ValueError("Evaluation already started or finished; choose a new experiment explicitly")
    save_json(receipt, {"source_sha256": digest(code), "private_requested": private, "status": "started"})
    session = start_session(workers)
    try:
        public = session.public_eval(code, code_language="python", judge_version="202301")
        report = {"source_sha256": digest(code), "upstream_commit": ALE_COMMIT,
                  "public_score": public.overall_absolute_score,
                  "public_status": public.overall_judge_result.value, "private_evaluated": False}
        if private:
            # Terminal action on the previously frozen code. No return to evolution.
            result, rank, performance = session.private_eval(code, code_language="python", judge_version="202301")
            report.update(private_evaluated=True, private_score=result.overall_absolute_score,
                          private_status=result.overall_judge_result.value, rank=rank, performance=performance)
        save_json(output, report)
        save_json(receipt, {"source_sha256": digest(code), "private_requested": private, "status": "finished"})
        return report
    finally:
        session.close()
