import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from scripts.v5_flash_repeat2 import require_fresh, verify_protocol


class ReplicationTests(unittest.TestCase):
    def fixture(self):
        train = [SimpleNamespace(name='a', fingerprint='hash-a')]
        validation = [SimpleNamespace(name='b', fingerprint='hash-b')]
        config = {'model': 'deepseek-flash', 'calls': 256, 'repeats': 3}
        expected = {'version': 5, 'config': copy.deepcopy(config),
                    'inputs': {'a': 'hash-a', 'b': 'hash-b'},
                    'train': ['a'], 'validation': ['b']}
        return config, train, validation, expected

    def test_exact_protocol_acceptance(self):
        verify_protocol(*self.fixture())

    def test_model_budget_and_input_changes_rejected(self):
        for key, value in (('model', 'deepseek-v4-pro'), ('calls', 64), ('repeats', 1)):
            config, train, validation, expected = self.fixture()
            config[key] = value
            with self.assertRaises(ValueError):
                verify_protocol(config, train, validation, expected)
        config, train, validation, expected = self.fixture()
        train[0].fingerprint = 'different-input'
        with self.assertRaises(ValueError):
            verify_protocol(config, train, validation, expected)

    def test_fresh_launch_rejects_either_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            require_fresh(root / 'new-run', root / 'new-output')
            with self.assertRaises(ValueError):
                require_fresh(root, root / 'new-output')
            with self.assertRaises(ValueError):
                require_fresh(root / 'new-run', root)


if __name__ == '__main__':
    unittest.main()
