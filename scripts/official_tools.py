"""Build original Rust tools, generate public inputs, cross-check frozen outputs.

This is an integration check using ALE-Bench's original problem tools, not a
replacement for session.private_eval or an official rank calculation.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aad.io import digest, read_json, save_json
from aad.problem import load_split
from aad.runner import LocalEvaluator

RUST_IMAGE = "rust:1.85-slim"


def docker(args):
    subprocess.run(["docker", *args], check=True)


def build(reference):
    tools = (reference / "tools").resolve()
    docker(["run", "--rm", "--mount", f"type=bind,source={tools},target=/tools",
            "--workdir=/tools", RUST_IMAGE, "cargo", "build", "--release", "--locked"])


def prepare(reference, destination):
    if (destination / "manifest.json").exists():
        raise ValueError("Destination manifest already exists")
    # Deliberately select only the public field, never print the metadata document.
    public_seeds = read_json(reference / "data.json")["seeds"]["public"]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "public_seeds.txt").write_text("\n".join(map(str, public_seeds)) + "\n", encoding="utf-8")
    docker(["run", "--rm", "--network=none", "--mount",
            f"type=bind,source={(reference / 'tools').resolve()},target=/tools,readonly",
            "--mount", f"type=bind,source={destination.resolve()},target=/work",
            RUST_IMAGE, "/tools/target/release/gen", "/work/public_seeds.txt", "--dir", "/work/inputs"])
    splits = {"train": [], "validation": [], "holdout": []}
    for i, seed in enumerate(public_seeds):
        filename = f"inputs/{i:04d}.txt"
        text = (destination / filename).read_text(encoding="utf-8")
        split = "train" if i < len(public_seeds) * 0.6 else "validation" if i < len(public_seeds) * 0.8 else "holdout"
        splits[split].append({"file": filename, "seed": seed, "sha256": digest(text)})
    save_json(destination / "manifest.json", {"problem": "ahc001", "kind": "ALE-Bench-public-original-Rust",
              "dataset_revision": "0f42617", "splits": splits})
    print("Official public inputs:", len(public_seeds), flush=True)


def verify(reference, dataset, run, output):
    state = read_json(run / "state.json")
    if not state.get("frozen"):
        raise ValueError("Freeze the search before this independent integration check")
    if (output / "comparison.json").exists():
        raise ValueError("Comparison already exists")
    output.mkdir(parents=True, exist_ok=True)
    cases = [case for split in ("train", "validation", "holdout") for case in load_split(dataset, split)]
    evaluator = LocalEvaluator(output / "cache", backend="docker", workers=2, timeout=5.0)
    comparison = {"dataset": str(dataset), "kind": "official-public-tool-integration-not-private-ranking",
                  "source_run": str(run), "count": len(cases), "solutions": {}}
    for name in ("baseline", "champion"):
        candidate_id = state[name]
        code = (run / "candidates" / candidate_id / "solution.py").read_text(encoding="utf-8")
        if digest(code) != candidate_id:
            raise ValueError("Source hash mismatch")
        result = evaluator.evaluate(code, cases)
        folder = output / name
        folder.mkdir(exist_ok=True)
        for i, row in enumerate(result["cases"]):
            (folder / f"{i:04d}.in").write_text(cases[i].text, encoding="utf-8")
            (folder / f"{i:04d}.out").write_text(row.get("stdout", ""), encoding="utf-8")
        save_json(folder / "local.json", result)
        # Trusted checker runs inside the image; candidates have already finished.
        # The checker script is mounted read-only and reads only these outputs.
        docker(["run", "--rm", "--network=none", "--mount",
                f"type=bind,source={(reference / 'tools').resolve()},target=/tools,readonly",
                "--mount", f"type=bind,source={folder.resolve()},target=/work",
                "--mount", f"type=bind,source={Path(__file__).resolve().parent},target=/scripts,readonly",
                "--workdir=/work", "python:3.11-slim", "python", "/scripts/rust_score_batch.py"])
        original = read_json(folder / "rust_scores.json")
        mismatches = [i for i, row in enumerate(result["cases"]) if row["status"] != "AC" or original[i]["score"] != row["score"]]
        comparison["solutions"][name] = {"source_sha256": candidate_id, "mean": result["mean"],
                                          "all_valid": result["valid"], "rust_mismatches": mismatches,
                                          "max_seconds": result["max_seconds"]}
        print(name, comparison["solutions"][name], flush=True)
    save_json(output / "comparison.json", comparison)
    if any(v["rust_mismatches"] for v in comparison["solutions"].values()):
        raise RuntimeError("Official scorer mismatch; see comparison.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "prepare", "verify"])
    parser.add_argument("--reference", type=Path, default=Path("data/reference/ahc001"))
    parser.add_argument("--dataset", type=Path, default=Path("data/official_public"))
    parser.add_argument("--run", type=Path, default=Path("runs/deepseek"))
    parser.add_argument("--output", type=Path, default=Path("runs/official_tool_check"))
    args = parser.parse_args()
    if args.action == "build":
        build(args.reference)
    elif args.action == "prepare":
        prepare(args.reference, args.dataset)
    else:
        verify(args.reference, args.dataset, args.run, args.output)


if __name__ == "__main__":
    main()
