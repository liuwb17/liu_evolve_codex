"""Read-only v5 progress query; no provider initialization or private inputs."""
import argparse
import json
from collections import Counter
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json
from aad.v4_protocol import vectors

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);root=p.parse_args().run_dir
    state=read_json(root/'state.json') if (root/'state.json').exists() else {}
    ledger=read_json(root/'llm/ledger.json') if (root/'llm/ledger.json').exists() else []
    records=state.get('records',{});incumbent=state.get('incumbent')
    result={'requests_reserved':len(ledger),'request_statuses':dict(Counter(r['status'] for r in ledger)),
        'known_tokens':sum(r.get('usage',{}).get('total_tokens',0) for r in ledger),
        'unknown_usage_requests':sum('total_tokens' not in r.get('usage',{}) for r in ledger),
        'candidates':len(records),'valid_full_candidates':sum(r.get('full',{}).get('valid',False) for r in records.values()),
        'incumbent_repeated_train_mean':statistics.mean(vectors(records[incumbent]['repeats']).values()) if incumbent else None,
        'pending_arm':state.get('pending',{}).get('arm') if state.get('pending') else None,
        'validation_started':state.get('validation_started',False),'frozen':state.get('frozen',False),
        'recent_events':[{k:e.get(k) for k in ('arm','status','parent_gain','error')} for e in state.get('events',[])[-4:]]}
    if (root/'orchestration.json').exists():result['orchestration']=read_json(root/'orchestration.json')['phase']
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
