"""Report suite progress independently of process liveness."""
from pathlib import Path
import time


STALL_SECONDS = 1200


def task_progress(root, *, now=None, waiting_for_admission=False):
    root = Path(root)
    now = time.time() if now is None else now
    paths = [root / 'state.json', root / 'llm/ledger.json', root / 'planning/llm/ledger.json',
             root / 'measurements', root / 'measurements/cpu_leases']
    changed = [p.stat().st_mtime for p in paths if p.exists()]
    age = max(0, now - max(changed)) if changed else 0
    return {'seconds_since_progress': round(age, 1),
            'status': ('waiting_for_evaluation_admission' if waiting_for_admission else
                       'stalled' if age > STALL_SECONDS else 'running')}
