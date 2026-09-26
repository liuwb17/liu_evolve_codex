"""Two fresh Pro experiments; API work overlaps, timed evaluations are exclusive.

Does not modify the version-pinned v4/v5 implementations. The active legacy
Flash experiment is allowed to finish before either new run starts evaluation.
Credentials are inherited in memory only, never written to run artifacts.
"""
import argparse
from contextlib import contextmanager
import getpass
import hashlib
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from aad.io import read_json, save_json

LEGACY = ROOT / 'runs/v5_flash_256_20260924/orchestration.json'
LOCK = ROOT / 'runs/pro64_20260925_evaluation.lock'
SCRIPT = Path(__file__).resolve()


@contextmanager
def exclusive_file(path):
    """OS-managed lock: automatically released when the owner exits/crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        while True:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                time.sleep(1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def legacy_finished(path=LEGACY):
    # Fail closed, including a failed/interrupted legacy run: no silent overlap.
    return path.exists() and read_json(path).get('phase') == 'completed'


@contextmanager
def evaluation_slot(run):
    resource = run / 'resource_status.json'
    save_json(resource, {'status': 'waiting_for_legacy_completion', 'legacy': str(LEGACY)})
    while not legacy_finished():
        time.sleep(5)
    save_json(resource, {'status': 'waiting_for_evaluation_lock'})
    with exclusive_file(LOCK):
        save_json(resource, {'status': 'evaluating', 'pid': os.getpid()})
        try:
            yield
        finally:
            save_json(resource, {'status': 'idle'})


def paths(version):
    return (ROOT / f'runs/v{version}_pro_64_20260925',
            ROOT / f'runs/v{version}_pro_64_system_1000_20260925')


def worker(version, stage):
    run, out = paths(version)
    common = ['--run-dir', str(run), '--calls', '64', '--token-budget', '0',
              '--model', 'deepseek-v4-pro']
    if stage in ('evolution', 'finalize'):
        from aad.v3_evaluator import CleanEvaluator
        original = CleanEvaluator.evaluate

        def guarded(self, code, cases):
            with evaluation_slot(run):
                return original(self, code, cases)

        CleanEvaluator.evaluate = guarded
        if (run / 'state.json').exists():
            common.append('--resume')
        if stage == 'finalize':
            common.append('--finalize')
        sys.argv = [f'v{version}_search.py', *common]
        runpy.run_path(str(ROOT / 'scripts' / sys.argv[0]), run_name='__main__')
    elif stage == 'audit':
        sys.argv = [f'v{version}_audit.py', '--run-dir', str(run)]
        runpy.run_path(str(ROOT / 'scripts' / sys.argv[0]), run_name='__main__')
    else:
        # Includes generation checks, compilation, solver work and Rust rescore.
        with evaluation_slot(run):
            sys.argv = [f'v{version}_system_test.py', '--run-dir', str(run),
                        '--output', str(out), '--paired-baseline',
                        str(ROOT / 'runs/v3_kusano_private_20260922')]
            runpy.run_path(str(ROOT / 'scripts' / sys.argv[0]), run_name='__main__')


def experiment(version):
    run, out = paths(version)
    run.mkdir(parents=True, exist_ok=True)
    policy = {'version': version, 'model': 'deepseek-v4-pro', 'max_requests': 64,
              'token_cap': None, 'train_count': 40, 'validation_count': 10,
              'private_count': 1000, 'search_seconds': 4.8, 'hard_seconds': 4.95,
              'workers': 6, 'cpu_per_case': 1, 'memory_mib': 512,
              'cold_start': True, 'old_sources_supplied': False,
              'private_feedback_supplied': False,
              'human_baseline': 'runs/v3_kusano_private_20260922',
              'resource_policy': 'parallel API; exclusive evaluation after legacy completes',
              'wrapper_sha256': hashlib.sha256(SCRIPT.read_bytes()).hexdigest()}
    file = run / 'orchestration.json'
    if file.exists():
        old = read_json(file)
        if old['policy'] != policy:
            raise ValueError('Policy changed')
        if old['phase'] == 'completed':
            return

    def phase(name, **extra):
        save_json(file, {'policy': policy, 'phase': name, **extra})
        print(name, extra, flush=True)

    def invoke(stage):
        with (run / 'console.log').open('a', encoding='utf-8') as log:
            process = subprocess.run([sys.executable, '-B', str(SCRIPT),
                                      '--version', str(version), '--worker', stage],
                                     cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if process.returncode:
            phase('failed', step=stage, returncode=process.returncode)
            raise RuntimeError(f'{stage} failed; see console.log')

    state = read_json(run / 'state.json') if (run / 'state.json').exists() else {}
    if not any(state.get(k) for k in ('frozen', 'validation_started', 'development_closed')):
        if not os.environ.get('AAD_API_KEY'):
            raise ValueError('AAD_API_KEY required')
        phase('evolution')
        invoke('evolution')
        if (run / 'STOP_AFTER_CURRENT').exists():
            phase('cooperatively_stopped')
            return
    if not read_json(run / 'state.json').get('frozen'):
        phase('public_validation_and_freeze')
        invoke('finalize')
    phase('source_audit')
    invoke('audit')
    phase('private_1000_terminal_evaluation')
    invoke('terminal')
    phase('completed', output=str(out))


def launch_pair():
    # Refuse to duplicate an existing experiment or silently append paid calls.
    for version in (4, 5):
        run, out = paths(version)
        if run.exists() or out.exists():
            raise ValueError(f'Experiment already exists: {run}')
    key = os.environ.get('AAD_API_KEY') or getpass.getpass('AAD API key (hidden): ')
    if not key.strip():
        raise ValueError('Empty API key')
    env = dict(os.environ, AAD_API_KEY=key)
    for version in (4, 5):
        run, _ = paths(version)
        run.mkdir(parents=True)
        with (run / 'launcher.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, '-B', str(SCRIPT), '--version', str(version)],
                                     cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        save_json(run / 'launch.json', {'pid': child.pid, 'version': version,
                                      'model': 'deepseek-v4-pro', 'max_requests': 64})
        print(f'Launched v{version}: PID {child.pid}, {run}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--launch-pair', action='store_true')
    parser.add_argument('--version', type=int, choices=(4, 5))
    parser.add_argument('--worker', choices=('evolution', 'finalize', 'audit', 'terminal'))
    args = parser.parse_args()
    if args.launch_pair:
        if args.version or args.worker:
            parser.error('--launch-pair cannot be combined with worker options')
        launch_pair()
    elif args.version:
        if args.worker:
            worker(args.version, args.worker)
        else:
            experiment(args.version)
    else:
        parser.error('Choose --launch-pair or --version')
