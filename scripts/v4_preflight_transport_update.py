"""Explicit audited transport-only correction before any valid incumbent exists."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json,save_json,digest

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--reason',required=True);args=p.parse_args();root=args.run_dir.resolve()
    if read_json(root/'orchestration.json')['phase']!='cooperatively_stopped':raise ValueError('Stop controller first')
    s=read_json(root/'state.json');old=read_json(root/'protocol.json');ledger=read_json(root/'llm/ledger.json')
    if s['incumbent'] or s['islands'] or s['pending'] or s['frozen'] or s['validation_started']:
        raise ValueError('Preflight transport changes require no valid incumbent, no active task and no validation')
    if any(r.get('full',{}).get('valid') for r in s['records'].values()):raise ValueError('Valid candidate already exists')
    if any(r['status']=='started' for r in ledger):raise ValueError('Request is still running')
    project=Path(__file__).resolve().parents[1];new=copy.deepcopy(old)
    changed=[]
    for name,previous in old['config']['implementation_hashes'].items():
        current=hashlib.sha256((project/name).read_bytes()).hexdigest()
        if current!=previous:
            changed.append(name);new['config']['implementation_hashes'][name]=current
    if changed!=['aad/v4_provider.py']:raise ValueError('Only provider transport change is allowed')
    event={'reason':args.reason,'after_requests':len(ledger),'previous_protocol':old,'next_protocol':new,
           'budget_reset':False,'retained_invalid_candidates':len(s['records'])}
    s.setdefault('preflight_protocol_changes',[]).append(event)
    s['signature']=digest(json.dumps(new,sort_keys=True))
    save_json(root/'protocol.json',new);save_json(root/'state.json',s)
    print({'updated':changed,'preserved_request_count':len(ledger),'reason':args.reason})

if __name__=='__main__':main()
