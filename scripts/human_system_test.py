"""Evaluate unmodified kusano AHC001 source on the official 1000 inputs."""
import argparse
import csv
import hashlib
import statistics
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.cpp import CppEvaluator
from aad.io import digest,read_json,save_json
from system_test import prepare,ROOT,REFERENCE
from official_tools import docker

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--count',type=int,default=1000)
    args=parser.parse_args()
    if not 1<=args.count<=1000:raise ValueError('Invalid case count')
    source_path=ROOT/'data/human_references/kusano/original.cpp'
    provenance=read_json(source_path.with_name('provenance.json'))
    assert hashlib.sha256(source_path.read_bytes()).hexdigest()==provenance['sha256']
    source=source_path.read_text(encoding='utf-8')
    manifest,cases=prepare();cases=cases[:args.count]
    output=ROOT/'runs/human_kusano_1000';output.mkdir(parents=True,exist_ok=True)
    evaluator=CppEvaluator(output/'evaluation_cache',workers=6,seconds=4.8,container_timer=True)
    receipt={'source_sha256':digest(source),'original_byte_sha256':provenance['sha256'],
             'seeds_sha256':manifest['seeds_sha256'],'image':evaluator.image,
             'compiler_flags':'-std=c++17 -O3 -DNDEBUG','workers':6,
             'candidate_wall_limit_seconds':5,'infrastructure_wall_limit_seconds':30,
             'internal_search_seconds':4.8,'source_modified':False,
             'argv_note':'Harness passes 4.8; original int main() ignores arguments; its limit=4.8 is unchanged.'}
    if (output/'receipt.json').exists() and read_json(output/'receipt.json')!=receipt:raise ValueError('Receipt changed')
    save_json(output/'receipt.json',receipt)
    if (output/'report.json').exists():print('Already complete');return
    rows=[]
    for start in range(0,len(cases),30):
        rows.extend(evaluator.evaluate(source,cases[start:start+30])['cases'])
        progress={'completed':len(rows),'mean_score':statistics.mean(r['score'] for r in rows),
                  'status_counts':dict(Counter(r['status'] for r in rows))}
        save_json(output/'progress.json',progress);print(progress,flush=True)
    save_json(output/f'raw_{len(rows)}.json',rows)
    if args.count!=1000:return
    # Re-score legal outputs only; timeout/invalid cases remain zero, never omitted from mean.
    folder=output/'rust_check';folder.mkdir(exist_ok=True)
    for i,row in enumerate(rows):
        if row['status']=='AC':
            (folder/f'{i:04d}.in').write_text(cases[i].text,encoding='utf-8')
            (folder/f'{i:04d}.out').write_text(row['stdout'],encoding='utf-8')
    docker(['run','--rm','--network=none','--mount',f"type=bind,source={REFERENCE/'tools'},target=/tools,readonly",
            '--mount',f'type=bind,source={folder},target=/work','--mount',f"type=bind,source={ROOT/'scripts'},target=/scripts,readonly",
            '--workdir=/work','python:3.11-slim','python','/scripts/rust_score_batch.py'])
    official={int(r['file'].split('.')[0]):r['score'] for r in read_json(folder/'rust_scores.json')}
    assert len(official)==sum(r['status']=='AC' for r in rows)
    mismatches=[i for i,r in enumerate(rows) if r['status']=='AC' and official[i]!=r['score']]
    scores=[r['score'] for r in rows]
    minimum=min(range(len(rows)),key=lambda i:scores[i]);maximum=max(range(len(rows)),key=lambda i:scores[i])
    report={**receipt,'author':'kusano','count':len(rows),'mean_score':statistics.mean(scores),
            'total_score':sum(scores),'min_score':scores[minimum],'min_case':minimum,
            'max_score':scores[maximum],'max_case':maximum,
            'status_counts':dict(Counter(r['status'] for r in rows)),
            'max_candidate_seconds':max(r['seconds'] for r in rows),
            'max_container_seconds':max(r.get('container_wall_seconds',0) for r in rows),
            'rust_checked':len(official),'rust_mismatches':mismatches,'official_rank_evaluated':False,
            'reference_source':provenance['source'],'champion_status':provenance['champion_status']}
    save_json(output/'report.json',report)
    with (output/'per_case.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['case','seed','score','status','candidate_seconds','container_seconds','rust_score'])
        for i,r in enumerate(rows):writer.writerow([i,manifest['cases'][i]['seed'],r['score'],r['status'],r['seconds'],r.get('container_wall_seconds'),official.get(i)])
    print(report,flush=True)

if __name__=='__main__':main()
