"""Freeze the current training leader, regenerate official inputs, fresh paired terminal test."""
import csv
import hashlib
import io
import statistics
import sys
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import digest,read_json,save_json
from aad.problem import Case
from aad.v3_evaluator import CleanEvaluator
from official_tools import docker,RUST_IMAGE
from system_test import ROOT,REFERENCE,SEEDS

OUT=ROOT/'runs/v3_kusano_private_20260922'
RUN=ROOT/'runs/v3_pro_fromscratch'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'report.json').exists():
        print('Already complete:',OUT/'report.json');return
    state=read_json(RUN/'state.json')
    best=max((k for k,r in state['records'].items() if r.get('full',{}).get('valid')),
             key=lambda k:state['records'][k]['full']['mean'])
    source=(RUN/'candidates'/best/'solution.cpp').read_text(encoding='utf-8')
    assert digest(source)==best
    if state.get('frozen'):
        assert state['champion']==best
    else:
        # User explicitly requested the current best, not validation-based reselection.
        state.update(frozen=True,champion=best,selection_policy='user-requested-current-training-mean-leader',
                     terminal_evaluation=str(OUT),validation_skipped_by_selection_policy=True)
        (RUN/'best.cpp').write_text(source,encoding='utf-8')
        save_json(RUN/'state.json',state)
    human_path=ROOT/'data/human_references/kusano/original.cpp'
    provenance=read_json(human_path.with_name('provenance.json'))
    assert hashlib.sha256(human_path.read_bytes()).hexdigest()==provenance['sha256']
    codes={'aad':source,'kusano':human_path.read_text(encoding='utf-8')}
    evaluators={name:CleanEvaluator(OUT/name/'evaluations',workers=6,seconds=4.8) for name in codes}
    assert len({e.image for e in evaluators.values()})==1
    receipt={'sources':{k:digest(v) for k,v in codes.items()},'image':evaluators['aad'].image,
             'search_seconds':4.8,'hard_limit_seconds':4.95,'workers':6,'cpu_per_case':1,
             'memory_mib':512,'compiler_flags':'-std=c++17 -O3 -DNDEBUG',
             'selection_policy':state['selection_policy'],'human_source_modified':False,
             'fresh_cache_root':str(OUT),'single_run_per_case':True}
    if (OUT/'receipt.json').exists():assert read_json(OUT/'receipt.json')==receipt
    save_json(OUT/'receipt.json',receipt)
    data=OUT/'official_data';data.mkdir(exist_ok=True)
    if not (data/'verified.json').exists():
        url='https://img.atcoder.jp/ahc001/seeds.zip'
        with urllib.request.urlopen(url,timeout=60) as response:archive=response.read(1_000_000)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            member=next(n for n in z.namelist() if Path(n).name=='seeds.txt')
            raw=z.read(member)
        assert raw==SEEDS.read_bytes()
        assert hashlib.md5(raw).hexdigest()=='8fc1ce3f4beabac6abc1bdb4206d7f7e'
        seeds=list(map(int,raw.decode().split()))
        assert len(seeds)==len(set(seeds))==1000
        assert seeds==read_json(REFERENCE/'data.json')['seeds']['private']
        (data/'seeds.zip').write_bytes(archive);(data/'seeds.txt').write_bytes(raw)
        docker(['run','--rm','--network=none','--mount',f"type=bind,source={REFERENCE/'tools'},target=/tools,readonly",
                '--mount',f'type=bind,source={data},target=/work',RUST_IMAGE,
                '/tools/target/release/gen','/work/seeds.txt','--dir','/work/inputs'])
        old=read_json(ROOT/'data/official_system_1000/manifest.json')
        records=[]
        for i,seed in enumerate(seeds):
            path=data/'inputs'/f'{i:04d}.txt';sha=digest(path.read_text(encoding='utf-8'))
            assert sha==old['cases'][i]['sha256'] and seed==old['cases'][i]['seed']
            records.append({'file':f'inputs/{i:04d}.txt','seed':seed,'sha256':sha})
        save_json(data/'verified.json',{'seed_url':url,'seed_md5':hashlib.md5(raw).hexdigest(),
            'seeds_sha256':hashlib.sha256(raw).hexdigest(),'ale_private_exact_order_match':True,
            'fresh_original_rust_generation':True,'matches_previous_1000_inputs':True,
            'generator_sha256':hashlib.sha256((REFERENCE/'tools/target/release/gen').read_bytes()).hexdigest(),
            'scorer_sha256':hashlib.sha256((REFERENCE/'tools/target/release/vis').read_bytes()).hexdigest(),
            'cases':records})
    manifest=read_json(data/'verified.json')
    cases=[]
    for record in manifest['cases']:
        text=(data/record['file']).read_text(encoding='utf-8');assert digest(text)==record['sha256']
        cases.append(Case(record['file'],text))
    assert len(cases)==1000
    print('Verified official seeds; fresh Rust-generated inputs match all 1000 prior hashes.',flush=True)
    rows={k:[] for k in codes}
    # Alternate first solver per batch, with only six candidate processes at a time.
    for start in range(0,1000,30):
        order=list(codes) if (start//30)%2==0 else list(reversed(codes))
        for name in order:
            rows[name].extend(evaluators[name].evaluate(codes[name],cases[start:start+30])['cases'])
            progress={k:{'completed':len(v),'mean_score':statistics.mean(r['score'] for r in v) if v else None,
                         'statuses':dict(Counter(r['status'] for r in v))} for k,v in rows.items()}
            save_json(OUT/'progress.json',progress);print(progress,flush=True)
    reports={}
    for name,values in rows.items():
        folder=OUT/name;save_json(folder/'raw_1000.json',values)
        rust=folder/'rust_check';rust.mkdir(exist_ok=True)
        for i,row in enumerate(values):
            if row['status']=='AC':
                (rust/f'{i:04d}.in').write_text(cases[i].text,encoding='utf-8')
                (rust/f'{i:04d}.out').write_text(row['stdout'],encoding='utf-8')
        docker(['run','--rm','--network=none','--mount',f"type=bind,source={REFERENCE/'tools'},target=/tools,readonly",
            '--mount',f'type=bind,source={rust},target=/work','--mount',f"type=bind,source={ROOT/'scripts'},target=/scripts,readonly",
            '--workdir=/work','python:3.11-slim','python','/scripts/rust_score_batch.py'])
        official={int(r['file'].split('.')[0]):r['score'] for r in read_json(rust/'rust_scores.json')}
        mismatch=[i for i,r in enumerate(values) if r['status']=='AC' and official.get(i)!=r['score']]
        assert len(official)==sum(r['status']=='AC' for r in values)
        scores=[r['score'] for r in values]
        reports[name]={'source_sha256':digest(codes[name]),'count':1000,'mean_score':statistics.mean(scores),
            'total_score':sum(scores),'min_score':min(scores),'min_case':scores.index(min(scores)),
            'max_score':max(scores),'max_case':scores.index(max(scores)),
            'status_counts':dict(Counter(r['status'] for r in values)),
            'max_seconds':max(r['seconds'] for r in values),'rust_checked':len(official),'rust_mismatches':mismatch}
        save_json(folder/'report.json',reports[name])
    differences=[a['score']-b['score'] for a,b in zip(rows['aad'],rows['kusano'])]
    report={**receipt,'dataset_verified':True,'solutions':reports,
            'aad_mean_minus_kusano':statistics.mean(differences),'aad_wins':sum(d>0 for d in differences),
            'ties':sum(d==0 for d in differences),'kusano_wins':sum(d<0 for d in differences),
            'official_rank_evaluated':False,
            'exceeds_kusano':statistics.mean(differences)>0 and all(r['status_counts']=={'AC':1000} and not r['rust_mismatches'] for r in reports.values())}
    with (OUT/'per_case.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['case','official_seed','aad_score','kusano_score','difference','aad_status','kusano_status'])
        for i,(a,b) in enumerate(zip(rows['aad'],rows['kusano'])):
            writer.writerow([i,manifest['cases'][i]['seed'],a['score'],b['score'],differences[i],a['status'],b['status']])
    save_json(OUT/'report.json',report);print(report,flush=True)

if __name__=='__main__':main()
