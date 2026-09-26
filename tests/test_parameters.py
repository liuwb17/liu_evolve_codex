import importlib.util
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aad.io import digest,save_json
from aad.parameters import BEGIN,END,validate_schema,validate_block,assemble_parameters,decode,defaults_vector,differential_trial
from aad.v3_provider import PROBLEM


class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.schema=[{'name':'temperature','kind':'float','scale':'log','min':.001,'max':1.,'default':.1},
                     {'name':'count','kind':'int','scale':'linear','min':1,'max':9,'default':3}]

    def test_schema_and_default_round_trip(self):
        validate_schema(self.schema)
        decoded=decode(defaults_vector(self.schema),self.schema)
        self.assertAlmostEqual(float(decoded[0]),.1);self.assertEqual(decoded[1],'3')
        self.assertEqual(decode([0.,1.],self.schema),['0.001','9'])
        with self.assertRaises(ValueError):decode([.2],self.schema)
        bad=[{**self.schema[0],'min':0}]
        with self.assertRaises(ValueError):validate_schema(bad)

    def test_only_numeric_declarations_can_be_installed(self):
        old=f'{BEGIN}\nconstexpr double AAD_PARAM_0_DEFAULT = 0.1;\nconstexpr int AAD_PARAM_1_DEFAULT = 3;\n{END}'
        new=f'{BEGIN}\nconstexpr double AAD_PARAM_0_DEFAULT = 0.2;\nconstexpr int AAD_PARAM_1_DEFAULT = 5;\n{END}'
        parent='prefix\n'+old+'\nsuffix'
        validate_block(old,self.schema)
        result=assemble_parameters(parent,new,self.schema,['.2','5'])
        self.assertEqual(result,'prefix\n'+new+'\nsuffix')
        with self.assertRaises(ValueError):assemble_parameters(parent,new+'\nint other;',self.schema,['.2','5'])
        with self.assertRaises(ValueError):assemble_parameters(parent,new.replace('0.2','0.3'),self.schema,['.2','5'])
        with self.assertRaises(ValueError):validate_block(old.replace('constexpr int','constexpr double'),self.schema)

    def test_differential_trial_is_bounded_and_reproducible(self):
        population=[[.1,.2],[.3,.4],[.5,.6],[.8,.9]]
        a=differential_trial(population,0,random.Random(7))
        b=differential_trial(population,0,random.Random(7))
        self.assertEqual(a,b);self.assertTrue(all(0<=x<=1 for x in a));self.assertNotEqual(a,population[0])

    def test_audit_reconstructs_model_block_and_rejects_other_changes(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('parameter_audit_test',root/'scripts/v3_audit.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        schema=[{'name':'value','kind':'float','scale':'linear','min':0.,'max':2.,'default':1.}]
        block=f'{BEGIN}\nconstexpr double AAD_PARAM_0_DEFAULT = 1;\n{END}'
        parent=block+'\nint main(){return 0;}\n'
        replacement=block.replace('= 1;','= 1.5;')
        child=assemble_parameters(parent,replacement,schema,['1.5'])
        a,b=digest(parent),digest(child)
        assembly={'type':'parameter-block','parent':a,'schema':schema,'values':['1.5']}
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            save_json(folder/'state.json',{'records':{a:{'call_id':0},b:{'call_id':1,'assembly':assembly}}})
            tasks=[{'problem':PROBLEM,'operation':'independent_initial_design'},
                   {'problem':PROBLEM,'operation':'parameter_defaults','parent_source':parent,'parameter_schema':schema,'selected_values':['1.5']}]
            for i,(ident,code,response_code,task) in enumerate(zip([a,b],[parent,child],[parent,replacement],tasks)):
                path=folder/'candidates'/ident;path.mkdir(parents=True)
                (path/'solution.cpp').write_text(code,encoding='utf-8')
                save_json(folder/'llm'/f'{i:05d}.request.json',{'messages':[{}, {'content':json.dumps(task)}]})
                save_json(folder/'llm'/f'{i:05d}.response.json',{'choices':[{'message':{'content':json.dumps({'code':response_code})}}]})
            with patch.object(sys,'argv',['v3_audit','--run-dir',str(folder)]):
                module.main()
                audit=json.loads((folder/'source_audit.json').read_text())
                self.assertTrue(audit['all_sources_match_audited_origin'])
                self.assertFalse(audit['all_sources_match_api'])
                self.assertEqual(audit['model_written_parameter_blocks'],1)
                (folder/'candidates'/b/'solution.cpp').write_text(child.replace('return 0','return 1'),encoding='utf-8')
                with self.assertRaises(AssertionError):module.main()


if __name__=='__main__':unittest.main()
