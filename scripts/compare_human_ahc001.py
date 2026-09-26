"""Auditable paired comparison; does not execute or tune either solver."""
import csv
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json,save_json

ROOT=Path(__file__).resolve().parents[1]
human_dir=ROOT/'runs/human_kusano_1000'
aad_dir=ROOT/'runs/v2_system_1000_runtime5'
human=read_json(human_dir/'raw_1000.json')
aad=read_json(aad_dir/'champion/local.json')['cases']
hr=read_json(human_dir/'report.json');ar=read_json(aad_dir/'report.json')
assert len(human)==len(aad)==1000
assert not hr['rust_mismatches'] and not ar['rust_mismatches']
assert hr['seeds_sha256']==ar['seeds_sha256']
assert hr['image']==ar['cpp_image']
assert all(h['input_sha256']==a['input_sha256'] for h,a in zip(human,aad))
assert sum(r['score'] for r in human)==hr['total_score']
assert sum(r['score'] for r in aad)==ar['total_score']
differences=[h['score']-a['score'] for h,a in zip(human,aad)]
result={'count':1000,'human_mean':hr['mean_score'],'aad_mean':ar['mean_score'],
        'human_minus_aad_mean':sum(differences)/1000,
        'human_wins':sum(d>0 for d in differences),'ties':sum(d==0 for d in differences),
        'aad_wins':sum(d<0 for d in differences),
        'same_input_hashes_and_docker_image':True,
        'same_hard_candidate_wall_limit_seconds':5,
        'human_internal_search_seconds':4.8,'aad_internal_search_seconds':3.5,
        'equal_search_budget':False,'single_run_per_solver':True,
        'official_rank_comparison':False}
save_json(human_dir/'comparison.json',result)
with (human_dir/'paired.csv').open('w',encoding='utf-8',newline='') as stream:
    writer=csv.writer(stream);writer.writerow(['case','kusano_score','aad_score','kusano_minus_aad'])
    for i,(h,a,d) in enumerate(zip(human,aad,differences)):writer.writerow([i,h['score'],a['score'],d])
print(result)
