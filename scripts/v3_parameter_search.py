"""LLM-exposed runtime parameters, public-only numeric evolution, audited LLM block installation."""
import argparse
import getpass
import json
import math
import os
import random
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.engine import compact
from aad.io import digest,read_json,save_json,tuple_tree
from aad.parameters import BEGIN,END,validate_schema,parameter_span,validate_block,assemble_parameters,decode,defaults_vector,differential_trial
from aad.v3_evaluator import CleanEvaluator
from aad.v3_provider import ProgramProposer
from v3_search import PROJECT,public_cases,objective

PARAMETER_INSTRUCTION='''Expose at most six influential numerical hyperparameters of this exact parent algorithm, without introducing new algorithmic mechanisms. Preserve the original defaults and behavior when optional arguments are absent. argv[1] remains the wall-clock budget. Optional argv[2], argv[3], ... override parameters in schema order. Every combination inside your declared bounds must be safe; normalize coupled fractions if necessary and retain all time/validity guards. Do not expose random seeds, input data, board dimensions, validity rules, the true scoring formula, hardware assumptions, total time limits, or deadline safety margins as tunable parameters. Algorithmic surrogate weights are allowed. Phase allocation may be redistributed only within the unchanged overall runtime budget.
Return the complete program plus a parameter_schema JSON array. Each entry must have name, kind (int or float), scale (linear or log), min, max, default; bounds/default are JSON numbers. Choose meaningful ranges from your own algorithm, not from other solutions.
Define all defaults in exactly one global block bounded by literal lines // AAD_PARAMETERS_BEGIN and // AAD_PARAMETERS_END. The interior must contain ONLY one declaration per schema entry, in index order: constexpr double AAD_PARAM_0_DEFAULT = number; (use int for an int entry). Continue indices 1,2,... . No comments or other code in the interior. The rest of the complete program must actually use these constants for defaults and parse the optional overrides. Do not change the optimization logic beyond replacing constants by these model-chosen runtime parameters.'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',type=Path,default=PROJECT/'runs/v3_pro_fromscratch')
    parser.add_argument('--calls',type=int,default=58)
    parser.add_argument('--variants',type=int,default=64)
    parser.add_argument('--ask-key',action='store_true')
    parser.add_argument('--prepare-only',action='store_true',help='Stop after parameter interface generation for inspection, without numeric trials or installation')
    parser.add_argument('--interface-rejection',help='Record manual inspection failure before any numeric trials and request model repair')
    parser.add_argument('--parameter-attempt-limit',type=int,default=2,help='Explicit bounded allowance for parameterization and repair attempts')
    parser.add_argument('--recover-parameter-metadata',type=int,help='Recover model-supplied schema from an already recorded response with exactly matching source')
    parser.add_argument('--retry-network-failure',action='store_true',help='Permit one additional parameterization attempt after exclusively network failures, without raising the cumulative API cap')
    args=parser.parse_args();root=args.run_dir.resolve();state_path=root/'state.json'
    if not 1<=args.parameter_attempt_limit<=16:raise ValueError('Parameter attempt limit must be 1..16')
    state=read_json(state_path)
    if state.get('frozen') or state.get('validation_started'):raise ValueError('Development is closed')
    folder=root/'numeric_search';folder.mkdir(exist_ok=True);path=folder/'state.json'
    if path.exists():
        search=read_json(path)
        if search['variant_limit']!=args.variants:raise ValueError('Numeric budget changed')
        if args.recover_parameter_metadata is not None:
            call=args.recover_parameter_metadata
            if search['variants'] or search.get('parameterized'):raise ValueError('Recovery requires unprepared interface and zero trials')
            event=next(e for e in state['events'] if e['call_id']==call and e['operator']=='parameterization')
            payload=read_json(root/'llm'/f'{call:05d}.response.json')
            proposal=json.loads(payload['choices'][0]['message']['content'])
            ident=event['candidate'];schema=validate_schema(proposal.get('parameter_schema'))
            if digest(proposal['code'])!=ident:raise ValueError('Recovered metadata source mismatch')
            state['records'][ident].update(parameter_schema=schema,parameter_schema_call_id=call)
            search.setdefault('metadata_recoveries',[]).append({'call_id':call,'candidate':ident,'previous_event_status':event['status']})
            search.update(phase='parameterize',pending_parameter_candidate=ident,pending_parameter_event=call)
        if args.parameter_attempt_limit>search.get('parameterization_limit',2):
            search.setdefault('attempt_limit_changes',[]).append({'previous':search.get('parameterization_limit',2),
                'next':args.parameter_attempt_limit,'cumulative_api_cap':args.calls})
            search['parameterization_limit']=args.parameter_attempt_limit
            if search['phase']=='failed' and not search.get('parameterized') and search['parameterization_calls']<args.parameter_attempt_limit:
                search['phase']='parameterize'
        if args.interface_rejection:
            if search['phase']!='search' or search['variants'] or not search.get('parameterized'):
                raise ValueError('Interface rejection requires prepared interface and zero numeric trials')
            search.setdefault('interface_reviews',[]).append({'candidate':search['parameterized'],
                'accepted':False,'reason':args.interface_rejection})
            search.update(phase='parameterize',repair_parent=search['parameterized'],last_error=args.interface_rejection)
            search.pop('parameterized',None)
        if args.retry_network_failure and search['phase']=='failed' and not search.get('parameterized'):
            ledger=read_json(root/'llm/ledger.json')
            attempts=[e for e in state['events'] if e['operator']=='parameterization']
            if not attempts or any(ledger[e['call_id']]['status']!='network_error' for e in attempts):
                raise ValueError('Explicit network retry requires exclusively network-failed parameterization attempts')
            search.update(phase='parameterize',parameterization_limit=search['parameterization_calls']+1)
            search.setdefault('network_retries',[]).append({'previous_attempts':search['parameterization_calls'],'cumulative_api_cap':args.calls})
        if search['phase'] in ('complete','failed'):
            print('Numeric phase already ended:',search['phase']);return
    else:
        eligible={k:r for k,r in state['records'].items() if r.get('full',{}).get('valid')}
        base=max(eligible,key=lambda k:eligible[k]['full']['mean'])
        search={'phase':'parameterize','base':base,'parameterization_calls':0,'installation_calls':0,
                'variants':[],'population':[],'variant_limit':args.variants,'private_inputs_used':False}
    if args.ask_key:os.environ['AAD_API_KEY']=getpass.getpass('AAD API key (hidden): ')
    config=read_json(root/'config.json');config['max_requests']=args.calls
    save_json(folder/'config.json',config)
    proposer=ProgramProposer(config,root);print('Available models:',proposer.check_connection(),flush=True)
    case_map={c.name:c for c in public_cases()[0]};protocol=read_json(root/'protocol.json')
    train=[case_map[name] for name in protocol['train']]
    assert len(train)==40 and not set(protocol['train'])&set(protocol['validation'])
    assert all(c.fingerprint==protocol['inputs'][c.name] for c in train)
    screen=[case_map[name] for name in protocol['screen']]
    assert len(screen)==12 and {c.name for c in screen}<=set(protocol['train'])
    smoke=[case_map[name] for name in protocol['screen'][::2]]
    search['screen_names']=[c.name for c in screen]
    short=CleanEvaluator(root/'evaluations',workers=6,seconds=.6)
    full=CleanEvaluator(root/'evaluations',workers=6,seconds=4.8)
    rng=random.Random(644001)
    if 'rng' in search:rng.setstate(tuple_tree(search['rng']))

    def save():
        search['rng']=rng.getstate()
        state['numeric_search_summary']={k:search.get(k) for k in ('phase','base','parameterized','installed','variant_limit')}
        state['numeric_search_summary']['variants_completed']=len(search['variants'])
        save_json(state_path,state);save_json(path,search)

    def source(ident):
        code=(root/'candidates'/ident/'solution.cpp').read_text(encoding='utf-8')
        assert digest(code)==ident
        return code

    def propose(task,assembly=None):
        event={'call_id':proposer.requests,'operator':task['operation'],
               'parents':[digest(task[k]) for k in ('parent_source','donor_source') if task.get(k)]}
        try:proposal=proposer.generate(task)
        except (ValueError,RuntimeError) as error:
            event.update(status='request_error',error=str(error));state['events'].append(event);save()
            if 'HTTP 401' in str(error) or 'HTTP 402' in str(error):raise
            return None,None,str(error)
        try:
            code=assemble_parameters(task['parent_source'],proposal['code'],assembly['schema'],assembly['values']) if assembly else proposal['code']
        except ValueError as error:
            event.update(status='invalid_parameter_block',error=str(error));state['events'].append(event);save()
            return None,None,str(error)
        ident=digest(code)
        if ident in state['records']:
            if task['operation']=='parameterization' and 'parameter_schema' in proposal:
                state['records'][ident].update(parameter_schema=proposal['parameter_schema'],parameter_schema_call_id=proposal['call_id'])
            event.update(status='duplicate',duplicate=True,candidate=ident);state['events'].append(event);save()
            return ident,event,None
        destination=root/'candidates'/ident;destination.mkdir(parents=True,exist_ok=True)
        (destination/'solution.cpp').write_text(code,encoding='utf-8')
        record={k:v for k,v in proposal.items() if k!='code'}
        record.update(origin='LLM-parameter-block-assembly' if assembly else 'LLM-generated-full-program',
                      parents=event['parents'],operator=task['operation'])
        if assembly:record['assembly']=assembly
        state['records'][ident]=record;event['candidate']=ident;state['events'].append(event);save()
        return ident,event,None

    def evaluate_default(ident,event):
        record=state['records'][ident];code=source(ident)
        result=short.evaluate(code,smoke);record['smoke']=result;save()
        if result['valid']:
            result=full.evaluate(code,train);record['full']=result
        event.update(status='full_evaluated' if result['valid'] else 'invalid',mean=result['mean'],
                     family=record['family'],hypothesis=record['hypothesis'][:1200])
        save_json(root/'candidates'/ident/'record.json',record);save()
        return result

    save()
    while search['phase']=='parameterize' and (search.get('pending_parameter_candidate') or search['parameterization_calls']<search.get('parameterization_limit',2) and proposer.requests<args.calls):
        if (root/'STOP_AFTER_CURRENT').exists():save();print('Cooperative stop before parameterization.');return
        if search.get('pending_parameter_candidate'):
            ident=search['pending_parameter_candidate']
            event=next(e for e in state['events'] if e['call_id']==search['pending_parameter_event'])
        else:
            parent=search.get('repair_parent',search['base'])
            task={'operation':'parameterization','instruction':PARAMETER_INSTRUCTION,
                  'parent_source':source(parent),'diagnostic':search.get('last_error'),
                  'original_parent_mean':state['records'][search['base']]['full']['mean']}
            if parent!=search['base']:task['donor_source']=source(search['base'])
            search['parameterization_calls']+=1;save()
            ident,event,error=propose(task)
            if error:search['last_error']=error;save();continue
            search.update(pending_parameter_candidate=ident,pending_parameter_event=event['call_id']);save()
        record=state['records'][ident];search['repair_parent']=ident
        try:
            schema=validate_schema(record.get('parameter_schema'))
            start,end=parameter_span(source(ident));validate_block(source(ident)[start:end],schema)
        except ValueError as error:
            event.update(status='invalid_parameter_schema',error=str(error));search['last_error']=str(error)
            search.pop('pending_parameter_candidate',None);save();continue
        result=evaluate_default(ident,event)
        search.pop('pending_parameter_candidate',None)
        if not result['valid']:
            failures=[r for r in result['cases'] if r['status']!='AC']
            search['last_error']=compact(result)
            if failures and failures[0]['status']!='CE':
                bad=failures[0];search['last_error']['public_counterexample']={
                    'input':case_map[bad['name']].text,'output':bad.get('stdout',''),
                    'status':bad['status'],'message':bad.get('message','')}
            save();continue
        if result['mean']<state['records'][search['base']]['full']['mean']-.002:
            search['last_error']={'error':'Default behavior regressed by more than 2M; preserve the original algorithm and defaults',
                                  'original_mean':state['records'][search['base']]['full']['mean'],'new_mean':result['mean']}
            save();continue
        full.arguments=tuple(decode(defaults_vector(schema),schema))
        argument_check=full.evaluate(source(ident),screen)
        full.arguments=()
        record['default_argument_check']=argument_check
        save_json(root/'candidates'/ident/'record.json',record)
        expected=sum(r['score'] for r in result['cases'] if r['name'] in {c.name for c in screen})/len(screen)/1e9
        if not argument_check['valid'] or abs(argument_check['mean']-expected)>.002:
            event['status']='invalid_parameter_interface'
            search['last_error']={'error':'Passing the declared default arguments must preserve normal default behavior',
                                 'arguments':decode(defaults_vector(schema),schema),
                                 'expected_screen_mean':expected,'observed':compact(argument_check)}
            save();continue
        search.update(parameterized=ident,schema=schema,phase='search');save()
        print({'parameterized_default_mean':result['mean'],'parameters':schema},flush=True)
    if search['phase']=='parameterize':
        search.update(phase='failed',reason='Could not produce a valid parameterized program within the recorded attempt/request limits');save();print(search['reason']);return

    if args.prepare_only:
        save();print('Parameter interface prepared; numeric search has not been advanced by this invocation.',flush=True);return

    schema=search['schema'];code=source(search['parameterized'])
    population_size=max(8,min(16,2*len(schema)))
    while search['phase']=='search' and len(search['variants'])<args.variants:
        if (root/'STOP_AFTER_CURRENT').exists():save();print('Cooperative stop between numeric variants.');return
        variants=search['variants'];index=len(variants);target=None
        if 'pending' not in search:
            seen={tuple(v['arguments']) for v in variants}
            for attempt in range(100):
                if index==0:vector=defaults_vector(schema)
                elif index<population_size or attempt>30:vector=[rng.random() for _ in schema]
                else:
                    target=index%population_size
                    population=[variants[i]['vector'] for i in search['population']]
                    vector=differential_trial(population,target,rng)
                arguments=decode(vector,schema)
                if tuple(arguments) not in seen:break
            else:break
            search['pending']={'vector':vector,'arguments':arguments,'target':target};save()
        pending=search['pending'];full.arguments=tuple(pending['arguments'])
        result=full.evaluate(code,screen)
        score=result['mean'] if result['valid'] else -1.
        variant={**pending,'screen':result,'selection_score':score};variants.append(variant)
        if len(search['population'])<population_size:search['population'].append(index)
        elif pending['target'] is not None:
            old=search['population'][pending['target']]
            if score>variants[old]['selection_score']:search['population'][pending['target']]=index
        search.pop('pending');save()
        print({'numeric_variant':len(variants),'screen_mean':result['mean'],'valid':result['valid'],
               'best_screen_mean':max(v['selection_score'] for v in variants)},flush=True)
    if search['phase']=='search':
        valid=[i for i,v in enumerate(search['variants']) if v['screen']['valid']]
        if not valid:search.update(phase='failed',reason='No valid numeric variants');save();return
        finalists=sorted(valid,key=lambda i:search['variants'][i]['screen']['mean'],reverse=True)[:4]
        if 0 in valid and 0 not in finalists:finalists.append(0)
        for i in finalists:
            if (root/'STOP_AFTER_CURRENT').exists():save();print('Cooperative stop between numeric finalists.');return
            variant=search['variants'][i]
            if 'full' not in variant:
                full.arguments=tuple(variant['arguments']);variant['full']=full.evaluate(code,train);save()
                print({'numeric_finalist':i,'mean':variant['full']['mean'],'valid':variant['full']['valid']},flush=True)
            if 'smoke' not in variant:
                short.arguments=tuple(variant['arguments']);variant['smoke']=short.evaluate(code,smoke);save()
        eligible=[i for i in finalists if search['variants'][i]['full']['valid'] and search['variants'][i]['smoke']['valid']]
        if not eligible:search.update(phase='failed',reason='No fully valid numeric finalist');save();return
        winner=max(eligible,key=lambda i:search['variants'][i]['full']['mean'])
        search['best_variant']=winner
        improvement=search['variants'][winner]['full']['mean']-state['records'][search['base']]['full']['mean']
        is_default=all(math.isclose(float(a),p['default'],rel_tol=1e-12,abs_tol=1e-15)
                       for a,p in zip(search['variants'][winner]['arguments'],schema))
        if improvement<=.00005 or is_default:
            search.update(phase='complete',reason='No nondefault full-training gain above 0.05M; installation skipped');save();print(search['reason']);return
        search['phase']='install';save()
    full.arguments=();short.arguments=()
    while search['phase']=='install' and (search.get('pending_install_candidate') or search['installation_calls']<2 and proposer.requests<args.calls):
        if (root/'STOP_AFTER_CURRENT').exists():save();print('Cooperative stop before default installation.');return
        values=search['variants'][search['best_variant']]['arguments']
        task={'operation':'parameter_defaults','parent_source':code,'parameter_schema':schema,
              'selected_values':values,'instruction':f'Return ONLY the block from {BEGIN} through {END}. Use exactly the existing constexpr names/types/index order with the selected numeric values. No other changes or comments.',
              'diagnostic':search.get('installation_error')}
        assembly={'type':'parameter-block','parent':search['parameterized'],'schema':schema,'values':values}
        if search.get('pending_install_candidate'):
            ident=search['pending_install_candidate']
            event=next(e for e in state['events'] if e['call_id']==search['pending_install_event'])
        else:
            search['installation_calls']+=1;save()
            ident,event,error=propose(task,assembly)
            if error:search['installation_error']=error;save();continue
            search.update(pending_install_candidate=ident,pending_install_event=event['call_id']);save()
        result=evaluate_default(ident,event)
        search.pop('pending_install_candidate',None)
        if result['valid']:
            search.update(phase='complete',installed=ident,installed_mean=result['mean'],
                          installed_vs_numeric_mean_delta=result['mean']-search['variants'][search['best_variant']]['full']['mean'],
                          installed_improves_original=result['mean']>state['records'][search['base']]['full']['mean'])
            ranked=[k for k,r in state['records'].items() if r.get('full',{}).get('valid')]
            state['development_best']=max(ranked,key=lambda k:objective(state['records'][k]['full']))
            save();print({'installed_mean':result['mean'],'source_sha256':ident},flush=True);return
        search['installation_error']=compact(result);save()
    if search['phase']=='install':
        search.update(phase='failed',reason='Tuned defaults could not be installed within the API budget');save();print(search['reason'])


if __name__=='__main__':main()
