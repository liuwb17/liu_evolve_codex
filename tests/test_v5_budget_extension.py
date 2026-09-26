import copy
import json
import unittest

from aad.io import digest
from scripts.v5_pro_256 import expanded_protocol


class BudgetExtensionTests(unittest.TestCase):
    def fixture(self):
        old = {'version': 5, 'config': {'calls': 64, 'model': 'deepseek-v4-pro',
                                      'repeats': 3}, 'train': ['a'], 'validation': ['b']}
        flash = copy.deepcopy(old)
        flash['config'].update(calls=256, model='deepseek-flash')
        state = {'signature': digest(json.dumps(old, sort_keys=True)), 'pending': None}
        return old, flash, state, [{'status': 'ok'}] * 8

    def test_only_budget_changes_and_ledger_preserved(self):
        old, flash, state, ledger = self.fixture()
        result = expanded_protocol(old, flash, state, ledger)
        expected = copy.deepcopy(old)
        expected['config']['calls'] = 256
        self.assertEqual(result, expected)
        self.assertEqual(old['config']['calls'], 64)
        self.assertEqual(len(ledger), 8)

    def test_reject_mismatched_flash(self):
        old, flash, state, ledger = self.fixture()
        flash['config']['repeats'] = 1
        with self.assertRaises(ValueError):
            expanded_protocol(old, flash, state, ledger)

    def test_reject_active_or_closed_development(self):
        for field in ('frozen', 'validation_started', 'development_closed', 'pending'):
            old, flash, state, ledger = self.fixture()
            state[field] = True
            with self.assertRaises(ValueError):
                expanded_protocol(old, flash, state, ledger)
        old, flash, state, ledger = self.fixture()
        ledger.append({'status': 'started'})
        with self.assertRaises(ValueError):
            expanded_protocol(old, flash, state, ledger)


if __name__ == '__main__':
    unittest.main()
