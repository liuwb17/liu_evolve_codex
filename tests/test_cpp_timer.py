import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aad.cpp import CppEvaluator
from aad.problem import Case


class CppTimerTests(unittest.TestCase):
    def evaluator(self,root):
        evaluator=CppEvaluator.__new__(CppEvaluator)
        evaluator.root=Path(root)
        evaluator.workers=1
        evaluator.seconds=3.5
        evaluator.image='test-image'
        evaluator.container_timer=True
        evaluator.compile=lambda code:(Path(root),None)
        return evaluator

    def test_inner_time_and_outer_time_are_separate(self):
        inner={'status':'AC','stdout':'0 0 1 1\n','stderr':'','seconds':3.51,'returncode':0}
        outer={'status':'AC','stdout':json.dumps(inner),'stderr':'','seconds':7.0,'returncode':0}
        with tempfile.TemporaryDirectory() as root, patch('aad.cpp.execute',return_value=outer) as execute, patch('aad.cpp.subprocess.run'):
            result=self.evaluator(root).evaluate('source',[Case('one','1\n0 0 1\n')])
            self.assertTrue(result['valid'])
            self.assertEqual(result['cases'][0]['seconds'],3.51)
            self.assertEqual(result['cases'][0]['container_wall_seconds'],7.0)
            self.assertIn('/harness/cpp_case_driver.py',execute.call_args.args[0])
            self.assertEqual(execute.call_args.args[3],30.0)

    def test_inner_timeout_is_not_accepted(self):
        inner={'status':'TLE','stdout':'0 0 1 1\n','stderr':'','seconds':5.01,'returncode':-9}
        outer={'status':'AC','stdout':json.dumps(inner),'stderr':'','seconds':6.0,'returncode':0}
        with tempfile.TemporaryDirectory() as root, patch('aad.cpp.execute',return_value=outer), patch('aad.cpp.subprocess.run'):
            result=self.evaluator(root).evaluate('source',[Case('one','1\n0 0 1\n')])
            self.assertFalse(result['valid'])
            self.assertEqual(result['cases'][0]['score'],0)
            self.assertEqual(result['cases'][0]['status'],'TLE')

    def test_outer_failure_is_infrastructure_error(self):
        outer={'status':'TLE','stdout':'','stderr':'','seconds':30.01,'returncode':-9}
        with tempfile.TemporaryDirectory() as root, patch('aad.cpp.execute',return_value=outer), patch('aad.cpp.subprocess.run'):
            result=self.evaluator(root).evaluate('source',[Case('one','1\n0 0 1\n')])
            self.assertFalse(result['valid'])
            self.assertEqual(result['cases'][0]['status'],'INFRA_ERROR')


if __name__=='__main__':unittest.main()
