import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aad.io import digest,save_json
from aad.problem import Case
from aad.parameters import BEGIN,END


class NumericWorkflowTests(unittest.TestCase):
    def module(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('numeric_workflow_test',root/'scripts/v3_parameter_search.py')
        module=importlib.util.module_from_spec(spec)
        with patch.object(sys,'path',[str(root/'scripts'),*sys.path]):spec.loader.exec_module(module)
        return module

    def test_closed_development_never_starts_api(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as directory:
            save_json(Path(directory)/'state.json',{'validation_started':True})
            with patch.object(sys,'argv',['parameter_search','--run-dir',directory]),patch.object(module,'ProgramProposer') as proposer:
                with self.assertRaisesRegex(ValueError,'closed'):module.main()
                proposer.assert_not_called()

    def test_network_retry_cannot_reopen_model_failure(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            save_json(root/'state.json',{'events':[{'call_id':0,'operator':'parameterization'}]})
            save_json(root/'numeric_search/state.json',{'phase':'failed','variant_limit':64,'parameterization_calls':2})
            save_json(root/'llm/ledger.json',[{'status':'invalid_response'}])
            with patch.object(sys,'argv',['parameter_search','--run-dir',directory,'--retry-network-failure']),patch.object(module,'ProgramProposer') as proposer:
                with self.assertRaisesRegex(ValueError,'exclusively network'):module.main()
                proposer.assert_not_called()

    def test_full_mock_workflow_keeps_private_and_validation_out(self):
        module=self.module()
        cases=[Case(f'public_{i:04d}',f'1\n{i} 0 1\n') for i in range(50)]
        train=[c for i,c in enumerate(cases) if i%5!=4];validation=[c for i,c in enumerate(cases) if i%5==4]
        schema=[{'name':'knob','kind':'int','scale':'linear','min':1,'max':9,'default':1}]
        block=lambda value:f'{BEGIN}\nconstexpr int AAD_PARAM_0_DEFAULT = {value};\n{END}'
        exposed=block(1)+'\nint main(){return 0;}\n'
        seen=[]
        class Proposer:
            def __init__(self,*args):self.requests=1
            def check_connection(self):return ['mock']
            def generate(self,task):
                call=self.requests;self.requests+=1
                result={'call_id':call,'hypothesis':'mock','family':'mock'}
                if task['operation']=='parameter_defaults':result['code']=block(task['selected_values'][0])
                else:result.update(code=exposed,parameter_schema=schema)
                return result
        class Evaluator:
            def __init__(self,*args,seconds,**kwargs):self.seconds=seconds;self.arguments=()
            def evaluate(self,code,subset):
                seen.extend(c.name for c in subset)
                value=float(self.arguments[0]) if self.arguments else float(re.search(r'AAD_PARAM_0_DEFAULT = ([0-9.]+);',code).group(1))
                mean=.79+value*.01
                return {'valid':True,'mean':mean,'cases':[
                    {'name':c.name,'score':round(mean*1e9),'status':'AC','seconds':self.seconds,
                     'input_sha256':c.fingerprint,'stdout':'0 0 1 1\n'} for c in subset]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base='original API source';ident=digest(base)
            (root/'candidates'/ident).mkdir(parents=True)
            (root/'candidates'/ident/'solution.cpp').write_text(base)
            original={'valid':True,'mean':.8,'cases':[{'name':c.name,'score':800000000} for c in train]}
            save_json(root/'state.json',{'frozen':False,'events':[],
                      'records':{ident:{'call_id':0,'full':original,'family':'original','hypothesis':'original'}}})
            save_json(root/'config.json',{'max_requests':1})
            save_json(root/'protocol.json',{'train':[c.name for c in train],
                'validation':[c.name for c in validation],'screen':[c.name for c in train[:12]],
                'inputs':{c.name:c.fingerprint for c in cases}})
            with patch.object(sys,'argv',['parameter_search','--run-dir',directory,'--calls','3','--variants','12']),patch.object(module,'ProgramProposer',Proposer),patch.object(module,'CleanEvaluator',Evaluator),patch.object(module,'public_cases',return_value=(cases,{})):
                module.main()
            search=json.loads((root/'numeric_search/state.json').read_text())
            state=json.loads((root/'state.json').read_text())
            self.assertEqual(search['phase'],'complete')
            self.assertIn('installed',search)
            self.assertGreater(search['installed_mean'],.8)
            self.assertLessEqual(len(search['variants']),12)
            self.assertGreaterEqual(len(search['variants']),8)
            self.assertFalse(set(seen)&{c.name for c in validation})
            self.assertEqual(state['records'][search['installed']]['assembly']['type'],'parameter-block')
            self.assertFalse(state['frozen'])


if __name__=='__main__':unittest.main()
