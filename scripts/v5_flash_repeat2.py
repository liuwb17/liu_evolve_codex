"""Independent v5 Flash replication with the original development protocol."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from aad.io import read_json, save_json
from pro64_experiments import evaluation_slot, exclusive_file

SCRIPT = Path(__file__).resolve()
RUN = ROOT / 'runs/v5_flash_256_repeat2_20260925'
OUT = ROOT / 'runs/v5_flash_256_repeat2_system_1000_20260925'
REFERENCE = ROOT / 'runs/v5_flash_256_20260924/protocol.json'


def verify_protocol(config, train, validation, expected):
    actual = {'version': 5, 'config': config,
              'inputs': {c.name: c.fingerprint for c in [*train, *validation]},
              'train': [c.name for c in train], 'validation': [c.name for c in validation]}
    if actual != expected:
        raise ValueError('Replication protocol differs from original Flash experiment')


def policy():
    return {'version': 5, 'replicate': 2, 'model': 'deepseek-flash',
            'max_requests': 256, 'token_cap': None, 'train_count': 40,
            'validation_count': 10, 'private_count': 1000, 'search_seconds': 4.8,
            'hard_seconds': 4.95, 'workers': 6, 'cpu_per_case': 1, 'memory_mib': 512,
            'cold_start': True, 'old_solver_sources_supplied': False,
            'private_feedback_supplied': False, 'one_call_per_proposal': True,
            'human_baseline': 'runs/v3_kusano_private_20260922',
            'resource_policy': 'Shared exclusive evaluation lock with Pro; parallel API',
            'reference_protocol_sha256': hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
            'wrapper_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (SCRIPT, ROOT / 'scripts/v5_search.py', ROOT / 'scripts/pro64_experiments.py')}}


def worker(stage):
    if stage in ('evolution', 'finalize'):
        import v5_search
        from aad.v5_engine import V5Engine
        from aad.v3_evaluator import CleanEvaluator
        expected = read_json(REFERENCE)
        original_evaluate = CleanEvaluator.evaluate

        def guarded(self, code, cases):
            with evaluation_slot(RUN):
                return original_evaluate(self, code, cases)

        class VerifiedEngine(V5Engine):
            def __init__(self, root, provider, evaluate, train, validation, config):
                # Before any proposal/API call; no prior solver or scores are loaded.
                verify_protocol(config, train, validation, expected)
                super().__init__(root, provider, evaluate, train, validation, config)

        CleanEvaluator.evaluate = guarded
        v5_search.V5Engine = VerifiedEngine
        sys.argv = ['v5_search.py', '--run-dir', str(RUN), '--calls', '256',
                    '--token-budget', '0', '--model', 'deepseek-flash']
        if (RUN / 'state.json').exists():
            sys.argv.append('--resume')
        if stage == 'finalize':
            sys.argv.append('--finalize')
        v5_search.main()
    elif stage == 'audit':
        sys.argv = ['v5_audit.py', '--run-dir', str(RUN)]
        runpy.run_path(str(ROOT / 'scripts/v5_audit.py'), run_name='__main__')
    else:
        with evaluation_slot(RUN):
            sys.argv = ['v5_system_test.py', '--run-dir', str(RUN), '--output', str(OUT),
                        '--paired-baseline', str(ROOT / 'runs/v3_kusano_private_20260922')]
            runpy.run_path(str(ROOT / 'scripts/v5_system_test.py'), run_name='__main__')


def experiment():
    with exclusive_file(RUN / 'controller.lock'):
        expected = policy()
        file = RUN / 'orchestration.json'
        if file.exists():
            old = read_json(file)
            if old['policy'] != expected:
                raise ValueError('Experiment policy changed')
            if old['phase'] == 'completed':
                return

        def phase(name, **extra):
            save_json(file, {'policy': expected, 'phase': name, **extra})
            print(name, extra, flush=True)

        def invoke(stage):
            with (RUN / 'console.log').open('a', encoding='utf-8') as log:
                result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--worker', stage],
                                        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                phase('failed', step=stage, returncode=result.returncode)
                raise RuntimeError('Worker failed: ' + stage)

        state = read_json(RUN / 'state.json') if (RUN / 'state.json').exists() else {}
        if not any(state.get(k) for k in ('frozen', 'validation_started', 'development_closed')):
            if not os.environ.get('AAD_API_KEY'):
                raise ValueError('AAD_API_KEY required')
            phase('evolution')
            invoke('evolution')
            if (RUN / 'STOP_AFTER_CURRENT').exists():
                phase('cooperatively_stopped')
                return
        if not read_json(RUN / 'state.json').get('frozen'):
            phase('public_validation_and_freeze')
            invoke('finalize')
        phase('source_audit')
        invoke('audit')
        phase('private_1000_terminal_evaluation')
        invoke('terminal')
        phase('completed')


def require_fresh(run, out):
    if run.exists() or out.exists():
        raise ValueError('Replication already exists; use explicit resume, not a new launch')


def launch():
    require_fresh(RUN, OUT)
    key = os.environ.get('AAD_API_KEY') or getpass.getpass('AAD API key (hidden): ')
    if not key.strip():
        raise ValueError('Empty key')
    RUN.mkdir(parents=True, exist_ok=False)
    save_json(RUN / 'orchestration.json', {'policy': policy(), 'phase': 'launching'})
    with (RUN / 'launcher.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT)], cwd=ROOT,
            env=dict(os.environ, AAD_API_KEY=key), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    save_json(RUN / 'launch.json', {'pid': child.pid, 'max_requests': 256,
              'model': 'deepseek-flash', 'replicate': 2,
              'launched_at_utc': datetime.now(timezone.utc).isoformat()})
    print('Launched independent v5 Flash repeat 2; PID', child.pid, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--launch', action='store_true')
    modes.add_argument('--worker', choices=('evolution', 'finalize', 'audit', 'terminal'))
    args = parser.parse_args()
    if args.launch:
        launch()
    elif args.worker:
        worker(args.worker)
    else:
        experiment()
