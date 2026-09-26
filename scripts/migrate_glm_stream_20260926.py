"""One-time, user-authorized transport fix; preserve the failed call and all history."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from aad.io import digest, read_json, save_json
from v5_glm52_128 import RUN, policy, transport_policy, preflight
from pro64_experiments import exclusive_file


def migrate():
    preflight()
    backup = RUN / 'transport_fix_20260926'
    with exclusive_file(RUN / 'controller.lock'):
        if (backup / 'receipt.json').exists():
            raise ValueError('Migration already applied')
        state = read_json(RUN / 'state.json')
        ledger_path = RUN / 'llm/ledger.json'
        ledger_bytes = ledger_path.read_bytes()
        ledger = read_json(ledger_path)
        protocol = read_json(RUN / 'protocol.json')
        checkpoint = read_json(RUN / 'orchestration.json')
        if state != read_json(backup / 'state.before.json') or protocol != read_json(backup / 'protocol.before.json'):
            raise ValueError('Checkpoint changed since backup')
        if ledger_bytes != (backup / 'ledger.before.json').read_bytes():
            raise ValueError('Ledger changed since backup')
        if len(ledger) != 1 or ledger[0]['status'] != 'network_error' or state['records'] or state['frozen']:
            raise ValueError('Not the known first-request transport failure')
        if checkpoint['phase'] != 'failed' or checkpoint != read_json(backup / 'orchestration.before.json'):
            raise ValueError('Not the stopped original experiment')
        if digest(json.dumps(protocol, sort_keys=True)) != state['signature']:
            raise ValueError('Original signature invalid')
        previous_signature = state['signature']
        protocol['config']['glm_transport'] = transport_policy()
        state['signature'] = digest(json.dumps(protocol, sort_keys=True))
        save_json(RUN / 'protocol.json', protocol)
        save_json(RUN / 'state.json', state)
        save_json(RUN / 'orchestration.json', {'policy': policy(), 'phase': 'transport_repaired_ready'})
        if ledger_path.read_bytes() != ledger_bytes:
            raise ValueError('Unexpected ledger mutation')
        save_json(backup / 'receipt.json', {
            'migrated_at_utc': datetime.now(timezone.utc).isoformat(),
            'authorization': 'User explicitly requested fix and continuation',
            'reason': '604.609s nonstream network error; use SSE with progress and detailed errors',
            'old_signature': previous_signature, 'new_signature': state['signature'],
            'ledger_sha256': hashlib.sha256(ledger_bytes).hexdigest(),
            'preserved_requests': len(ledger), 'max_requests': 128,
            'model_prompts_algorithm_data_and_search_budgets_unchanged': True,
            'old_pending_request_preserved': True,
            'note': 'Engine will record the already failed pending call, not resend under its old ID.'})
        print('Migrated transport only; failed call 0 preserved; 127 request slots remain.')


if __name__ == '__main__':
    migrate()
