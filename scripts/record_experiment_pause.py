"""Record a user-requested pause after the exact experiment processes stop."""
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aad.io import read_json, save_json


def main():
    for name in ('v5_flash_256_20260924', 'v4_pro_64_20260925', 'v5_pro_64_20260925'):
        run = ROOT / 'runs' / name
        state = read_json(run / 'state.json')
        ledger = read_json(run / 'llm/ledger.json')
        if any(row['status'] == 'started' for row in ledger):
            raise ValueError('In-flight API call; do not claim clean API pause')
        checkpoint = read_json(run / 'orchestration.json')
        previous = checkpoint.get('phase_before_pause', checkpoint['phase'])
        timestamp = datetime.now(timezone.utc).isoformat()
        receipt = {'paused_at_utc': timestamp, 'reason': 'Explicit user request; resume only when requested',
                   'previous_phase': previous, 'requests': len(ledger),
                   'frozen': state.get('frozen', False),
                   'pending_preserved': state.get('pending') is not None,
                   'source_response_and_evaluation_cache_preserved': True,
                   'resume_note': 'Remove STOP_AFTER_CURRENT only after explicit resume. '
                                  'Reuse original run, policy and pending response; never launch a fresh pair.'}
        if name == 'v5_flash_256_20260924':
            receipt['terminal_progress'] = read_json(ROOT / 'runs/v5_flash_256_system_1000/progress.json')
            receipt['resume_note'] += ' Resume scripts/v5_run_experiment.py; no API key needed for frozen run.'
        else:
            receipt['resume_note'] += ' Supply AAD_API_KEY in memory, then pro64_experiments.py --version 4 or 5.'
            save_json(run / 'resource_status.json', {'status': 'paused_by_user'})
        save_json(run / 'pause.json', receipt)
        save_json(run / 'orchestration.json', {**checkpoint, 'phase': 'paused_by_user',
                  'phase_before_pause': previous, 'paused_at_utc': timestamp})
        print(name, 'paused;', len(ledger), 'requests')


if __name__ == '__main__':
    main()
