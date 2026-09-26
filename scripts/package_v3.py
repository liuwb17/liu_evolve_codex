"""Package a completed cold-start experiment without replacing the historical v0.2 bundle."""
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from package_project import ROOT,FOLDERS,FILES


def main():
    run=ROOT/'runs/v3_pro_fromscratch'
    state=json.loads((run/'state.json').read_text(encoding='utf-8'))
    if not state.get('frozen'):raise ValueError('Finish selection and freeze before packaging')
    canonical_source=(run/'best.cpp').read_text(encoding='utf-8').encode('utf-8')
    if hashlib.sha256(canonical_source).hexdigest()!=state['champion']:
        raise ValueError('Frozen source changed')
    report=json.loads((ROOT/'runs/v3_system_1000/report.json').read_text(encoding='utf-8'))
    if report['count']!=1000 or report['source_sha256']!=state['champion']:
        raise ValueError('Complete the matching terminal 1000-case test before packaging')
    subprocess.run([sys.executable,str(ROOT/'scripts/v3_audit.py'),'--run-dir',str(run)],check=True)
    audit=json.loads((run/'source_audit.json').read_text(encoding='utf-8'))
    if audit['indexed_programs']!=len(state['records']) or not audit['all_sources_match_audited_origin'] or not audit['all_supplied_parent_sources_belong_to_this_run']:
        raise ValueError('Run the source audit before packaging')
    folders=[*FOLDERS,'runs/v3_fromscratch','runs/v3_pro_fromscratch',
             'runs/v3_system_1000']
    paths=[ROOT/name for name in FILES]
    paths.append(ROOT/'data/human_references/kusano/provenance.json')
    paths.extend(ROOT/'runs/human_kusano_1000'/name for name in (
        'report.json','raw_1000.json','per_case.csv','comparison.json','rust_check/rust_scores.json'))
    for folder in folders:
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file()
            and not any(part in {'__pycache__','cache','builds'} for part in p.relative_to(ROOT).parts)
            and p.suffix!='.tmp' and p.name!='STOP_AFTER_CURRENT')
    destination=ROOT/'dist/mosaic-aad-v0.3.0.zip'
    if destination.exists():raise FileExistsError('Existing v0.3 package is preserved; select a new release path explicitly')
    destination.parent.mkdir(exist_ok=True)
    contents=[]
    for path in sorted(set(paths)):
        content=path.read_bytes()
        if re.search(rb'sk-[a-zA-Z0-9]{15,}',content):
            raise RuntimeError(f'Possible credential in {path.relative_to(ROOT)}; refusing package')
        contents.append((path.relative_to(ROOT).as_posix(),content))
    checksums={name:hashlib.sha256(data).hexdigest() for name,data in contents}
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in contents:archive.writestr('mosaic-aad/'+name,data)
        archive.writestr('mosaic-aad/package-manifest.json',json.dumps(checksums,indent=2))
    print(json.dumps({'path':str(destination),'files':len(checksums),
        'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
        'exceeds_kusano':report['exceeds_kusano']},indent=2))


if __name__=='__main__':main()
