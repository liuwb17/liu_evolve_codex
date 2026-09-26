"""Read-only experiment status; never reads keys, solution sources or private inputs."""
import argparse
import json
from collections import Counter
from pathlib import Path


def status(root):
    state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    ledger=json.loads((root/'llm/ledger.json').read_text(encoding='utf-8'))
    records=state.get('records',{})
    evaluated=[(k,r['full']) for k,r in records.items() if r.get('full',{}).get('valid')]
    best=max(evaluated,key=lambda item:item[1]['mean']) if evaluated else None
    latest=ledger[-1] if ledger else None
    completed_calls={event['call_id'] for event in state.get('events',[])}
    activity='checkpointed'
    if latest and latest['status']=='started':activity='reserved_request_without_completion'
    elif latest and latest['id'] not in completed_calls:activity='request_completed_evaluation_pending'
    if state.get('validation_started'):activity='validation_started_development_closed'
    if state.get('frozen'):activity='frozen'
    return {
        'run':str(root),'requests_reserved':len(ledger),
        'request_statuses':dict(Counter(r['status'] for r in ledger)),
        'latest_request':{k:latest.get(k) for k in ('id','status','seconds')} if latest else None,
        'checkpoint_activity':activity,
        'known_usage':{key:sum(r.get('usage',{}).get(key,0) for r in ledger)
                       for key in ('prompt_tokens','completion_tokens','total_tokens')},
        'requests_without_reported_usage':sum(not r.get('usage') for r in ledger),
        'indexed_programs':len(records),'valid_full_train_programs':len(evaluated),
        'highest_full_train_mean':best[1]['mean'] if best else None,
        'highest_mean_source_sha256':best[0] if best else None,
        'frozen':state.get('frozen',False),
        'public_validation_evaluated':any('validation' in r for r in records.values()),
        'evaluation_policy':state.get('evaluation_policy','multi-fidelity'),
        'archive_policy':state.get('archive_policy','family-labels'),
        'restart_patience':state.get('restart_patience',0),
        'numeric_search':state.get('numeric_search_summary'),
        'latest_events':[{k:e.get(k) for k in ('call_id','operator','status','mean')}
                         for e in state.get('events',[])[-5:]],
    }


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',type=Path,default=Path(__file__).resolve().parents[1]/'runs/v3_pro_fromscratch')
    args=parser.parse_args()
    print(json.dumps(status(args.run_dir),ensure_ascii=False,indent=2))
