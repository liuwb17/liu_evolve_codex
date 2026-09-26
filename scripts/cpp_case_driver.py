"""Trusted in-container timer: excludes Docker startup, includes candidate I/O."""
import json
import sys
from pathlib import Path
sys.path.insert(0,'/library')
from aad.runner import execute

result=execute(['/work/solution',sys.argv[1]],sys.stdin.read(),Path('/work'),5.0)
print(json.dumps(result))
