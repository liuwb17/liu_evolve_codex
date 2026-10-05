"""WSL bootstrap: unchanged v5.2 search, host-coordinated evaluation admission."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

source, suite, task = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
schedule = json.loads((suite/'schedule.json').read_text())
task_root = schedule['task_roots'][task]
sys.path.insert(0, str(source))
sys.path.insert(0, str(source/'scripts'))
os.environ['AAD_API_KEY'] = json.loads(sys.stdin.readline())['key']
from aad.v5_1_ale import AleBatchEvaluator
original = AleBatchEvaluator.__call__


def coordinated(self, *args, **kwargs):
    folder = suite/'admission'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps(dict(task=task, pid=os.getpid())))
    while not (folder/'grant').exists():
        if (suite/'STOP_AFTER_CURRENT').exists():
            raise RuntimeError('Suite stopped before evaluation admission')
        time.sleep(.2)
    try:
        return original(self, *args, **kwargs)
    finally:
        (folder/'done').touch()


AleBatchEvaluator.__call__ = coordinated
from v5_1_search import main
args=['run','--task',task_root,'--run-dir',str(suite/task),
      '--calls',str(schedule['calls']),'--model',schedule['model'],
      '--token-budget',str(schedule['token_budget']),
      '--plan' if schedule['plan'] else '--no-plan',
      '--failure-experience' if schedule['failure_experience'] else '--no-failure-experience']
if schedule['para'] is not None:args.extend(['--para',str(schedule['para'])])
if schedule.get('resume'):args.append('--resume')
main(args)
# The run command does not auto-freeze; freeze only after a normal budget exit.
if not (suite/task/'STOP_AFTER_CURRENT').exists():
    main(['freeze','--task',task_root,'--run-dir',str(suite/task)])
