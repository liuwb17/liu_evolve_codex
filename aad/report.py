"""Freeze before reading holdout; report paired differences without search feedback."""

import random
import statistics
from pathlib import Path

from .config import Config
from .io import digest, read_json, save_json
from .problem import load_split
from .runner import LocalEvaluator


def paired_bootstrap(baseline, champion, seed=2026):
    differences = [(b["score"] - a["score"]) / 1e9 for a, b in zip(baseline, champion, strict=True)]
    rng = random.Random(seed)
    samples = sorted(statistics.mean(rng.choices(differences, k=len(differences))) for _ in range(2000))
    return {"mean_delta": statistics.mean(differences), "bootstrap_95_percent": [samples[50], samples[1949]],
            "n": len(differences), "note": "Descriptive paired bootstrap; one search seed, not a superiority claim"}


def finalize(root: Path):
    path = root / "state.json"
    state = read_json(path)
    report_path = root / "report.json"
    if report_path.exists():
        return read_json(report_path)
    if state.get("holdout_started"):
        raise ValueError("Holdout evaluation was interrupted. It will not be silently repeated.")
    config = Config(**read_json(root / "config.json"))
    if not state.get("champion"):
        raise ValueError("No champion to freeze")
    # Freeze persists before the first holdout read; Evolution refuses further search.
    state.update(frozen=True, holdout_started=True)
    save_json(path, state)
    cases = load_split(Path(config.dataset), "holdout")
    if config.backend == "ale":
        from .ale import AleEvaluator
        evaluator = AleEvaluator(root, config)
    else:
        evaluator = LocalEvaluator(root / "cache", config.backend, config.timeout, config.workers, config.docker_image)
    evaluations = {}
    try:
        for name in ("baseline", "champion"):
            candidate_id = state[name]
            code = (root / "candidates" / candidate_id / "solution.py").read_text(encoding="utf-8")
            if digest(code) != candidate_id:
                raise ValueError("Candidate source hash mismatch")
            evaluations[name] = evaluator.evaluate(code, cases)
    finally:
        if hasattr(evaluator, "close"):
            evaluator.close()
    baseline = state["candidates"][state["baseline"]]
    champion = state["candidates"][state["champion"]]
    ledger = read_json(root / "llm/ledger.json") if (root / "llm/ledger.json").exists() else []
    usage = {}
    for entry in ledger:
        for key, value in entry.get("usage", {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + value
    report = {"problem": "ahc001", "provider": config.provider, "backend": config.backend,
              "dataset_kind": read_json(Path(config.dataset) / "manifest.json")["kind"],
              "iterations": state["iteration"], "candidates": len(state["candidates"]),
              "requests": len(ledger), "usage": usage, "search_seconds": state["elapsed_seconds"],
              "official_private_evaluated": False,
              "baseline": {"id": state["baseline"], "train_mean": baseline["train"]["mean"],
                           "validation_mean": baseline["validation"]["mean"], "holdout": evaluations["baseline"]},
              "champion": {"id": state["champion"], "train_mean": champion["train"]["mean"],
                           "validation_mean": champion["validation"]["mean"], "holdout": evaluations["champion"]},
              "paired_holdout": paired_bootstrap(evaluations["baseline"]["cases"], evaluations["champion"]["cases"])}
    save_json(report_path, report)
    rows = ["# AHC001 实验结果", "", f"模式：{config.provider}；执行：{config.backend}；数据：{report['dataset_kind']}。",
            "本报告是本地实验，不是 ALE-Bench 私有集排名。验证集用于选型，留出集在冻结后评测。", "",
            "| 候选 | 训练均分 | 验证均分 | 留出均分 | 留出全部合法 |", "|---|---:|---:|---:|---|",
            f"| 初始程序 | {baseline['train']['mean']:.6f} | {baseline['validation']['mean']:.6f} | {evaluations['baseline']['mean']:.6f} | {evaluations['baseline']['valid']} |",
            f"| 最终程序 | {champion['train']['mean']:.6f} | {champion['validation']['mean']:.6f} | {evaluations['champion']['mean']:.6f} | {evaluations['champion']['valid']} |", "",
            "表中均分为原始题目分数除以 1e9。", "",
            f"迭代 {state['iteration']}，候选 {len(state['candidates'])}，API 请求 {len(ledger)}。",
            f"留出配对平均差：{report['paired_holdout']['mean_delta']:.6f}。",
            f"95% 描述性 bootstrap 区间：{report['paired_holdout']['bootstrap_95_percent']}。",
            "单次搜索、小样本结果不支持优于现有 AAD 框架的结论。", "",
            "逐例输入哈希、状态、输出、耗时见 report.json；谱系与搜索事件见 state.json。"]
    (root / "report.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    best = read_json(root / "best.json")
    best["holdout_evaluated"] = True
    save_json(root / "best.json", best)
    return report
