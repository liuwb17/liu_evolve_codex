import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aad.v3_evaluator import CleanEvaluator
from aad.v3_provider import PROBLEM
from aad.problem import Case

class ColdStartTests(unittest.TestCase):
    def test_problem_is_specification_not_seed(self):
        self.assertNotIn('int main',PROBLEM)
        self.assertNotIn('Useful directions',PROBLEM)
        self.assertNotIn('annealing',PROBLEM.lower())
        self.assertNotIn('kusano',PROBLEM.lower())
        self.assertIn('argv[1]',PROBLEM)

    def test_reference_free_mounts(self):
        inner={'status':'AC','stdout':'0 0 1 1\n','stderr':'','seconds':.61,'returncode':0}
        outer={'status':'AC','stdout':json.dumps(inner),'stderr':'','seconds':1.2,'returncode':0}
        with tempfile.TemporaryDirectory() as root,patch('aad.v3_evaluator.execute',return_value=outer) as execute,patch('aad.v3_evaluator.subprocess.run'):
            evaluator=CleanEvaluator.__new__(CleanEvaluator)
            evaluator.root=Path(root);evaluator.seconds=.6;evaluator.workers=1;evaluator.image='test-image'
            evaluator.compile=lambda code:(Path(root),None)
            result=evaluator.evaluate('source',[Case('one','1\n0 0 1\n')])
            self.assertTrue(result['valid'])
            command=' '.join(execute.call_args.args[0])
            self.assertIn('target=/timer.py,readonly',command)
            self.assertNotIn('/library',command)
            self.assertNotIn('/harness',command)
            self.assertNotIn('human_references',command)
            self.assertNotIn('seed_v2',command)

    def test_public_split_is_disjoint(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v3_search',root/'scripts/v3_search.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cases,_=module.public_cases()
        self.assertEqual(len(cases),50)
        train={c.fingerprint for i,c in enumerate(cases) if i%5!=4}
        validation={c.fingerprint for i,c in enumerate(cases) if i%5==4}
        self.assertEqual(len(train),40);self.assertEqual(len(validation),10)
        self.assertFalse(train & validation)

    def test_private_gate_precedes_loading_cases(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v3_system_test',root/'scripts/v3_system_test.py')
        module=importlib.util.module_from_spec(spec)
        import sys
        with patch.object(sys,'path',[str(root/'scripts'),*sys.path]):
            spec.loader.exec_module(module)
        with patch.object(module,'read_json',return_value={'frozen':False}),patch.object(module,'prepare') as prepare,patch.object(sys,'argv',['v3_system_test']):
            with self.assertRaisesRegex(ValueError,'Freeze'):
                module.main()
            prepare.assert_not_called()

    def test_full_evaluation_does_not_reject_low_short_score(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v3_search_test',root/'scripts/v3_search.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cases=[Case(f'public_{i:04d}',f'1\n{i} 0 1\n') for i in range(50)]
        class Proposer:
            def __init__(self,*args):self.requests=0
            def check_connection(self):return ['mock']
            def generate(self,task):
                self.requests+=1
                return {'code':f'candidate {self.requests}','hypothesis':'test','family':str(self.requests)}
        evaluated=[]
        class Evaluator:
            def __init__(self,*args,seconds,**kwargs):self.seconds=seconds
            def evaluate(self,code,subset):
                value=.9 if code=='candidate 1' else .1
                evaluated.append((code,self.seconds,len(subset)))
                return {'valid':True,'mean':value,'cases':[
                    {'name':c.name,'score':int(value*1e9),'status':'AC','seconds':self.seconds,
                     'input_sha256':c.fingerprint,'stdout':'0 0 1 1\n'} for c in subset]}
        import sys
        with tempfile.TemporaryDirectory() as folder,patch.object(module,'ProgramProposer',Proposer),patch.object(module,'CleanEvaluator',Evaluator),patch.object(module,'public_cases',return_value=(cases,{})),patch.object(sys,'argv',['v3_search','--run-dir',folder,'--calls','2','--initial','1','--full-evaluation']):
            module.main()
            state=json.loads((Path(folder)/'state.json').read_text())
            self.assertEqual(len(state['records']),2)
            self.assertTrue(all('full' in r for r in state['records'].values()))
            self.assertIn(('candidate 2',4.8,40),evaluated)
            self.assertFalse(any(size==10 for _,_,size in evaluated))
            self.assertFalse(state['frozen'])

    def test_stagnation_restart_respects_improvement_and_spacing(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v3_restart_test',root/'scripts/v3_search.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        state={'records':{'a':{'full':{'valid':True,'mean':.8,'cases':[{'score':800000000}]}}},
               'events':[{'operator':'independent_initial_design','candidate':'a'}]}
        self.assertFalse(module.restart_due(state,3))
        state['events'] += [{'operator':'targeted_improvement','status':'invalid'} for _ in range(3)]
        self.assertTrue(module.restart_due(state,3))
        self.assertFalse(module.restart_due(state,0))
        state['events'].append({'operator':'independent_restart','status':'request_error'})
        self.assertFalse(module.restart_due(state,3))
        state['records']['b']={'full':{'valid':True,'mean':.9,'cases':[{'score':900000000}]}}
        state['events'] += [{'operator':'targeted_improvement','candidate':'b'}]
        self.assertFalse(module.restart_due(state,3))

    def test_validation_start_closes_development_before_data_or_api(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v3_validation_guard_test',root/'scripts/v3_search.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        import sys
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'state.json').write_text(json.dumps({'validation_started':True,'frozen':False}))
            with patch.object(sys,'argv',['v3_search','--run-dir',folder,'--resume']),patch.object(module,'public_cases') as cases,patch.object(module,'ProgramProposer') as proposer:
                with self.assertRaisesRegex(ValueError,'development is closed'):module.main()
                cases.assert_not_called();proposer.assert_not_called()

    def test_behavior_frontier_keeps_complementary_not_dominated_programs(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v3_frontier_test',root/'scripts/v3_search.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def record(a,b):return {'full':{'valid':True,'cases':[{'name':'a','score':a},{'name':'b','score':b}]}}
        records={'old':record(5,5),'first':record(9,7),'second':record(8,8),
                 'bad':{'full':{'valid':False}}}
        self.assertEqual(module.nondominated_ids(records),{'first','second'})

    def test_reasoning_budget_is_routed_by_operation_and_logged(self):
        import io
        import os
        from aad.v3_provider import ProgramProposer
        payload={'choices':[{'message':{'content':json.dumps({'code':'test','family':'test','hypothesis':'test'})}}]}
        config={'base_url':'https://example.invalid','model':'mock','api_key_env':'V3_TEST_KEY',
                'max_requests':3,'max_output_tokens':64,'request_timeout':1,
                'exploration_effort':'high','request_options':{'thinking':{'type':'enabled'},'reasoning_effort':'low'}}
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'V3_TEST_KEY':'unit-secret'}),patch('aad.v3_provider.urllib.request.urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(payload).encode())):
            proposer=ProgramProposer(config,Path(folder))
            proposer.generate({'operation':'structural_redesign'})
            proposer.generate({'operation':'repair'})
            proposer.generate({'operation':'complementary_crossover','donor_selection_reason':'recent-structural-mechanism'})
            first=json.loads((Path(folder)/'llm/00000.request.json').read_text())
            second=json.loads((Path(folder)/'llm/00001.request.json').read_text())
            self.assertEqual(first['reasoning_effort'],'high')
            self.assertEqual(second['reasoning_effort'],'low')
            third=json.loads((Path(folder)/'llm/00002.request.json').read_text())
            self.assertEqual(third['reasoning_effort'],'high')
            self.assertNotIn('unit-secret',json.dumps(first))

    def test_mechanism_donor_can_be_dominated_but_is_quality_bounded(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v3_donor_test',root/'scripts/v3_search.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def record(ident,operation,a,b):
            return {'call_id':ident,'operator':operation,'full':{'valid':True,'mean':(a+b)/2,
                    'cases':[{'name':'a','score':int(a*1e9)},{'name':'b','score':int(b*1e9)}]}}
        records={'parent':record(1,'targeted_improvement',.984,.984),
                 'complement':record(2,'targeted_improvement',.985,.979),
                 'mechanism':record(3,'structural_redesign',.96,.96),
                 'too_weak':record(4,'novel_design',.9,.9)}
        pool=['parent','complement']
        self.assertEqual(module.select_donor(records,pool,'parent',False),('complement','case-complementary'))
        self.assertEqual(module.select_donor(records,pool,'parent',True),('mechanism','recent-structural-mechanism'))

    def test_malformed_response_metadata_is_counted_not_left_started(self):
        import io
        import os
        from aad.v3_provider import ProgramProposer
        config={'base_url':'https://example.invalid','model':'mock','api_key_env':'V3_TEST_KEY',
                'max_requests':1,'max_output_tokens':64,'request_timeout':1}
        for proposal in ([],{'hypothesis':'x','family':'x','code':'x','extra':float('nan')},
                         {'hypothesis':'x','family':'x','code':'\ud800'}):
            payload={'choices':[{'message':{'content':json.dumps(proposal)}}]}
            with self.subTest(proposal_type=type(proposal).__name__),tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'V3_TEST_KEY':'unit-secret'}),patch('aad.v3_provider.urllib.request.urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(payload).encode())):
                proposer=ProgramProposer(config,Path(folder))
                with self.assertRaises(ValueError):proposer.generate({'operation':'independent_initial_design'})
                self.assertEqual(proposer.requests,1)
                self.assertEqual(proposer.ledger[0]['status'],'invalid_response')

if __name__=='__main__':unittest.main()
