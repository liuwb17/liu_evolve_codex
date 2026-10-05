import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aad.io import digest, read_json, save_json

spec = importlib.util.spec_from_file_location('migration', Path(__file__).resolve().parents[1] / 'scripts/v52_migrate_sync.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def setup_suite(self, folder):
        root = Path(folder)
        project = root / 'project'
        for name in ('aad', 'scripts', 'tests', 'docs'):
            (project / name).mkdir(parents=True)
        for name in ('AGENTS.md', 'README.md', 'pyproject.toml', 'aad/example.py', 'scripts/v5_1_search.py'):
            (project / name).write_text('snapshot')
        source, destination = root / 'old', root / 'new'
        run = source / 'task'
        run.mkdir(parents=True)
        manifest = {'cases': [{'seed': 0}]}
        runtime = {'sdk_sha256': 'unchanged', 'docker_images': {'test': 'same'}}
        protocol = {'version': '5.2', 'config': {'runtime': runtime,
                    'task_sha256': digest(json.dumps(manifest, sort_keys=True)), 'implementation_hashes': {}}}
        code = 'int main() {}\n'
        ident = digest(code)
        save_json(run / 'protocol.json', protocol)
        save_json(run / 'state.json', {'signature': digest(json.dumps(protocol, sort_keys=True)),
            'frozen': False, 'records': {ident: {'smoke': {'valid': True}, 'evaluations': []}},
            'parallel_batch': {'tasks': [{'implementation_call': 0, 'candidate': ident}]}})
        save_json(run / 'llm/ledger.json', [{'id': 0, 'status': 'ok'}])
        save_json(run / 'llm/00000.response.json', {'unchanged': True})
        save_json(run / 'planning/llm/ledger.json', [{'id': 0, 'status': 'ok'}])
        (run / 'candidates' / ident).mkdir(parents=True)
        (run / 'candidates' / ident / 'solution.cpp').write_text(code)
        (run / 'STOP_AFTER_CURRENT').touch()
        (source / 'STOP_AFTER_CURRENT').touch()
        save_json(source / 'schedule.json', {'tasks': ['task'], 'task_roots': {'task': str(root / 'data')}})
        save_json(source / 'status.json', {'processes': {'task': {'exit_code': 9}}})
        return project, source, destination, manifest, runtime

    def test_recovery_preserves_paid_responses_pending_work_and_original(self):
        with tempfile.TemporaryDirectory() as folder:
            project, source, destination, manifest, runtime = self.setup_suite(folder)
            before = (source / 'task/state.json').read_bytes()
            with patch.object(migration, 'ROOT', project), patch.object(migration, 'load_task', return_value=(manifest, [])), \
                    patch.object(migration, 'runtime_identity', return_value={**runtime, 'execution_protocol': 'fixed'}):
                audit = migration.migrate(source, destination)
            self.assertEqual((source / 'task/state.json').read_bytes(), before)
            self.assertEqual((source / 'task/llm/00000.response.json').read_bytes(),
                             (destination / 'task/llm/00000.response.json').read_bytes())
            state = read_json(destination / 'task/state.json')
            self.assertEqual(state['parallel_batch'], json.loads(before)['parallel_batch'])
            self.assertEqual(state['records'], json.loads(before)['records'])
            self.assertNotEqual(state['signature'], json.loads(before)['signature'])
            self.assertEqual(audit['tasks']['task']['solve_requests'], 1)
            self.assertFalse((destination / 'task/STOP_AFTER_CURRENT').exists())

    def test_unrelated_runtime_change_rejected_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            project, source, destination, manifest, runtime = self.setup_suite(folder)
            with patch.object(migration, 'ROOT', project), patch.object(migration, 'load_task', return_value=(manifest, [])), \
                    patch.object(migration, 'runtime_identity', return_value={**runtime, 'sdk_sha256': 'changed'}):
                with self.assertRaisesRegex(ValueError, 'beyond'):
                    migration.migrate(source, destination)
            self.assertFalse(destination.exists())

    def test_active_source_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            _, source, destination, _, _ = self.setup_suite(folder)
            save_json(source / 'status.json', {'processes': {'task': {'exit_code': None}}})
            with self.assertRaisesRegex(ValueError, 'Stop all'):
                migration.migrate(source, destination)
