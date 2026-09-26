"""Preserve all completed cache records from the interrupted 4.25 s attempt."""
import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aad.io import digest,read_json,save_json

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,default=ROOT/'runs/v2_system_1000')
    parser.add_argument('--cache-root',type=Path,default=ROOT/'runs/cpp_evaluations')
    args=parser.parse_args()
    receipt=read_json(args.run/'receipt.json')
    source=(ROOT/'runs/v2_deepseek/best.cpp').read_text(encoding='utf-8')
    assert digest(source)==receipt['source_sha256']
    build=read_json(args.cache_root/'builds'/digest(source)/'compiled.json')
    manifest=read_json(ROOT/'data/official_system_1000/manifest.json')
    rows=[]
    for case in manifest['cases']:
        key=digest(source+case['sha256']+build['image']+str(receipt['search_seconds'])+'cpp-v1')
        path=args.cache_root/'cache'/(key+'.json')
        if path.exists():rows.append(read_json(path))
    report={**receipt,'complete':False,'reason':'Interrupted after TLEs; retained without selective retry',
            'completed_cached_cases':len(rows),'status_counts':dict(Counter(r['status'] for r in rows)),
            'mean_including_failures':sum(r['score'] for r in rows)/len(rows) if rows else None,
            'cases':rows}
    save_json(args.run/'interrupted.json',report)
    print({k:v for k,v in report.items() if k!='cases'})

if __name__=='__main__':main()
