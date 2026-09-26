"""Trusted batch wrapper, executed inside Docker by official_tools.py."""

import json
import re
import subprocess
from pathlib import Path

results = []
for input_path in sorted(Path("/work").glob("*.in")):
    result = subprocess.run(["/tools/target/release/vis", str(input_path), str(input_path.with_suffix(".out"))],
                            capture_output=True, text=True, check=True, timeout=10)
    match = re.search(r"^Score = (\d+)$", result.stdout, re.MULTILINE)
    if not match:
        raise RuntimeError("Rust scorer did not return a score")
    results.append({"file": input_path.name, "score": int(match.group(1)), "message": result.stdout.strip()})
Path("/work/rust_scores.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(f"Rust scorer verified {len(results)} outputs")
