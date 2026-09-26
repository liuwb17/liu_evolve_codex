"""Fine-grained, multi-fidelity C++ evolution. No official system inputs are read."""
import argparse
import getpass
import json
import os
import random
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.cpp import CppEvaluator
from aad.problem import Case,synthetic_case
from aad.io import digest,read_json,save_json,tuple_tree
from aad.provider import ChatProposer,BudgetExhausted
from aad.engine import compact

ROOT=Path('runs/v2_deepseek')

def cases(offset,count):
    return [Case(f'dev_{i}',synthetic_case(i)) for i in range(offset,offset+count)]

def main():
    global ROOT
    p=argparse.ArgumentParser()
    p.add_argument('--ask-key',action='store_true')
    p.add_argument('--calls',type=int,default=12)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--finalists',type=int,default=3)
    p.add_argument('--run-dir',type=Path,default=ROOT)
    args=p.parse_args()
    ROOT=args.run_dir
    # Reject frozen or accidental overwrite before prompting or making API calls.
    existing=ROOT/'state.json'
    if existing.exists():
        if read_json(existing).get('frozen'):raise ValueError('Run frozen; use a new --run-dir')
        if not args.resume:raise ValueError('Run exists: use --resume')
    elif args.resume:
        raise ValueError('Cannot resume: state.json is missing')
    if args.ask_key:os.environ['AAD_API_KEY']=getpass.getpass('AAD API key (hidden): ')
    ROOT.mkdir(parents=True,exist_ok=True)
    config={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key_env':'AAD_API_KEY',
            'max_requests':args.calls,'max_output_tokens':8192,'request_timeout':180,
            'request_options':{'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}},
            'language':'cpp','timeout':5.0}
    save_json(ROOT/'config.json',config)
    proposer=ChatProposer(config,ROOT)
    print('Models:',proposer.check_connection(),flush=True)
    rng=random.Random(970)
    state_path=ROOT/'state.json'
    train=cases(40000,16);validation=cases(60000,24)
    # Only new developer-chosen seeds, never the system set or old official feedback.
    for split,data in [('train',train),('validation',validation)]:
        folder=ROOT/'inputs'/split;folder.mkdir(parents=True,exist_ok=True)
        for case in data:(folder/(case.name+'.txt')).write_text(case.text,encoding='utf-8')
    short=CppEvaluator(Path('runs/cpp_evaluations'),workers=4,seconds=0.5)
    full=CppEvaluator(Path('runs/cpp_evaluations'),workers=4,seconds=4.25)
    def save_code(code,record):
        ident=digest(code);folder=ROOT/'candidates'/ident;folder.mkdir(parents=True,exist_ok=True)
        (folder/'solution.cpp').write_text(code,encoding='utf-8');save_json(folder/'record.json',record)
        state['records'][ident]=record
        return ident
    def source(ident):
        text=(ROOT/'candidates'/ident/'solution.cpp').read_text(encoding='utf-8')
        if digest(text)!=ident:raise ValueError('Source hash mismatch')
        return text
    def checkpoint():
        state['rng']=rng.getstate();save_json(state_path,state)
    if state_path.exists():
        if not args.resume:raise ValueError('Run exists: use --resume')
        state=read_json(state_path)
        if state.get('frozen'):raise ValueError('Run frozen')
        rng.setstate(tuple_tree(state['rng']))
    else:
        state={'records':{},'events':[],'frozen':False,'protocol':'v2-fidelity0.5-4.25-fixed-splits',
               'train_offset':40000,'validation_offset':60000,'holdout_offset':80000,'holdout_count':80}
        seed=Path('aad/assets/seed_v2.cpp').read_text(encoding='utf-8')
        result=short.evaluate(seed,train)
        if not result['valid']:raise RuntimeError('Seed invalid: '+str(compact(result)))
        state['baseline']=save_code(seed,{'origin':'human-engineered-seed','parents':[],'train':result})
        checkpoint()
        print('Seed 0.5s mean:',result['mean'],flush=True)
    ops=['proposal','conflict','schedule','search','parameters','rewrite','crossover']
    while proposer.requests<args.calls:
        pool=sorted([k for k,v in state['records'].items() if v.get('train',{}).get('valid')],
                    key=lambda k:state['records'][k]['train']['mean'],reverse=True)[:8]
        parent=pool[0] if rng.random()<0.65 else rng.choice(pool)
        donor=rng.choice(pool)
        op=ops[len(state['events'])%len(ops)]
        parent_code=source(parent)
        feedback={'parent_train':compact(state['records'][parent]['train']),
                  'recent_events':state['events'][-6:],
                  'requirements':'Target >=0.970 on unseen cases. quality() is exact and immutable. Preserve the seconds argument and check elapsed time often. Proposals must maintain anchors. Improve coupled geometry, do not merely increase runtime. Replace only necessary named blocks. Score comparisons use the same 0.5 second budget per case; finalists get 4.25 seconds. Small cases can saturate while dense cases need faster or coordinated moves.',
                  'operator_focus':op}
        # Two trials per idea, with actual failed source and compiler/runtime feedback.
        for attempt in range(2):
            event={'operator':op,'parent':parent,'attempt':attempt,'call':proposer.requests}
            try:proposal=proposer.propose(parent_code,source(donor),op,feedback,rng)
            except BudgetExhausted:break
            except (ValueError,RuntimeError,SyntaxError) as error:
                event.update(status='proposal_error',error=str(error)[:2000]);state['events'].append(event)
                feedback['repair']=str(error);checkpoint();continue
            code=proposal.pop('code');ident=digest(code)
            event.update(candidate=ident,hypothesis=proposal['hypothesis'])
            if ident in state['records']:
                event['status']='duplicate';state['events'].append(event);checkpoint();break
            smoke=short.evaluate(code,train[:4])
            if smoke['valid']:
                parent_smoke=short.evaluate(parent_code,train[:4])
                if smoke['mean']<parent_smoke['mean']-0.012:
                    save_code(code,{'origin':'deepseek','parents':[parent,donor],'proposal':proposal,'smoke':smoke})
                    event.update(status='screened',mean=smoke['mean']);state['events'].append(event);checkpoint();break
                result=short.evaluate(code,train)
            else:result=smoke
            record={'origin':'deepseek','parents':[parent,donor],'proposal':proposal,'train':result}
            save_code(code,record)
            event.update(status='valid' if result['valid'] else 'invalid',mean=result['mean'])
            state['events'].append(event);checkpoint()
            print(f"call={proposer.requests} op={op} status={event['status']} train={result['mean']:.6f}",flush=True)
            if result['valid']:break
            feedback['repair']=compact(result);feedback['failed_source']=code
            parent_code=code
    pool=sorted([k for k,v in state['records'].items() if v.get('train',{}).get('valid')],
                key=lambda k:state['records'][k]['train']['mean'],reverse=True)[:args.finalists]
    for ident in pool:
        if 'validation' not in state['records'][ident]:
            result=full.evaluate(source(ident),validation)
            state['records'][ident]['validation']=result;checkpoint()
        print('Full-budget validation',ident[:12],state['records'][ident]['validation']['mean'],flush=True)
    valid=[k for k in pool if state['records'][k]['validation']['valid']]
    if not valid:raise RuntimeError('No valid full-budget finalist')
    champion=max(valid,key=lambda k:state['records'][k]['validation']['mean'])
    state.update(champion=champion,frozen=True)
    (ROOT/'best.cpp').write_text(source(champion),encoding='utf-8');checkpoint()
    # Holdout is generated only after selecting and freezing the candidate.
    holdout=cases(80000,80)
    folder=ROOT/'inputs'/'holdout';folder.mkdir(exist_ok=True)
    for case in holdout:(folder/(case.name+'.txt')).write_text(case.text,encoding='utf-8')
    result=full.evaluate(source(champion),holdout)
    report={'source_sha256':champion,'origin':state['records'][champion]['origin'],
            'model_requests':proposer.requests,'validation':compact(state['records'][champion]['validation']),
            'holdout':result,'target':0.970,'target_met_on_development_holdout':result['valid'] and result['mean']>=.970,
            'official_system_evaluated':False}
    save_json(ROOT/'report.json',report)
    print('Frozen champion holdout:',result['mean'],'valid:',result['valid'],flush=True)

if __name__=='__main__':main()
