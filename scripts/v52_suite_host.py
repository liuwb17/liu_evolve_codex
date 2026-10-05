"""Windows host supervisor; grants shared Docker work exclusive access vs legacy timers."""
import argparse
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aad.provider import environment
from aad.io import save_json,read_json
from aad.v5_1_watchdog import task_progress


def linux(path):
    value=str(Path(path).resolve()).replace('\\','/')
    return '/mnt/'+value[0].lower()+value[2:]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite',type=Path)
    suite=parser.parse_args().suite.resolve()
    protocol=read_json(suite/'schedule.json')
    key=environment('DEEPSEEK_API_KEY') or environment('AAD_API_KEY')
    if not key:raise ValueError('Missing provider credential')
    children={}
    for task in protocol['tasks']:
        exists=(suite/task/'state.json').exists()
        if exists != bool(protocol.get('resume')):
            raise ValueError('Fresh or explicitly migrated resume run required for all tasks')
        if (suite/task/'STOP_AFTER_CURRENT').exists() or (suite/'STOP_AFTER_CURRENT').exists():
            raise ValueError('Suite is stopped')
    for task in protocol['tasks']:
        cmd=['wsl.exe','-d','Ubuntu','-u','root','--','/root/ale-venv/bin/python','-u',
             linux(suite/'source/scripts/v52_suite_worker.py'),linux(suite/'source'),linux(suite),task]
        with (suite/(task+'.stdout.log')).open('w') as out,(suite/(task+'.stderr.log')).open('w') as err:
            child=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=out,stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW)
        child.stdin.write((json.dumps(dict(key=key))+'\n').encode());child.stdin.close()
        children[task]=child
        save_json(suite/(task+'.process.json'),dict(pid=child.pid,command=cmd))
    key=None
    lock=Path(protocol['legacy_evaluation_lock'])
    locked=False
    with lock.open('a+b') as handle:
        try:
            while True:
                folders=list((suite/'admission').glob('*'))
                pending=[p for p in folders if (p/'request.json').exists() and not (p/'grant').exists()]
                active=[p for p in folders if (p/'grant').exists() and not (p/'done').exists()]
                if not active and locked:
                    handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1);locked=False
                if pending and not locked:
                    try:
                        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);locked=True
                    except OSError:pass
                if locked:
                    for p in pending:(p/'grant').touch()
                queued={read_json(p/'request.json')['task'] for p in pending}
                progress={t:task_progress(suite/t,waiting_for_admission=t in queued and not locked)
                          for t,c in children.items() if c.poll() is None}
                failed=any(c.poll() is not None and c.returncode != 0 for c in children.values())
                stalled=any(p['status']=='stalled' for p in progress.values())
                if failed or stalled:
                    # Stop at safe batch boundaries; never silently continue spending
                    # after an infrastructure failure or claim a stalled worker is healthy.
                    for task in protocol['tasks']:(suite/task/'STOP_AFTER_CURRENT').touch()
                state=dict(time=datetime.now().astimezone().isoformat(),phase='running',
                    processes={t:dict(pid=c.pid,exit_code=c.poll()) for t,c in children.items()},
                    evaluation_lock_held=locked,pending_admissions=len(pending),active_admissions=len(active),
                    progress=progress)
                if failed:state['phase']='needs_attention'
                elif stalled:state['phase']='stalled'
                save_json(suite/'status.json',state)
                if all(c.poll() is not None for c in children.values()):
                    state['phase']='completed' if all(c.returncode==0 for c in children.values()) else 'needs_attention'
                    save_json(suite/'status.json',state)
                    with (ROOT/'exp_log.md').open('a',encoding='utf-8') as stream:
                        stream.write('\n### v5.2 suite exited\n\n```json\n'+json.dumps(state,indent=2)+'\n```\n')
                    break
                time.sleep(1)
        finally:
            if locked:
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


if __name__=='__main__':main()
