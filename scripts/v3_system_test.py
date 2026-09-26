"""One terminal private-set evaluation of a frozen LLM-cold-start experiment."""
import argparse
import csv
import statistics
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import digest,read_json,save_json
from aad.v3_evaluator import CleanEvaluator
from system_test import ROOT,REFERENCE,prepare
from official_tools import docker

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',type=Path,default=ROOT/'runs/v3_pro_fromscratch')
    parser.add_argument('--output',type=Path,default=ROOT/'runs/v3_system_1000')
    parser.add_argument('--paired-baseline',type=Path,help='Completed matched AAD/kusano run whose human results should be reused')
    args=parser.parse_args();run=args.run_dir.resolve();out=args.output.resolve()
    state=read_json(run/'state.json')
    if not state.get('frozen'):raise ValueError('Freeze the experiment before opening private cases')
    source=(run/'best.cpp').read_text(encoding='utf-8')
    if digest(source)!=state['champion']:raise ValueError('Frozen source mismatch')
    if (out/'report.json').exists():print('Already complete:',out/'report.json');return
    manifest,cases=prepare()
    baseline=read_json(ROOT/'runs/human_kusano_1000/report.json')
    baseline_dir=ROOT/'runs/human_kusano_1000'
    if args.paired_baseline:
        paired=args.paired_baseline.resolve()
        combined=read_json(paired/'report.json')
        verified=read_json(paired/'official_data/verified.json')
        baseline={**combined['solutions']['kusano'],'image':combined['image'],
                  'seeds_sha256':verified['seeds_sha256'],
                  'candidate_wall_limit_seconds':combined['hard_limit_seconds']}
        baseline_dir=paired/'kusano'
    evaluator=CleanEvaluator(out/'evaluations',workers=6,seconds=4.8)
    if baseline['image']!=evaluator.image or baseline['seeds_sha256']!=manifest['seeds_sha256']:
        raise ValueError('Human baseline image or seed set differs')
    human_rows=read_json(baseline_dir/'raw_1000.json')
    if len(human_rows)!=1000 or any(h['input_sha256']!=c.fingerprint for h,c in zip(human_rows,cases)):
        raise ValueError('Human baseline input hashes differ')
    if sum(h['score'] for h in human_rows)!=baseline['total_score']:
        raise ValueError('Human baseline score total differs')
    receipt={'source_sha256':digest(source),'seeds_sha256':manifest['seeds_sha256'],
             'cpp_image':evaluator.image,'search_seconds':4.8,'candidate_limit_seconds':4.95,
             'infrastructure_limit_seconds':30,'workers':6,
             'baseline_mean_score':baseline['mean_score'],'origin':'LLM-only source from problem-only cold start'}
    out.mkdir(parents=True,exist_ok=True)
    if (out/'receipt.json').exists() and read_json(out/'receipt.json')!=receipt:raise ValueError('Receipt mismatch')
    save_json(out/'receipt.json',receipt)
    rows=[]
    for start in range(0,1000,30):
        rows.extend(evaluator.evaluate(source,cases[start:start+30])['cases'])
        progress={'completed':len(rows),'mean_score':statistics.mean(r['score'] for r in rows),
                  'status_counts':dict(Counter(r['status'] for r in rows))}
        save_json(out/'progress.json',progress);print(progress,flush=True)
    save_json(out/'raw_1000.json',rows)
    folder=out/'rust_check';folder.mkdir(exist_ok=True)
    for i,row in enumerate(rows):
        if row['status']=='AC':
            (folder/f'{i:04d}.in').write_text(cases[i].text,encoding='utf-8')
            (folder/f'{i:04d}.out').write_text(row['stdout'],encoding='utf-8')
    docker(['run','--rm','--network=none','--mount',f"type=bind,source={REFERENCE/'tools'},target=/tools,readonly",
            '--mount',f'type=bind,source={folder},target=/work','--mount',f"type=bind,source={ROOT/'scripts'},target=/scripts,readonly",
            '--workdir=/work','python:3.11-slim','python','/scripts/rust_score_batch.py'])
    official={int(r['file'].split('.')[0]):r['score'] for r in read_json(folder/'rust_scores.json')}
    mismatches=[i for i,r in enumerate(rows) if r['status']=='AC' and official.get(i)!=r['score']]
    scores=[r['score'] for r in rows];mean=statistics.mean(scores)
    differences=[r['score']-h['score'] for r,h in zip(rows,human_rows)]
    report={**receipt,'count':1000,'mean_score':mean,'total_score':sum(scores),
            'min_score':min(scores),'min_case':scores.index(min(scores)),
            'max_score':max(scores),'max_case':scores.index(max(scores)),
            'status_counts':dict(Counter(r['status'] for r in rows)),
            'max_candidate_seconds':max(r['seconds'] for r in rows),
            'rust_mismatches':mismatches,'rust_checked':len(official),
            'exceeds_kusano':mean>baseline['mean_score'] and all(r['status']=='AC' for r in rows) and not mismatches,
            'mean_gain_over_kusano':mean-baseline['mean_score'],
            'paired_wins':sum(d>0 for d in differences),'paired_ties':sum(d==0 for d in differences),
            'paired_losses':sum(d<0 for d in differences),
            'same_input_hashes_and_docker_image':True,'equal_internal_search_seconds':4.8,
            'human_hard_limit_seconds':baseline.get('candidate_wall_limit_seconds',5.0),'single_run_per_solver':True,
            'human_baseline_directory':str(baseline_dir),
            'official_rank_evaluated':False}
    save_json(out/'report.json',report)
    with (out/'per_case.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['case','seed','score','status','candidate_seconds','rust_score'])
        for i,row in enumerate(rows):writer.writerow([i,manifest['cases'][i]['seed'],row['score'],row['status'],row['seconds'],official.get(i)])
    print(report,flush=True)

if __name__=='__main__':main()
