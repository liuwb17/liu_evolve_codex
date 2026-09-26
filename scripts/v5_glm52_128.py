"""Independent GLM-5.2 v5 experiment, 128 requests including failures."""
import argparse
import copy
from datetime import datetime, timezone
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
from aad.provider import environment
from aad.v5_glm_provider import ENDPOINT, MODEL, GLM5Provider, IDLE_TIMEOUT, TOTAL_TIMEOUT
from pro64_experiments import evaluation_slot, exclusive_file

SCRIPT = Path(__file__).resolve()
RUN = ROOT / 'runs/v5_glm52_128_20260926'
OUT = ROOT / 'runs/v5_glm52_128_system_1000_20260926'
REFERENCE = ROOT / 'runs/v5_flash_256_20260924/protocol.json'
MAX_CALLS = 128


def verify_protocol(config, train, validation, reference):
    expected = copy.deepcopy(reference)
    expected['config'].update(model=MODEL, calls=MAX_CALLS)
    actual = {'version': 5, 'config': config,
              'inputs': {c.name: c.fingerprint for c in [*train, *validation]},
              'train': [c.name for c in train], 'validation': [c.name for c in validation]}
    if actual != expected:
        raise ValueError('Core v5 protocol differs beyond authorized model/request budget')


def transport_policy():
    return {'endpoint': ENDPOINT, 'model': MODEL, 'key_environment': 'ZHIPU_API_KEY',
            'max_tokens': 65536, 'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low',
            'documented_glm_effort_mapping': 'low maps to high; not equivalent DeepSeek compute',
            'stream': True, 'idle_timeout_seconds': IDLE_TIMEOUT,
            'total_timeout_seconds': TOTAL_TIMEOUT, 'automatic_retries': 0,
            'http_or_network_error': 'stop experiment; count request; retain checkpoint',
            'returned_model_policy': 'require glm-5.2, case insensitive',
            'adapter_sha256': hashlib.sha256((ROOT / 'aad/v5_glm_provider.py').read_bytes()).hexdigest(),
            'stream_parser_sha256': hashlib.sha256((ROOT / 'aad/glm_stream.py').read_bytes()).hexdigest()}


def policy():
    return {'version': 5, 'model': MODEL, 'max_requests': MAX_CALLS,
            'token_cap': None, 'train_count': 40, 'validation_count': 10,
            'private_count': 1000, 'search_seconds': 4.8, 'hard_seconds': 4.95,
            'workers': 6, 'cpu_per_case': 1, 'memory_mib': 512,
            'cold_start': True, 'old_solver_sources_supplied': False,
            'private_feedback_supplied': False, 'one_call_per_proposal': True,
            'human_baseline': 'runs/v3_kusano_private_20260922',
            'resource_policy': 'Shared exclusive evaluation lock; other runs remain paused',
            'transport': transport_policy(),
            'reference_protocol_sha256': hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
            'wrapper_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (SCRIPT, ROOT / 'scripts/v5_search.py', ROOT / 'scripts/pro64_experiments.py')}}


def worker(stage):
    if stage in ('evolution', 'finalize'):
        import v5_search
        from aad.v5_engine import V5Engine
        from aad.v3_evaluator import CleanEvaluator
        reference = read_json(REFERENCE)
        original_evaluate = CleanEvaluator.evaluate

        def guarded(self, code, cases):
            with evaluation_slot(RUN):
                return original_evaluate(self, code, cases)

        class VerifiedEngine(V5Engine):
            def __init__(self, root, provider, evaluate, train, validation, config):
                verify_protocol(config, train, validation, reference)
                config = {**config, 'glm_transport': transport_policy()}
                super().__init__(root, provider, evaluate, train, validation, config)

        CleanEvaluator.evaluate = guarded
        v5_search.V5Provider = GLM5Provider
        v5_search.V5Engine = VerifiedEngine
        sys.argv = ['v5_search.py', '--run-dir', str(RUN), '--calls', str(MAX_CALLS),
                    '--token-budget', '0', '--model', MODEL]
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

        if (RUN / 'STOP_AFTER_CURRENT').exists():
            phase('cooperatively_stopped')
            return
        state = read_json(RUN / 'state.json') if (RUN / 'state.json').exists() else {}
        if not any(state.get(k) for k in ('frozen', 'validation_started', 'development_closed')):
            if not environment('ZHIPU_API_KEY'):
                raise ValueError('ZHIPU_API_KEY required')
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


def preflight():
    # Read only protocol and public cases, never prior sources or private scores.
    from v3_search import public_cases
    reference = read_json(REFERENCE)
    config = copy.deepcopy(reference['config'])
    config.update(model=MODEL, calls=MAX_CALLS)
    config['image'] = subprocess.run(['docker', 'image', 'inspect', 'mosaic-cpp:1', '--format', '{{.Id}}'],
        capture_output=True, text=True, check=True).stdout.strip()
    config['implementation_hashes'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                      for name in config['implementation_hashes']}
    cases, _ = public_cases()
    verify_protocol(config, [c for i, c in enumerate(cases) if i % 5 != 4],
                    [c for i, c in enumerate(cases) if i % 5 == 4], reference)
    if not environment('ZHIPU_API_KEY'):
        raise ValueError('ZHIPU_API_KEY required')
    print('Preflight OK: unchanged v5 core, official public 40/10 split, matching image; key present.', flush=True)


def launch():
    if RUN.exists() or OUT.exists():
        raise ValueError('Existing GLM experiment; refuse fresh launch over history')
    preflight()
    key = environment('ZHIPU_API_KEY')
    RUN.mkdir(parents=True, exist_ok=False)
    save_json(RUN / 'orchestration.json', {'policy': policy(), 'phase': 'launching'})
    launch_background(key, 'launch.json')


def launch_background(key, receipt):
    child_env = dict(os.environ, ZHIPU_API_KEY=key, PYTHONUNBUFFERED='1')
    child_env.pop('AAD_API_KEY', None)
    with (RUN / 'launcher.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT)], cwd=ROOT,
            env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    save_json(RUN / receipt, {'pid': child.pid, 'model': MODEL, 'max_requests': MAX_CALLS,
              'launched_at_utc': datetime.now(timezone.utc).isoformat()})
    print('Launched GLM-5.2 v5, 128 total requests; PID', child.pid, flush=True)


def resume_background():
    preflight()
    if (RUN / 'STOP_AFTER_CURRENT').exists():
        raise ValueError('Explicitly paused; do not auto-remove stop marker')
    checkpoint = read_json(RUN / 'orchestration.json')
    if checkpoint['policy'] != policy():
        raise ValueError('Migrate transport before resume')
    if checkpoint['phase'] not in ('failed', 'transport_repaired_ready', 'cooperatively_stopped'):
        raise ValueError('Run is not in a resumable stopped phase')
    launch_background(environment('ZHIPU_API_KEY'), 'resume_stream.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--launch', action='store_true')
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--resume-background', action='store_true')
    modes.add_argument('--worker', choices=('evolution', 'finalize', 'audit', 'terminal'))
    args = parser.parse_args()
    if args.launch:
        launch()
    elif args.preflight:
        preflight()
    elif args.resume_background:
        resume_background()
    elif args.worker:
        worker(args.worker)
    else:
        experiment()
