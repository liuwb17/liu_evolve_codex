"""Audited 64 -> 256 request extension; preserve the existing cold-start lineage."""
import argparse
import copy
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from aad.io import digest, read_json, save_json
from pro64_experiments import evaluation_slot

# Keep the existing directory so sources, response IDs and cached measurements survive.
RUN = ROOT / 'runs/v5_pro_64_20260925'
OUT = ROOT / 'runs/v5_pro_256_system_1000_20260925'
FLASH = ROOT / 'runs/v5_flash_256_20260924'
SCRIPT = Path(__file__).resolve()


def expanded_protocol(old, flash, state, ledger):
    if old['config']['calls'] != 64 or old['config']['model'] != 'deepseek-v4-pro':
        raise ValueError('Expected original 64-call Pro protocol')
    if state['signature'] != digest(json.dumps(old, sort_keys=True)):
        raise ValueError('Original protocol signature mismatch')
    if any(state.get(k) for k in ('frozen', 'validation_started', 'development_closed', 'pending')):
        raise ValueError('Require a completed proposal boundary before validation')
    if len(ledger) > 64 or any(r['status'] == 'started' for r in ledger):
        raise ValueError('Unresolved or over-budget original ledger')
    new = copy.deepcopy(old)
    new['config']['calls'] = 256
    expected = copy.deepcopy(flash)
    expected['config']['model'] = 'deepseek-v4-pro'
    if new != expected:
        raise ValueError('Pro and Flash protocols differ beyond the model and authorized budget')
    return new


def policy():
    old = read_json(RUN / 'budget_256_migration' / 'orchestration.before.json')['policy']
    return {**old, 'max_requests': 256,
            'wrapper_sha256': hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            'budget_extension_receipt': 'budget_256_migration/receipt.json',
            'output_directory': str(OUT)}


def upgrade():
    orchestration = read_json(RUN / 'orchestration.json')
    if orchestration['phase'] != 'cooperatively_stopped':
        raise ValueError('Original orchestrator has not safely stopped')
    old = read_json(RUN / 'protocol.json')
    state = read_json(RUN / 'state.json')
    ledger_file = RUN / 'llm/ledger.json'
    ledger_hash = hashlib.sha256(ledger_file.read_bytes()).hexdigest()
    ledger = read_json(ledger_file)
    new = expanded_protocol(old, read_json(FLASH / 'protocol.json'), state, ledger)
    for name, expected_hash in new['config']['implementation_hashes'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected_hash:
            raise ValueError('Pinned implementation changed: ' + name)
    archive = RUN / 'budget_256_migration'
    archive.mkdir(exist_ok=False)
    for name in ('protocol', 'state', 'orchestration'):
        shutil.copyfile(RUN / (name + '.json'), archive / (name + '.before.json'))
    shutil.copyfile(ledger_file, archive / 'ledger.before.json')
    new_state = {**state, 'signature': digest(json.dumps(new, sort_keys=True))}
    save_json(archive / 'receipt.json', {
        'authorized_at_utc': datetime.now(timezone.utc).isoformat(),
        'reason': 'User requested v5 Pro total 256 calls and same settings as v5 Flash except model',
        'before_calls': 64, 'after_calls': 256, 'requests_already_used': len(ledger),
        'before_signature': state['signature'], 'after_signature': new_state['signature'],
        'ledger_sha256': ledger_hash, 'only_protocol_change': 'config.calls',
        'only_difference_from_flash_protocol': 'config.model',
        'sources_and_measurements_preserved': True, 'fresh_restart': False,
        'resource_note': 'Shared exclusive evaluation lock with v4 Pro; solver resources unchanged'})
    save_json(RUN / 'protocol.json', new)
    save_json(RUN / 'state.json', new_state)
    save_json(RUN / 'orchestration.json', {'policy': policy(), 'phase': 'budget_extended_ready'})
    if hashlib.sha256(ledger_file.read_bytes()).hexdigest() != ledger_hash:
        raise ValueError('Ledger changed during migration')
    (RUN / 'STOP_AFTER_CURRENT').rename(archive / 'STOP_AFTER_CURRENT.before')
    print('Budget upgraded; preserved', len(ledger), 'requests; only model differs from Flash protocol', flush=True)


def worker(stage):
    if stage in ('evolution', 'finalize'):
        from aad.v3_evaluator import CleanEvaluator
        original = CleanEvaluator.evaluate

        def guarded(self, code, cases):
            with evaluation_slot(RUN):
                return original(self, code, cases)

        CleanEvaluator.evaluate = guarded
        sys.argv = ['v5_search.py', '--run-dir', str(RUN), '--calls', '256',
                    '--token-budget', '0', '--model', 'deepseek-v4-pro', '--resume']
        if stage == 'finalize':
            sys.argv.append('--finalize')
        runpy.run_path(str(ROOT / 'scripts/v5_search.py'), run_name='__main__')
    elif stage == 'audit':
        sys.argv = ['v5_audit.py', '--run-dir', str(RUN)]
        runpy.run_path(str(ROOT / 'scripts/v5_audit.py'), run_name='__main__')
    else:
        with evaluation_slot(RUN):
            sys.argv = ['v5_system_test.py', '--run-dir', str(RUN), '--output', str(OUT),
                        '--paired-baseline', str(ROOT / 'runs/v3_kusano_private_20260922')]
            runpy.run_path(str(ROOT / 'scripts/v5_system_test.py'), run_name='__main__')


def experiment():
    expected = policy()
    old = read_json(RUN / 'orchestration.json')
    if old['policy'] != expected:
        raise ValueError('Experiment policy changed')
    if old['phase'] == 'completed':
        return

    def phase(name, **extra):
        save_json(RUN / 'orchestration.json', {'policy': expected, 'phase': name, **extra})
        print(name, extra, flush=True)

    def invoke(stage):
        with (RUN / 'console.log').open('a', encoding='utf-8') as log:
            result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--worker', stage],
                                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            phase('failed', step=stage, returncode=result.returncode)
            raise RuntimeError('Worker failed: ' + stage)

    state = read_json(RUN / 'state.json')
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


def upgrade_and_launch():
    key = os.environ.get('AAD_API_KEY') or getpass.getpass('AAD API key (hidden): ')
    if not key.strip():
        raise ValueError('Empty key')
    while read_json(RUN / 'orchestration.json')['phase'] != 'cooperatively_stopped':
        current = read_json(RUN / 'orchestration.json')['phase']
        if current != 'evolution':
            raise ValueError('Unexpected phase while waiting: ' + current)
        print('Waiting for current proposal to finish safely...', flush=True)
        time.sleep(15)
    upgrade()
    with (RUN / 'launcher_256.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT)], cwd=ROOT,
            env=dict(os.environ, AAD_API_KEY=key), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    save_json(RUN / 'launch_256.json', {'pid': child.pid, 'max_requests': 256,
              'model': 'deepseek-v4-pro', 'launched_at_utc': datetime.now(timezone.utc).isoformat()})
    print('Resumed v5 Pro with total 256-call cap; PID', child.pid, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--upgrade-and-launch', action='store_true')
    parser.add_argument('--worker', choices=('evolution', 'finalize', 'audit', 'terminal'))
    args = parser.parse_args()
    if args.upgrade_and_launch and args.worker:
        parser.error('Choose one mode')
    if args.upgrade_and_launch:
        upgrade_and_launch()
    elif args.worker:
        worker(args.worker)
    else:
        experiment()
