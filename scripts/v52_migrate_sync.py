"""Copy a stopped pre-fsync suite into a separately audited recovery run (no model calls)."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aad.io import digest, read_json, save_json
from aad.v5_1_ale import load_task, runtime_identity
from aad.v5_1_execution import EXECUTION_PROTOCOL


def linux(path):
    text = str(path).replace('\\', '/')
    return '/mnt/' + text[0].lower() + text[2:] if len(text) > 1 and text[1] == ':' else text


def migrate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError('Recovery requires a distinct, new destination')
    schedule = read_json(source / 'schedule.json')
    status = read_json(source / 'status.json')
    if any(p.get('exit_code') is None for p in status['processes'].values()):
        raise ValueError('Stop all source workers first')
    if not (source / 'STOP_AFTER_CURRENT').exists():
        raise ValueError('Source suite must remain stopped')
    verified = {}
    for task in schedule['tasks']:
        run = source / task
        if not (run / 'STOP_AFTER_CURRENT').exists():
            raise ValueError('Source task must remain stopped')
        protocol, state = read_json(run / 'protocol.json'), read_json(run / 'state.json')
        if state.get('frozen') or state.get('development_closed'):
            raise ValueError('Frozen run cannot migrate')
        if digest(json.dumps(protocol, sort_keys=True)) != state['signature']:
            raise ValueError('Source protocol changed')
        task_root = Path(linux(schedule['task_roots'][task]))
        manifest, _ = load_task(task_root)
        if digest(json.dumps(manifest, sort_keys=True)) != protocol['config']['task_sha256']:
            raise ValueError('Task bundle changed')
        runtime = runtime_identity(task_root)
        old_runtime = protocol['config']['runtime']
        if 'execution_protocol' in old_runtime:
            raise ValueError('Source is not a pre-fsync run')
        if {k: v for k, v in runtime.items() if k != 'execution_protocol'} != old_runtime:
            raise ValueError('SDK, tools, images or CPU policy changed beyond the approved completion repair')
        for ident in state['records']:
            if digest((run / 'candidates' / ident / 'solution.cpp').read_text()) != ident:
                raise ValueError('Candidate source changed')
        verified[task] = protocol, state, runtime

    destination.mkdir(parents=True)
    snapshot = destination / 'source'
    snapshot.mkdir()
    for name in ('aad', 'scripts', 'tests', 'docs'):
        shutil.copytree(ROOT / name, snapshot / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('AGENTS.md', 'README.md', 'pyproject.toml'):
        shutil.copy2(ROOT / name, snapshot / name)
    paths = sorted((snapshot / 'aad').rglob('*.py')) + [snapshot / 'scripts/v5_1_search.py']
    hashes = {p.relative_to(snapshot).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    audit = {'time': datetime.now(timezone.utc).isoformat(), 'source_suite': str(source),
             'execution_protocol': EXECUTION_PROTOCOL, 'tasks': {},
             'scope': 'Existing completed measurements carried with provenance; incomplete evaluations rerun. '
                      'No solver source, prompt, request ID, call budget, selection rule or GNU time measurement changed.'}
    for task, (protocol, state, runtime) in verified.items():
        old_run, run = source / task, destination / task
        run.mkdir()
        # All paid-response evidence and candidate sources remain byte-identical.
        for name in ('llm', 'planning', 'candidates'):
            if (old_run / name).exists():
                shutil.copytree(old_run / name, run / name)
        for name in ('plan.json',):
            if (old_run / name).exists():
                shutil.copy2(old_run / name, run / name)
        save_json(run / 'migration_original_protocol.json', protocol)
        origin = {'source_run': str(old_run), 'protocol_signature': state['signature'],
                  'state_sha256': hashlib.sha256((old_run / 'state.json').read_bytes()).hexdigest(),
                  'ledger_sha256': hashlib.sha256((old_run / 'llm/ledger.json').read_bytes()).hexdigest(),
                  'carried_measurements': {ident: {'smoke': 'smoke' in r,
                                                   'evaluations': len(r.get('evaluations', [])),
                                                   'full': 'full' in r}
                                           for ident, r in state['records'].items()},
                  'solve_requests': len(read_json(run / 'llm/ledger.json')),
                  'plan_requests': len(read_json(run / 'planning/llm/ledger.json'))}
        protocol['config']['runtime'] = runtime
        protocol['config']['implementation_hashes'] = hashes
        state['signature'] = digest(json.dumps(protocol, sort_keys=True))
        state['migration'] = {'record': 'migration.json', 'source_run': str(old_run),
                              'original_protocol_signature': origin['protocol_signature'],
                              'execution_protocol': EXECUTION_PROTOCOL}
        save_json(run / 'migration.json', origin)
        save_json(run / 'protocol.json', protocol)
        save_json(run / 'state.json', state)
        audit['tasks'][task] = {k: v for k, v in origin.items() if k != 'carried_measurements'}
    schedule.update(resume=True, recovery_from=str(source), run_dir=str(destination),
                    recovery_execution_protocol=EXECUTION_PROTOCOL,
                    legacy_evaluation_lock=str(ROOT.parent / 'liu_evolve_codex/runs/v5_native_evaluation.lock'))
    # Windows host consumes this path; workers use Linux task_roots unchanged.
    lock = schedule['legacy_evaluation_lock']
    if lock.startswith('/mnt/'):
        schedule['legacy_evaluation_lock'] = lock[5].upper() + ':' + lock[6:].replace('/', '\\')
    schedule['source_hashes'] = {p.relative_to(snapshot).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(snapshot.rglob('*')) if p.is_file()}
    save_json(destination / 'schedule.json', schedule)
    save_json(destination / 'migration.json', audit)
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination)))
