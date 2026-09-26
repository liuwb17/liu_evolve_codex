"""Create a portable source + evidence bundle, with no credentials or build caches."""

import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ["aad", "configs", "docs", "scripts", "tests", "docker", "data/ahc001", "data/official_public",
           "runs/offline", "runs/deepseek", "runs/official_tool_check",
           "data/official_system_seeds", "data/official_system_1000", "runs/system_test_1000",
           "runs/v2_deepseek", "runs/v2_system_1000", "runs/v2_system_1000_final", "runs/v2_system_1000_runtime5"]
FILES = ["README.md", "pyproject.toml", ".gitignore",
         "runs/v2_probe_initial.json", "runs/v2_seed_full_budget.json"]


def main():
    paths = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        paths.extend(path for path in (ROOT / folder).rglob("*") if path.is_file()
                     and not any(part in {"__pycache__", "cache"} for part in path.relative_to(ROOT).parts))
    destination = ROOT / "dist/mosaic-aad-v0.2.0.zip"
    destination.parent.mkdir(exist_ok=True)
    checksums = {}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            content = path.read_bytes()
            if re.search(rb"sk-[a-zA-Z0-9]{15,}", content):
                raise RuntimeError(f"Possible credential in {path.relative_to(ROOT)}; refusing package")
            name = path.relative_to(ROOT).as_posix()
            archive.writestr("mosaic-aad/" + name, content)
            checksums[name] = hashlib.sha256(content).hexdigest()
        archive.writestr("mosaic-aad/package-manifest.json", json.dumps(checksums, indent=2))
    print(json.dumps({"path": str(destination), "files": len(checksums),
                      "bytes": destination.stat().st_size,
                      "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
