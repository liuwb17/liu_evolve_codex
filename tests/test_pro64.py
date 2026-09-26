import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aad.io import save_json
from scripts import pro64_experiments as pro


class Pro64Tests(unittest.TestCase):
    def test_legacy_gate_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'phase.json'
            self.assertFalse(pro.legacy_finished(path))
            for phase in ('evolution', 'failed', 'cooperatively_stopped'):
                save_json(path, {'phase': phase})
                self.assertFalse(pro.legacy_finished(path))
            save_json(path, {'phase': 'completed'})
            self.assertTrue(pro.legacy_finished(path))

    def test_cross_process_exclusion_and_exception_release(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / 'lock'
            probe = """
import os, sys
f = open(sys.argv[1], 'r+b')
try:
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(7)
"""
            def attempt():
                return subprocess.run([sys.executable, '-c', probe, str(lock)],
                                      capture_output=True).returncode
            with self.assertRaisesRegex(RuntimeError, 'test'):
                with pro.exclusive_file(lock):
                    self.assertEqual(attempt(), 7)
                    raise RuntimeError('test')
            self.assertEqual(attempt(), 0)

    def test_launch_refuses_existing_run_before_reading_key(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(pro, 'paths', return_value=(Path(temp), Path(temp) / 'out')):
                with patch.object(pro.getpass, 'getpass') as prompt:
                    with self.assertRaisesRegex(ValueError, 'already exists'):
                        pro.launch_pair()
                    prompt.assert_not_called()

    def test_worker_preserves_model_budget_and_original_entry(self):
        from aad.v3_evaluator import CleanEvaluator
        original = CleanEvaluator.evaluate
        argv = sys.argv
        try:
            with tempfile.TemporaryDirectory() as temp:
                with patch.object(pro, 'paths', return_value=(Path(temp), Path(temp) / 'out')):
                    with patch.object(pro.runpy, 'run_path') as invoke:
                        pro.worker(4, 'evolution')
                        self.assertIn('deepseek-v4-pro', sys.argv)
                        self.assertEqual(sys.argv[sys.argv.index('--calls') + 1], '64')
                        self.assertNotIn('--resume', sys.argv)
                        self.assertTrue(invoke.call_args.args[0].endswith('v4_search.py'))
        finally:
            CleanEvaluator.evaluate = original
            sys.argv = argv


if __name__ == '__main__':
    unittest.main()
