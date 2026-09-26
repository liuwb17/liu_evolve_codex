import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aad.io import digest,save_json
from aad.problem import Case
from aad.v3_provider import PROBLEM
from aad.v5_policy import program,edit,choose_operator,feedback_timing,pool
from aad.v5_engine import V5Engine
from aad.v5_provider import V5Provider

CODE='int a=1; int b=2; int main(){return 0;}\n// AAD_V5_COMPLETE\n'
def results(score,cases):
    return {'valid':True,'mean':score,'cases':[{'name':c.name,'score':round(score*1e9),
        'status':'AC','seconds':.1,'stdout':f'{i} 0 {i+1} 1\n'} for i,c in enumerate(cases)]}

class V5Tests(unittest.TestCase):
    def test_coordinated_edits_and_overlap_rejection(self):
        payload={'parent_sha256':digest(CODE),'hypothesis':'change coordinated constants',
                 'edits':[{'old':'a=1','new':'a=3'},{'old':'b=2','new':'b=4'}]}
        code,_=edit(CODE,json.dumps(payload));self.assertIn('a=3',code);self.assertIn('b=4',code)
        payload['edits']=[{'old':'int a=1','new':'int a=3'},{'old':'a=1','new':'a=4'}]
        with self.assertRaisesRegex(ValueError,'Overlapping'):edit(CODE,json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'marker'):program('int main(){}')

    def test_crossover_quota_not_sticky_historical_reward(self):
        history=[]
        for slot in range(100):
            arm=choose_operator(history,slot)
            history.append({'arm':arm,'parent_gain':0,'reward':100 if arm=='crossover' else 0})
            self.assertLessEqual(sum(e['arm']=='crossover' for e in history[-8:]),1)
        self.assertLessEqual(sum(e['arm']=='crossover' for e in history),13)
        self.assertGreater(sum(e['arm']=='redesign' for e in history),20)

    def test_runtime_gain_uses_matching_cases(self):
        cases=[Case('a','1\n0 0 1\n'),Case('b','1\n1 0 1\n')]
        record={'full':results(.9,cases),'smoke':results(.89,cases[:1])}
        feedback=feedback_timing(record)
        self.assertTrue(feedback['underutilized']);self.assertAlmostEqual(feedback['same_cases_score_gain_from_short'],.01)

    def test_thinking_enabled_for_complete_code_and_no_plan_request(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':CODE}}],
                'model':'deepseek-flash','usage':{'total_tokens':20}}).encode()
        with tempfile.TemporaryDirectory() as root,patch('aad.v5_provider.urllib.request.urlopen',return_value=Response()) as http:
            provider=V5Provider(root,'deepseek-flash',1,0,key='test');provider.request('program',{})
            body=json.loads(http.call_args.args[0].data)
            self.assertEqual(body['thinking']['type'],'enabled');self.assertEqual(body['max_tokens'],65536)
            self.assertNotIn('response_format',body);self.assertEqual(provider.requests,1)

    def test_repeat_failure_reaches_repair_feedback(self):
        cases=[Case(f'p{i}',f'1\n{i} 0 1\n') for i in range(50)]
        def evaluate(code,subset,seconds,replica):
            r=results(.91,subset)
            if replica=='train-r1':
                r['valid']=False;r['cases'][0].update(status='WA',message='overlap on repeat',score=0)
            return r
        config={'repeats':3,'validation_margin':.0002}
        with tempfile.TemporaryDirectory() as tmp:
            engine=V5Engine(tmp,None,evaluate,cases[:40],cases[40:],config)
            ident=digest(CODE);folder=Path(tmp)/'candidates'/ident;folder.mkdir(parents=True)
            (folder/'solution.cpp').write_text(CODE)
            engine.s['records'][ident]={'parent':None,'lineage':ident,'smoke':results(.9,cases[:6]),
                'full':results(.9,cases[:40]),'repeats':[results(.9,cases[:40])]}
            pending={'arm':'local','parent':None};engine.qualify(ident,pending)
            self.assertEqual(pending['status'],'invalid_repeat');self.assertEqual(engine.s['repair'],ident)
            self.assertNotIn(ident,pool(engine.s['records']))
            with patch('aad.v5_engine.layout_diagnostics',return_value={}):feedback=engine.feedback(ident)
            self.assertTrue(feedback['failure_on_repeat']);self.assertEqual(feedback['examples'][0]['status'],'WA')
            self.assertEqual(engine.task()['arm'],'repair')

    def test_closed_development_blocks_resume(self):
        cases=[Case(f'p{i}',f'1\n{i} 0 1\n') for i in range(50)]
        with tempfile.TemporaryDirectory() as tmp:
            engine=V5Engine(tmp,None,None,cases[:40],cases[40:],{'repeats':3})
            engine.s['development_closed']=True
            with self.assertRaisesRegex(ValueError,'closed'):engine.run()

    def test_one_call_offline_workflow_and_audit(self):
        cases=[Case(f'p{i}',f'1\n{i} 0 1\n') for i in range(50)];seen=[]
        class Provider:
            max_calls=4
            def __init__(self,root):self.root=Path(root);self.ledger=[]
            @property
            def requests(self):return len(self.ledger)
            def request(self,kind,task):
                call=self.requests;code=CODE.replace('return 0',f'return {call}')
                save_json(self.root/'llm'/f'{call:05d}.response.json',{'choices':[{'finish_reason':'stop','message':{'content':code}}]})
                save_json(self.root/'llm'/f'{call:05d}.request.json',{'messages':[{}, {'content':json.dumps({'problem':PROBLEM,**task})}]})
                self.ledger.append({'status':'ok','usage':{'total_tokens':100}})
                return {'call_id':call,'content':code,'usage':{'total_tokens':100}}
        def evaluate(code,subset,seconds,replica):
            seen.extend(c.name for c in subset)
            value=.8+int(code.split('return ')[1].split(';')[0])*.01
            return results(value,subset)
        with tempfile.TemporaryDirectory() as tmp:
            provider=Provider(tmp);engine=V5Engine(tmp,provider,evaluate,cases[:40],cases[40:],{'repeats':3,'validation_margin':.0002})
            engine.run();self.assertEqual(len(engine.s['records']),4);self.assertEqual(provider.requests,4)
            self.assertFalse(set(seen)&{c.name for c in cases[40:]})
            engine.finalize();self.assertTrue(engine.s['frozen']);self.assertEqual(provider.requests,4)
            path=Path(__file__).resolve().parents[1]/'scripts/v5_audit.py'
            spec=importlib.util.spec_from_file_location('v5_audit_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            self.assertTrue(module.audit(tmp)['all_origins_verified'])

if __name__=='__main__':unittest.main()
