"""Source-only v4 delivery; never include credentials, experiment solvers or private data."""
import hashlib
import re
import zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import save_json

def main():
    root=Path(__file__).resolve().parents[1];out=root/'dist/mosaic-aad-v0.4.0.zip'
    if out.exists():raise ValueError('Refusing to overwrite an existing release')
    files=[root/'README.md',root/'pyproject.toml']
    for folder in ('aad','scripts','tests','docs','docker'):
        files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.tmp'))
    files=sorted(set(files));payloads=[]
    for path in files:
        data=path.read_bytes()
        if re.search(rb'\bsk-[A-Za-z0-9]{20,}\b',data):raise ValueError('Possible credential in '+str(path))
        payloads.append((str(path.relative_to(root)).replace('\\','/'),data))
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED) as z:
        for name,data in payloads:z.writestr(name,data)
    manifest={'artifact':out.name,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
        'files':len(payloads),'size_bytes':out.stat().st_size,'contains_private_data':False,
        'contains_experiment_solver_sources':False,'v4_live_experiment_performed':False,
        'scope':'Source project, tests, docs. Official data/tools must be prepared separately.'}
    save_json(out.with_suffix('.manifest.json'),manifest);print(manifest)

if __name__=='__main__':main()
