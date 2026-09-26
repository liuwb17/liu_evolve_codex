"""Cold-start LLM population, specialist archive, multi-fidelity evolution.

Only official PUBLIC inputs are loaded. No existing solution or private data is read.
"""
import argparse
import getpass
import math
import os
import random
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.engine import compact
from aad.io import digest,read_json,save_json,tuple_tree
from aad.problem import Case
from aad.v3_evaluator import CleanEvaluator,SanitizerEvaluator
from aad.v3_provider import ProgramProposer,PROBLEM
from aad.v3_diagnostics import layout_diagnostics

PROJECT=Path(__file__).resolve().parents[1]

def public_cases():
    folder=PROJECT/'data/official_public'
    manifest=read_json(folder/'manifest.json')
    records=sorted([r for part in manifest['splits'].values() for r in part],key=lambda r:r['file'])
    assert len(records)==50
    cases=[]
    for record in records:
        code=(folder/record['file']).read_text(encoding='utf-8')
        assert digest(code)==record['sha256']
        cases.append(Case('public_'+Path(record['file']).stem,code))
    return cases,manifest

def objective(result):
    if not result.get('valid'):return -1.
    values=sorted(r['score']/1e9 for r in result['cases'])
    tail=sum(values[:max(1,len(values)//4)])/max(1,len(values)//4)
    return .9*result['mean']+.1*tail

def restart_due(state,patience):
    """A restart receives no lineage sources; only completed events drive stagnation."""
    if patience<=0:return False
    best=-1.;last_progress=-1;last_restart=-1
    for i,event in enumerate(state['events']):
        if event['operator'] in ('independent_initial_design','independent_restart'):
            last_restart=i
        record=state['records'].get(event.get('candidate'),{})
        if record.get('full',{}).get('valid'):
            value=objective(record['full'])
            if value>best+1e-9:best=value;last_progress=i
    return len(state['events'])-1-max(last_progress,last_restart)>=patience

def nondominated_ids(records):
    vectors={k:{c['name']:c['score'] for c in r['full']['cases']}
             for k,r in records.items() if r.get('full',{}).get('valid')}
    if vectors:
        names=set(next(iter(vectors.values())))
        if any(set(v)!=names for v in vectors.values()):raise ValueError('Non-matching training cases')
    return {k for k,v in vectors.items() if not any(
        all(other[n]>=value for n,value in v.items()) and any(other[n]>value for n,value in v.items())
        for j,other in vectors.items() if j!=k)}

def select_donor(records,pool,parent,mechanism=False):
    if mechanism:
        eligible=[k for k,r in records.items() if k!=parent and r.get('full',{}).get('valid')
                  and r['operator'] in ('structural_redesign','novel_design','independent_restart')
                  and r['full']['mean']>=records[parent]['full']['mean']-.04]
        if eligible:return max(eligible,key=lambda k:records[k]['call_id']),'recent-structural-mechanism'
    vector={r['name']:r['score'] for r in records[parent]['full']['cases']}
    donor=max(pool,key=lambda k:sum(max(0,r['score']-vector[r['name']]) for r in records[k]['full']['cases']))
    return donor,'case-complementary'

def feedback_for(record,train):
    result=record['full']
    case_map={c.name:c for c in train}
    examples=[]
    for row in sorted(result['cases'],key=lambda r:r['score'])[:2]:
        case=case_map[row['name']]
        assert case.fingerprint==row['input_sha256']
        examples.append({'name':case.name,'input':case.text,'candidate_output':row.get('stdout',''),
                         'score':row['score'],'coverage':row.get('coverage'),
                         'weak_fraction':row.get('weak_fraction'),'mean_aspect':row.get('mean_aspect'),
                         'measured_geometry':layout_diagnostics(case.text,row['stdout'])})
    return {'short':compact(record.get('short')),'full':compact(result),
            'allocated_full_search_seconds':4.8,
            'mean_measured_process_seconds':sum(r['seconds'] for r in result['cases'])/len(result['cases']),
            'public_training_features':[{'name':c.name,'n':int(c.text.split()[0])} for c in train],
            'worst_public_training_examples':examples,
            'diagnostic_instruction':'Use the measured time and examples to diagnose the algorithm, not to hardcode cases. If runtime is far below the allocated budget, check whether your search stops unnecessarily early. Infer algorithmic improvements yourself from your code and its actual layouts.'}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,default=PROJECT/'runs/v3_fromscratch')
    p.add_argument('--calls',type=int,default=64)
    p.add_argument('--initial',type=int,default=8)
    p.add_argument('--model',default='deepseek-flash')
    p.add_argument('--thinking',choices=['enabled','disabled'],default='disabled')
    p.add_argument('--effort',choices=['low','high','max'],default='high')
    p.add_argument('--exploration-effort',choices=['low','high','max'],help='Optional reasoning override for independent and structural designs')
    p.add_argument('--max-tokens',type=int,default=16384)
    p.add_argument('--full-evaluation',action='store_true',help='Evaluate every short-valid candidate on all 40 training cases; backfill previous screened candidates')
    p.add_argument('--restart-patience',type=int,default=0,help='Completed proposals without improvement before a problem-only independent restart; 0 disables')
    p.add_argument('--ask-key',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--finalize',action='store_true')
    args=p.parse_args();root=args.run_dir.resolve();state_path=root/'state.json'
    if state_path.exists():
        if not args.resume:raise ValueError('Existing run: use --resume')
        state=read_json(state_path)
        if state.get('frozen'):raise ValueError('Frozen run cannot evolve')
        if state.get('validation_started') and not args.finalize:
            raise ValueError('Validation has started; development is closed. Only --finalize may resume.')
    else:
        if args.resume:raise ValueError('No state to resume')
        state={'records':{},'events':[],'initial_issued':0,'frozen':False,'repair':None,
               'protocol':'llm-cold-start-public50-v3','origin':'problem-only-LLM',
               'initial_target':args.initial,'model':args.model}
    if args.initial!=state['initial_target'] or args.model!=state['model']:raise ValueError('Protocol changed')
    if args.ask_key:os.environ['AAD_API_KEY']=getpass.getpass('AAD API key (hidden): ')
    root.mkdir(parents=True,exist_ok=True)
    all_cases,manifest=public_cases()
    train=[c for i,c in enumerate(all_cases) if i%5!=4]
    validation=[c for i,c in enumerate(all_cases) if i%5==4]
    ordered=sorted(train,key=lambda c:int(c.text.split()[0]))
    screen=[ordered[round(i*(len(ordered)-1)/11)] for i in range(12)]
    smoke=screen[::2]
    split={'train':[c.name for c in train],'validation':[c.name for c in validation],
           'screen':[c.name for c in screen], 'inputs':{c.name:c.fingerprint for c in all_cases},
           'problem_sha256':digest(PROBLEM),'initial_target':args.initial,'model':args.model,
           'short_seconds':.6,'full_seconds':4.8,'hard_seconds':5.0,
           'private_inputs_used_in_evolution':False,'existing_solution_sources_supplied':False}
    signature=digest(str(split))
    if 'signature' in state and state['signature']!=signature:raise ValueError('Inputs or protocol changed')
    state['signature']=signature;save_json(root/'protocol.json',split)
    for part,cases in [('train',train),('validation',validation)]:
        folder=root/'inputs'/part;folder.mkdir(parents=True,exist_ok=True)
        for case in cases:(folder/(case.name+'.txt')).write_text(case.text,encoding='utf-8')
    config={'base_url':'https://api.deepseek.com','model':args.model,'api_key_env':'AAD_API_KEY',
            'max_requests':args.calls,'max_output_tokens':args.max_tokens,'request_timeout':600,
            'request_options':{'thinking':{'type':args.thinking},'response_format':{'type':'json_object'},'temperature':1.0}}
    if args.thinking=='enabled':config['request_options']['reasoning_effort']=args.effort
    if args.exploration_effort:config['exploration_effort']=args.exploration_effort
    if (root/'config.json').exists():
        previous=read_json(root/'config.json')
        if previous!=config:
            state.setdefault('generation_config_changes',[]).append({'after_events':len(state['events']),'previous':previous,'next':config})
    save_json(root/'config.json',config)
    proposer=ProgramProposer(config,root);print('Available models:',proposer.check_connection(),flush=True)
    short=CleanEvaluator(root/'evaluations',workers=6,seconds=.6)
    full=CleanEvaluator(root/'evaluations',workers=6,seconds=4.8)
    debug=None
    rng=random.Random(990001)
    if 'rng' in state:rng.setstate(tuple_tree(state['rng']))
    def checkpoint():
        state['rng']=rng.getstate();save_json(state_path,state)
    def source(ident):
        code=(root/'candidates'/ident/'solution.cpp').read_text(encoding='utf-8')
        assert digest(code)==ident
        return code
    def archive():
        full_ids=[k for k,r in state['records'].items() if r.get('full',{}).get('valid')]
        ranked=sorted(full_ids,key=lambda k:objective(state['records'][k]['full']),reverse=True)
        selected=ranked[:3]
        # Specialists in four point-count strata survive even when their mean is lower.
        for q in range(4):
            names={c.name for c in ordered[q*10:(q+1)*10]}
            if ranked:
                best=max(ranked,key=lambda k:sum(r['score'] for r in state['records'][k]['full']['cases'] if r['name'] in names))
                if best not in selected:selected.append(best)
        # Retain one strongest member per model-described algorithm family.
        families=set(state['records'][k]['family'] for k in selected)
        frontier=nondominated_ids(state['records'])
        for k in ranked:
            if k in frontier and state['records'][k]['family'] not in families:
                selected.append(k);families.add(state['records'][k]['family'])
            if len(selected)>=10:break
        return ranked,selected
    policy='all-valid-full40' if args.full_evaluation else 'multi-fidelity'
    previous_policy=state.get('evaluation_policy','multi-fidelity')
    if policy!=previous_policy:
        state.setdefault('evaluation_policy_changes',[]).append({
            'after_events':len(state['events']),'previous':previous_policy,'next':policy,
            'reason':'Avoid short-budget false negatives when model generation dominates evaluation cost'})
    state['evaluation_policy']=policy
    if state.get('restart_patience',0)!=args.restart_patience:
        state.setdefault('restart_policy_changes',[]).append({
            'after_events':len(state['events']),'previous':state.get('restart_patience',0),
            'next':args.restart_patience})
    state['restart_patience']=args.restart_patience
    if state.get('donor_policy')!='alternate-behavior-and-mechanism-v1':
        state['donor_policy']='alternate-behavior-and-mechanism-v1'
        state['donor_policy_enabled_after_events']=len(state['events'])
    if state.get('geometry_feedback')!='fixed-layout-edge-constraints-v1':
        state['geometry_feedback']='fixed-layout-edge-constraints-v1'
        state['geometry_feedback_enabled_after_events']=len(state['events'])
    if state.get('archive_policy')!='top3-specialists-pareto-families-v1':
        state.setdefault('archive_policy_changes',[]).append({
            'after_events':len(state['events']),'next':'top3-specialists-pareto-families-v1'})
        state['archive_policy']='top3-specialists-pareto-families-v1'
    if state.get('runtime_diagnostics')!='asan-ubsan-v1':
        state['runtime_diagnostics']='asan-ubsan-v1'
        state['runtime_diagnostics_enabled_after_events']=len(state['events'])
    checkpoint()
    if args.full_evaluation and not state.get('validation_started'):
        for ident,record in state['records'].items():
            if record.get('short',{}).get('valid') and 'full' not in record:
                record['full']=full.evaluate(source(ident),train)
                state.setdefault('evaluation_backfills',[]).append({
                    'candidate':ident,'mean':record['full']['mean'],
                    'valid':record['full']['valid'],'after_events':len(state['events'])})
                save_json(root/'candidates'/ident/'record.json',record);checkpoint()
                print({'backfill':ident,'mean':record['full']['mean'],'valid':record['full']['valid']},flush=True)
    while proposer.requests<args.calls and not state.get('validation_started'):
        if (root/'STOP_AFTER_CURRENT').exists():
            print('Cooperative stop requested; no new API request started.',flush=True);break
        ranked,pool=archive()
        repair=state.get('repair')
        if repair:
            parents=repair['parents'];parent=repair['candidate'];operator='repair'
            task={'operation':operator,'parent_source':source(parent),
                  'diagnostic':repair['diagnostic'],'instruction':'Repair the demonstrated failure with minimal changes. Preserve algorithmic intent and runtime-budget argument.'}
        elif state['initial_issued']<args.initial or not pool:
            parents=[];operator='independent_initial_design';state['initial_issued']+=1
            task={'operation':operator,'independent_design_index':state['initial_issued'],
                  'instruction':'Invent a complete high-performance algorithm from the specification alone. No seed code is available. Aim at mean satisfaction above 0.992 with robust validity. Consider genuinely different designs, not trivial feasible output. Explain your design and expected bottleneck in hypothesis.'}
        elif restart_due(state,args.restart_patience):
            parents=[];operator='independent_restart'
            task={'operation':operator,
                  'instruction':'Independently invent a high-performance complete algorithm from the specification alone. No parent code, prior designs, or reference solutions are supplied. Aim at mean satisfaction above 0.992 within the runtime budget. Explain the general optimization mechanism and its limitations in hypothesis.'}
        else:
            operators=['targeted_improvement','structural_redesign','bottleneck_optimization','complementary_crossover','robustness','novel_design']
            operator=operators[len(state['events'])%len(operators)]
            parent=ranked[0] if rng.random()<.55 else rng.choice(pool)
            a=state['records'][parent]
            cross_count=sum(e['operator']=='complementary_crossover' for e in state['events'])
            donor,donor_reason=select_donor(state['records'],pool,parent,
                mechanism=operator=='complementary_crossover' and cross_count%2==0)
            parents=[parent] if donor==parent or operator!='complementary_crossover' else [parent,donor]
            directions={
                'targeted_improvement':'Diagnose the parent\'s largest optimization weakness. Make one focused algorithmic change; preserve unrelated working code.',
                'structural_redesign':'Escape limitations of the current algorithm family. Introduce a substantially different construction, search representation, or optimization organization of your own design.',
                'bottleneck_optimization':'Improve useful search per unit time. Find dominant costs from source and failure profiles; reduce them without replacing the true objective with a misleading proxy.',
                'complementary_crossover':'The donor selection reason is supplied. A mechanism donor may score lower overall but offers a recently explored design. Transfer useful mechanisms while preserving strong parent components; do not simply replace the parent with the weaker donor. Combine algorithms, not outputs or hardcoded case routing. Respect one shared runtime budget.',
                'robustness':'Improve the lower-scoring public training instances without sacrificing the mean. Explain the general structural reason, not a case-specific exception.',
                'novel_design':'Challenge the current best design with a different algorithmic approach. Return a full implementation, with no dependency on files or external solvers.'}
            task={'operation':operator,'instruction':directions[operator],
                  'parent_source':source(parent),'parent_design':a['hypothesis'],
                  'feedback':feedback_for(a,train),
                  'recent_experiments':state['events'][-8:],
                  'target_mean':.992,'selection':'90% mean + 10% lowest-quartile mean at 4.8 seconds',
                  'donor_source':source(donor) if operator=='complementary_crossover' and donor!=parent else None}
            if operator=='complementary_crossover' and donor!=parent:
                task['donor_selection_reason']=donor_reason
                task['donor_design']=state['records'][donor]['hypothesis']
                task['donor_training_mean']=state['records'][donor]['full']['mean']
        checkpoint()
        event={'call_id':proposer.requests,'operator':operator,'parents':parents}
        if task.get('donor_selection_reason'):event['donor_selection_reason']=task['donor_selection_reason']
        try:proposal=proposer.generate(task)
        except (ValueError,RuntimeError) as error:
            event.update(status='request_error',error=str(error));state['events'].append(event);checkpoint()
            print(event,flush=True)
            if 'HTTP 401' in str(error) or 'HTTP 402' in str(error):raise
            continue
        code=proposal['code'];ident=digest(code);event['candidate']=ident
        if ident in state['records']:
            event['status']='duplicate';state['events'].append(event);state['repair']=None;checkpoint();continue
        folder=root/'candidates'/ident;folder.mkdir(parents=True,exist_ok=True)
        (folder/'solution.cpp').write_text(code,encoding='utf-8')
        record={k:v for k,v in proposal.items() if k!='code'}
        record.update(origin='LLM-generated-full-program',parents=parents,operator=operator)
        if task.get('donor_selection_reason'):record['donor_selection_reason']=task['donor_selection_reason']
        state['records'][ident]=record
        result=short.evaluate(code,smoke);record['smoke']=result;checkpoint()
        if result['valid']:
            result=short.evaluate(code,screen);record['short']=result;checkpoint()
        if result['valid']:
            if args.full_evaluation:
                result=full.evaluate(code,train);record['full']=result;checkpoint()
        if result['valid'] and not args.full_evaluation:
            best_short=max([r.get('short',{}).get('mean',0) for r in state['records'].values()],default=0)
            # Never discard cold starts solely on short-run score; periodically audit slow starters.
            promote=not pool or operator in ('independent_initial_design','structural_redesign','novel_design') or result['mean']>=best_short-.008 or proposer.requests%7==0
            if promote:
                probe=full.evaluate(code,screen);record['full_probe']=probe;checkpoint()
                threshold=state['records'][ranked[0]]['full']['mean']-.006 if ranked else 0
                if probe['valid'] and (probe['mean']>=threshold or operator=='independent_initial_design'):
                    result=full.evaluate(code,train);record['full']=result;checkpoint()
                else:result=probe
            else:event['status']='short_screened'
        if not result['valid']:
            if operator!='repair':
                diagnostic=compact(result)
                failures=[r for r in result['cases'] if r['status']!='AC']
                if failures and failures[0]['status']!='CE':
                    bad=failures[0]
                    case=next(c for c in train if c.name==bad['name'])
                    diagnostic['public_counterexample']={'input':case.text,'output':bad.get('stdout',''),
                        'status':bad['status'],'message':bad.get('message',''),
                        'returncode':bad.get('returncode'),'stderr':bad.get('stderr','')[-8000:]}
                    if bad['status']=='RE':
                        if debug is None:debug=SanitizerEvaluator(root/'diagnostics/asan-ubsan-v1',workers=1,seconds=4.8)
                        debug_result=debug.evaluate(code,[case])
                        record['runtime_diagnostic']=debug_result
                        row=debug_result['cases'][0]
                        diagnostic['sanitizer_replay']={
                            'diagnostic_only_not_a_score':True,'status':row['status'],
                            'returncode':row.get('returncode'),
                            'message':row.get('message',''),'stderr':row.get('stderr','')[-10000:]}
                state['repair']={'candidate':ident,'parents':[ident],'diagnostic':diagnostic}
            else:state['repair']=None
            event['status']='invalid'
        else:
            state['repair']=None
            event.setdefault('status','full_evaluated' if 'full' in record else 'probe_only')
        event.update(mean=result['mean'],family=record['family'],hypothesis=record['hypothesis'][:1200])
        state['events'].append(event);save_json(folder/'record.json',record);checkpoint()
        ranked,pool=archive();state['archive']=pool;checkpoint()
        print({'call':proposer.requests,'operator':operator,'status':event['status'],
               'candidate_mean':result['mean'],'best_full_train':state['records'][ranked[0]]['full']['mean'] if ranked else None},flush=True)
    ranked,pool=archive()
    if not ranked:raise RuntimeError('No fully evaluated valid candidate')
    state['development_best']=ranked[0];checkpoint()
    print('Development best:',state['records'][ranked[0]]['full']['mean'],flush=True)
    if not args.finalize:
        print('Not frozen; validation and private sets have not been evaluated. Resume or explicitly finalize.',flush=True);return
    state['validation_started']=True;checkpoint()
    finalists=ranked[:3]
    mean_leader=max(ranked,key=lambda k:state['records'][k]['full']['mean'])
    if mean_leader not in finalists:finalists.append(mean_leader)
    elif len(ranked)>3:finalists.append(ranked[3])
    for ident in finalists:
        if 'validation' not in state['records'][ident]:
            state['records'][ident]['validation']=full.evaluate(source(ident),validation);checkpoint()
    eligible=[k for k in finalists if state['records'][k]['validation']['valid']]
    if not eligible:raise RuntimeError('No valid validation candidate')
    champion=max(eligible,key=lambda k:(state['records'][k]['validation']['mean'],objective(state['records'][k]['validation'])))
    state.update(champion=champion,frozen=True)
    (root/'best.cpp').write_text(source(champion),encoding='utf-8');checkpoint()
    report={'source_sha256':champion,'origin':'LLM cold start, LLM-only source modifications',
            'requests':proposer.requests,'train':compact(state['records'][champion]['full']),
            'validation':compact(state['records'][champion]['validation']),
            'private_evaluated':False,'existing_solver_supplied':False}
    save_json(root/'report.json',report);print(report,flush=True)

if __name__=='__main__':main()
