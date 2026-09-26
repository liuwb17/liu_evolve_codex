"""Explicit resume of the three paused experiments, without changing protocols."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aad.io import read_json, save_json

RUNS = ('v5_flash_256_20260924', 'v4_pro_64_20260925', 'v5_pro_64_20260925')


def preflight():
    image = subprocess.run(['docker', 'image', 'inspect', 'mosaic-cpp:1', '--format', '{{.Id}}'],
                           capture_output=True, text=True, check=True).stdout.strip()
    process_check = subprocess.run(['powershell', '-NoProfile', '-Command',
        "@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and "
        "($_.CommandLine -match 'pro64_experiments.py|v5_run_experiment.py|v5_system_test.py') }).Count"],
        capture_output=True, text=True, check=True)
    if process_check.stdout.strip() != '0':
        raise ValueError('Existing experiment process detected; refuse duplicate resume')
    for name in RUNS:
        run = ROOT / 'runs' / name
        orchestration = read_json(run / 'orchestration.json')
        if orchestration['phase'] != 'paused_by_user' or not (run / 'STOP_AFTER_CURRENT').exists():
            raise ValueError(f'Not explicitly paused: {name}')
        policy = orchestration['policy']
        if name.startswith('v5_flash'):
            if not read_json(run / 'state.json')['frozen']:
                raise ValueError('Flash must remain frozen')
        elif hashlib.sha256((ROOT / 'scripts/pro64_experiments.py').read_bytes()).hexdigest() != policy['wrapper_sha256']:
            raise ValueError('Pro wrapper changed')
        ledger = read_json(run / 'llm/ledger.json')
        if any(row['status'] == 'started' for row in ledger):
            raise ValueError('Unresolved API request')
        print(name, 'saved requests:', len(ledger), 'image:', image, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    preflight()
    if args.check:
        return
    key = os.environ.get('AAD_API_KEY') or getpass.getpass('AAD API key (hidden): ')
    if not key.strip():
        raise ValueError('Empty key')
    env = dict(os.environ, AAD_API_KEY=key)
    for name, arguments in zip(RUNS, (
            ['scripts/v5_run_experiment.py'],
            ['scripts/pro64_experiments.py', '--version', '4'],
            ['scripts/pro64_experiments.py', '--version', '5'])):
        run = ROOT / 'runs' / name
        # Archive only this exact stop marker; preserve the original pause receipt.
        stop = run / 'STOP_AFTER_CURRENT'
        marker_archive = run / ('STOP_RESUMED_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        stop.rename(marker_archive)
        with (run / 'resume_launcher.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, '-B', *arguments], cwd=ROOT,
                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        save_json(run / 'resume.json', {'pid': child.pid,
                  'resumed_at_utc': datetime.now(timezone.utc).isoformat(),
                  'previous_pause': 'pause.json', 'preserve_existing_budget': True})
        print('Resumed', name, 'PID', child.pid, flush=True)


if __name__ == '__main__':
    main()
