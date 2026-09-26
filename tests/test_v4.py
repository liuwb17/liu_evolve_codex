import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aad.io import digest,save_json,read_json
from aad.problem import Case
from aad.provider import BudgetExhausted
from aad.v3_provider import PROBLEM
from aad.v4_protocol import program,apply_edit,paired_decision,choose_arm,parameter_influence
from aad.v4_provider import V4Provider
from aad.v4_engine import V4Engine

CODE='int main(){return 0;}\n// AAD_V4_COMPLETE\n'
PLAN={k:'test' for k in ('hypothesis','mechanism','expected_effect','risk','test')}

def result(score,names=('a','b','c')):
    return {'valid':True,'mean':score,'cases':[{'name':n,'score':round(score*1e9),'seconds':.1,'stdout':str(score)} for n in names]}

class V4Tests(unittest.TestCase):
    def test_exact_edit_contract(self):
        edit={'parent_sha256':digest(CODE),'edits':[{'old':'return 0','new':'return 1'}]}
        self.assertIn('return 1',apply_edit(CODE,json.dumps(edit)))
        edit['parent_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'parent'):apply_edit(CODE,json.dumps(edit))
        edit['parent_sha256']=digest(CODE);edit['edits']*=2
        with self.assertRaisesRegex(ValueError,'one atomic|Exactly one'):apply_edit(CODE,json.dumps(edit))
        with self.assertRaises(ValueError):program('int main(){}')

    def test_selection_keeps_incumbent_for_780_point_gain(self):
        a=[result(.9887533447)]*3;b=[result(.9887525648)]*3
        self.assertFalse(paired_decision(a,b,margin=.0002)['promote'])
        self.assertTrue(paired_decision([result(.991)]*3,b)['promote'])

    def test_repeat_cases_not_pseudoreplicated(self):
        d=paired_decision([result(.9)]*3,[result(.8)]*3)
        self.assertEqual(d['case_count'],3)
        with self.assertRaises(ValueError):paired_decision([result(.9,('x',))],[result(.8)])

    def test_request_recovery_does_not_call_api_again(self):
        cases=[Case(f'p{i}',f'1\n{i} 0 1\n') for i in range(50)]
        config={'initial_valid_roots':1,'max_initial_attempts':2,'repeats':3,'margin':.0001,'validation_margin':.0002}
        class Provider:
            requests=1
            ledger=[{'status':'ok'}]
            def request(self,*args):raise AssertionError('Paid request repeated')
        with tempfile.TemporaryDirectory() as tmp:
            engine=V4Engine(tmp,Provider(),None,cases[:40],cases[40:],config)
            engine.s['pending']={'plan_call':0}
            save_json(Path(tmp)/'llm/00000.response.json',{'choices':[{'message':{'content':json.dumps(PLAN)}}]})
            self.assertEqual(engine.invoke('plan',{},'plan_call')['call_id'],0)

    def test_unknown_usage_retains_conservative_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_json(Path(tmp)/'llm/ledger.json',[{'id':0,'status':'started','reserved_tokens':9000}])
            provider=V4Provider(tmp,'deepseek-flash',5,100000,key='test')
            self.assertEqual(provider.charged_tokens,9000)
            self.assertEqual(provider.ledger[0]['status'],'interrupted_usage_unknown')

    def test_source_edit_ambiguous_anchor_rejected(self):
        code='int main(){int a=0;int b=0;return a+b;}\n// AAD_V4_COMPLETE\n'
        edit={'parent_sha256':digest(code),'edits':[{'old':'=0','new':'=1'}]}
        with self.assertRaisesRegex(ValueError,'unique'):apply_edit(code,json.dumps(edit))

    def test_private_gate_before_audit_and_loader(self):
        import importlib.util
        import sys
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('v4_system_test_check',root/'scripts/v4_system_test.py')
        module=importlib.util.module_from_spec(spec)
        with patch.object(sys,'path',[str(root/'scripts'),*sys.path]):spec.loader.exec_module(module)
        with patch.object(sys,'argv',['test','--run-dir','unused','--output','unused2']),patch.object(module,'read_json',return_value={'frozen':False}),patch.object(module,'audit') as audit,patch.object(module.v3_system_test,'main') as terminal:
            with self.assertRaisesRegex(ValueError,'Freeze'):module.main()
            audit.assert_not_called();terminal.assert_not_called()

    def test_bandit_explores_and_charges_cost(self):
        self.assertEqual(choose_arm([],['a','b']),'a')
        h=[{'arm':'a','reward':1,'tokens':100000},{'arm':'b','reward':1,'tokens':10000}]
        self.assertEqual(choose_arm(h,['a','b']),'b')

    def test_unused_or_noisy_parameter_rejected(self):
        self.assertFalse(parameter_influence([result(.8)]*2,[result(.8)]*2)['accept'])
        self.assertTrue(parameter_influence([result(.8)]*2,[result(.9)]*2)['accept'])
        self.assertFalse(parameter_influence([result(.8),result(.81)],[result(.9)]*2)['accept'])

    def test_budget_guard_before_network(self):
        with tempfile.TemporaryDirectory() as tmp,patch('aad.v4_provider.urllib.request.urlopen') as http:
            provider=V4Provider(tmp,'deepseek-flash',1,1,key='test')
            with self.assertRaises(BudgetExhausted):provider.request('plan',{})
            http.assert_not_called();self.assertEqual(provider.requests,0)

    def test_transport_modes_and_truncation(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'choices':[{'finish_reason':'length','message':{'content':CODE}}],
                'usage':{'total_tokens':12},'model':'deepseek-flash'}).encode()
        with tempfile.TemporaryDirectory() as tmp,patch('aad.v4_provider.urllib.request.urlopen',return_value=Response()):
            provider=V4Provider(tmp,'deepseek-flash',4,1000000,key='test')
            for kind in ('plan','program','edit'):
                with self.assertRaisesRegex(ValueError,'Incomplete'):provider.request(kind,{})
            bodies=[read_json(Path(tmp)/'llm'/f'{i:05d}.request.json') for i in range(3)]
            self.assertEqual(bodies[0]['thinking']['type'],'enabled')
            self.assertEqual(bodies[1]['thinking']['type'],'disabled')
            self.assertNotIn('response_format',bodies[1]);self.assertEqual(provider.charged_tokens,36)

    def test_disabled_token_cap_does_not_disable_request_cap(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(PLAN)}}],
                'usage':{'total_tokens':12},'model':'deepseek-flash'}).encode()
        with tempfile.TemporaryDirectory() as tmp,patch('aad.v4_provider.urllib.request.urlopen',return_value=Response()) as http:
            provider=V4Provider(tmp,'deepseek-flash',1,0,key='test')
            provider.request('plan',{})
            with self.assertRaises(BudgetExhausted):provider.request('plan',{})
            self.assertEqual(http.call_count,1)

    def test_full_offline_engine_and_audit(self):
        import importlib.util
        audit_path=Path(__file__).resolve().parents[1]/'scripts/v4_audit.py'
        spec=importlib.util.spec_from_file_location('v4_audit_test',audit_path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cases=[Case(f'p{i}',f'1\n{i} 0 1\n') for i in range(50)]
        train=cases[:40];val=cases[40:];seen=[]
        class Provider:
            max_calls=4
            def __init__(self,root):self.root=Path(root);self.ledger=[]
            @property
            def requests(self):return len(self.ledger)
            def request(self,kind,task):
                call=self.requests
                content=json.dumps(PLAN) if kind=='plan' else CODE.replace('return 0',f'return {call}')
                response={'choices':[{'finish_reason':'stop','message':{'content':content}}],'usage':{'total_tokens':20}}
                save_json(self.root/'llm'/f'{call:05d}.response.json',response)
                save_json(self.root/'llm'/f'{call:05d}.request.json',{'messages':[{}, {'content':json.dumps({'problem':PROBLEM,**task})}]})
                self.ledger.append({'status':'ok','usage':{'total_tokens':20}})
                return {'call_id':call,'content':content,'usage':{'total_tokens':20}}
        def evaluate(code,subset,seconds,replica):
            seen.append((replica,[c.name for c in subset]));return result(.9 if 'return 3' in code else .8,[c.name for c in subset])
        config={'initial_valid_roots':2,'max_initial_attempts':4,'repeats':3,'margin':.0001,'validation_margin':.0002}
        with tempfile.TemporaryDirectory() as tmp:
            provider=Provider(tmp);engine=V4Engine(tmp,provider,evaluate,train,val,config);engine.run()
            self.assertEqual(len(engine.s['islands']),2)
            self.assertTrue(all(not any(n in {c.name for c in val} for n in names) for _,names in seen))
            champion=engine.finalize();self.assertEqual(provider.requests,4)
            self.assertTrue(engine.s['frozen']);self.assertIn('return 3',engine.source(champion))
            self.assertTrue(module.audit(tmp)['all_origins_verified'])
            with self.assertRaisesRegex(ValueError,'closed'):engine.run()
            engine2=V4Engine(tmp,provider,evaluate,train,val,config)
            self.assertEqual(engine2.finalize(),champion)

if __name__=='__main__':unittest.main()
