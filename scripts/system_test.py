"""Terminal AHC001 evaluation on all 1000 official system seeds.

Requires an already frozen run. Never exposes this data to the evolution engine.
Uses original Rust gen/vis with independent strict checking and Docker execution.
"""

import csv
import hashlib
import json
import platform
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aad.io import digest, read_json, save_json
from aad.problem import Case
from aad.runner import LocalEvaluator, summarize
from official_tools import docker, RUST_IMAGE

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data/reference/ahc001"
DATA = ROOT / "data/official_system_1000"
OUTPUT = ROOT / "runs/system_test_1000"
RUN = ROOT / "runs/deepseek"
SEEDS = ROOT / "data/official_system_seeds/seeds.txt"


def prepare():
    raw = SEEDS.read_bytes()
    if hashlib.md5(raw).hexdigest() != "8fc1ce3f4beabac6abc1bdb4206d7f7e":
        raise ValueError("Official seeds.txt MD5 does not match the problem statement")
    seeds = list(map(int, raw.decode().split()))
    ale_seeds = read_json(REFERENCE / "data.json")["seeds"]["private"]
    if len(seeds) != 1000 or len(set(seeds)) != 1000 or seeds != ale_seeds:
        raise ValueError("Official system seeds and ALE-Bench private seeds differ")
    DATA.mkdir(parents=True, exist_ok=True)
    if not (DATA / "manifest.json").exists():
        (DATA / "seeds.txt").write_bytes(raw)
        docker(["run", "--rm", "--network=none", "--mount",
                f"type=bind,source={REFERENCE / 'tools'},target=/tools,readonly",
                "--mount", f"type=bind,source={DATA},target=/work", RUST_IMAGE,
                "/tools/target/release/gen", "/work/seeds.txt", "--dir", "/work/inputs"])
        records = []
        for i, seed in enumerate(seeds):
            name = f"inputs/{i:04d}.txt"
            records.append({"file": name, "seed": seed,
                            "sha256": digest((DATA / name).read_text(encoding="utf-8"))})
        save_json(DATA / "manifest.json", {"kind": "official-system-1000-terminal-only",
                  "source": "https://img.atcoder.jp/ahc001/seeds.zip",
                  "seed_md5": hashlib.md5(raw).hexdigest(), "ale_private_exact_order_match": True,
                  "seeds_sha256": hashlib.sha256(raw).hexdigest(), "cases": records})
    manifest = read_json(DATA / "manifest.json")
    if [r["seed"] for r in manifest["cases"]] != seeds:
        raise ValueError("Stored manifest seed mismatch")
    cases = []
    for record in manifest["cases"]:
        text = (DATA / record["file"]).read_text(encoding="utf-8")
        if digest(text) != record["sha256"]:
            raise ValueError("Input changed after generation")
        cases.append(Case(record["file"], text))
    print("Verified: 1000 unique seeds; AtCoder MD5 and ALE-Bench private list match exactly", flush=True)
    return manifest, cases


def main():
    state = read_json(RUN / "state.json")
    if not state["frozen"]:
        raise ValueError("Search must be frozen")
    if (OUTPUT / "report.json").exists():
        print("Already completed:", OUTPUT / "report.json")
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest, cases = prepare()
    receipt = {"baseline": state["baseline"], "champion": state["champion"],
               "seeds_sha256": manifest["seeds_sha256"], "timeout": 5.0, "workers": 2}
    receipt_path = OUTPUT / "receipt.json"
    if receipt_path.exists() and read_json(receipt_path) != receipt:
        raise ValueError("Refusing to reuse results with different source or test inputs")
    save_json(receipt_path, receipt)
    evaluator = LocalEvaluator(OUTPUT / "cache", backend="docker", workers=2, timeout=5.0)
    report = {"kind": "full-official-system-set-original-tools-local-environment",
              "count": 1000, "ale_private_exact_order_match": True,
              "runtime": evaluator.runtime, "host": platform.platform(),
              "official_session_rank_evaluated": False, "solutions": {}}
    all_results = {}
    for name in ("baseline", "champion"):
        code = (RUN / "candidates" / state[name] / "solution.py").read_text(encoding="utf-8")
        if digest(code) != state[name]:
            raise ValueError("Frozen source changed")
        folder = OUTPUT / name
        folder.mkdir(exist_ok=True)
        rows = []
        for start in range(0, len(cases), 50):
            part = evaluator.evaluate(code, cases[start:start + 50])
            rows.extend(part["cases"])
            save_json(folder / "progress.json", {"completed": len(rows), "total": 1000,
                      "status_counts": dict(Counter(r["status"] for r in rows))})
            print(f"{name}: {len(rows)}/1000; mean={statistics.mean(r['score'] for r in rows)/1e9:.6f}", flush=True)
        for i, row in enumerate(rows):
            (folder / f"{i:04d}.in").write_text(cases[i].text, encoding="utf-8")
            (folder / f"{i:04d}.out").write_text(row.get("stdout", ""), encoding="utf-8")
        save_json(folder / "local.json", summarize(rows))
        docker(["run", "--rm", "--network=none", "--mount",
                f"type=bind,source={REFERENCE / 'tools'},target=/tools,readonly",
                "--mount", f"type=bind,source={folder},target=/work",
                "--mount", f"type=bind,source={ROOT / 'scripts'},target=/scripts,readonly",
                "--workdir=/work", "python:3.11-slim", "python", "/scripts/rust_score_batch.py"])
        rust = read_json(folder / "rust_scores.json")
        if len(rust) != 1000:
            raise ValueError("Incomplete Rust scorer result")
        mismatches = [i for i, row in enumerate(rows) if row["status"] == "AC" and rust[i]["score"] != row["score"]]
        scores = [r["score"] if r["status"] == "AC" else 0 for r in rows]
        report["solutions"][name] = {"source_sha256": state[name], "total_score": sum(scores),
                   "mean_score": statistics.mean(scores), "normalized_mean": statistics.mean(scores)/1e9,
                   "min_score": min(scores), "median_score": statistics.median(scores),
                   "status_counts": dict(Counter(r["status"] for r in rows)),
                   "max_seconds": max(r["seconds"] for r in rows), "rust_mismatches": mismatches}
        all_results[name] = rows
        save_json(OUTPUT / "partial_report.json", report)
        if mismatches:
            raise RuntimeError("Scorer mismatch: inspect partial_report.json")
    with (OUTPUT / "per_case.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case", "seed", "baseline_score", "champion_score", "delta", "baseline_status", "champion_status", "champion_seconds"])
        for i, (a, b) in enumerate(zip(all_results["baseline"], all_results["champion"])):
            writer.writerow([i, manifest["cases"][i]["seed"], a["score"], b["score"], b["score"]-a["score"], a["status"], b["status"], b["seconds"]])
    a, b = report["solutions"]["baseline"], report["solutions"]["champion"]
    report["normalized_delta"] = b["normalized_mean"] - a["normalized_mean"]
    report["relative_improvement_percent"] = (b["total_score"] / a["total_score"] - 1) * 100
    save_json(OUTPUT / "report.json", report)
    lines = ["# AHC001 官方 1000 例系统测试", "", "AtCoder 官方 seeds.txt 的 MD5 与题面一致，且 1000 个种子及顺序与 ALE-Bench private 集完全一致。使用原始 Rust 生成器和计分器，评测冻结程序，不再搜索或调参。", "",
             "| 程序 | 总分 | 归一化均分 | 状态 | 最大墙钟时间 |", "|---|---:|---:|---|---:|"]
    for name, result in report["solutions"].items():
        lines.append(f"| {name} | {result['total_score']} | {result['normalized_mean']:.9f} | {result['status_counts']} | {result['max_seconds']:.3f}s |")
    lines += ["", f"平均分提升：{report['normalized_delta']:.9f}；相对提升：{report['relative_improvement_percent']:.3f}%。",
              "", "全部 AC 输出均经原始 Rust 计分器复核。执行环境为本机 Docker、Python，逐例 5 秒墙钟上限（含容器启动），单容器 1 CPU/512 MiB，并发 2。未调用 ALE-Bench Session，也未计算官方 rank/performance；不能视作标准硬件的正式榜单成绩。",
              "", "逐例记录：per_case.csv；完整输出与计分：baseline/、champion/；种子与输入：data/official_system_1000/。"]
    (OUTPUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
