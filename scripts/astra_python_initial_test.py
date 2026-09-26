"""Single frozen Python proposal, public-only evaluation using the original timer."""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from aad.io import digest, read_json, save_json
from aad.v3_evaluator import CleanEvaluator
from pro64_experiments import evaluation_slot, exclusive_file
from v3_search import public_cases

SOURCE = ROOT / 'experiments/astra_python_initial_20260926/solution.py'
RUN = ROOT / 'runs/astra_python_initial_20260926'
SCRIPT = Path(__file__).resolve()


class PythonEvaluator(CleanEvaluator):
    def compile(self, code):
        try:
            ast.parse(code)
        except SyntaxError as error:
            return None, str(error)
        if not code.startswith('#!/usr/bin/python3\n'):
            raise ValueError('Expected verified Python interpreter shebang')
        folder = self.root / 'builds' / digest(code)
        folder.mkdir(parents=True, exist_ok=True)
        candidate = folder / 'solution'
        if candidate.exists() and digest(candidate.read_text(encoding='utf-8')) != digest(code):
            raise ValueError('Source changed')
        candidate.write_text(code, encoding='utf-8', newline='\n')
        candidate.chmod(0o755)
        return folder, None


def protocol():
    cases, _ = public_cases()
    train = [case for i,case in enumerate(cases) if i % 5 != 4]
    image = subprocess.run(['docker','image','inspect','mosaic-cpp:1','--format','{{.Id}}'],
                           capture_output=True,text=True,check=True).stdout.strip()
    return {'source_sha256': digest(SOURCE.read_text(encoding='utf-8')),
            'controller_sha256': hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            'image': image, 'language': 'CPython 3.11.2 / standard library only',
            'origin': 'Current assistant single proposal; existing conversation context retained',
            'not_verified_model_identity': 'User identified assistant as gpt6astra; no external API model receipt',
            'previous_solver_source_read': False, 'score_guided_solver_revisions': 0,
            'train': {c.name:c.fingerprint for c in train},
            'seconds': 4.8, 'hard_seconds': 4.95, 'repeats': 3, 'workers': 6,
            'cpu_per_case': 1, 'memory_mib': 512, 'private_evaluated': False,
            'glm_comparison': 'GLM first valid initial C++ program, not language-matched'}, train


def worker():
    with exclusive_file(RUN/'controller.lock'):
        expected, train = protocol()
        if read_json(RUN/'protocol.json') != expected:
            raise ValueError('Frozen initial proposal or protocol changed')
        code = SOURCE.read_text(encoding='utf-8')
        def status(phase, **extra):
            save_json(RUN/'status.json', {'phase':phase, **extra})
        try:
            # No preliminary scored optimization: every test uses this frozen source.
            results = []
            for replica in range(3):
                status('public_training', repeat=replica+1)
                with evaluation_slot(RUN):
                    evaluator = PythonEvaluator(RUN/'measurements'/f'r{replica}', workers=6, seconds=4.8)
                    if evaluator.image != expected['image']:
                        raise ValueError('Image changed')
                    result = evaluator.evaluate(code, train)
                save_json(RUN/f'repeat_{replica}.json', result)
                results.append(result)
                print({'repeat':replica+1,'mean':result['mean'],'valid':result['valid']},flush=True)
            status('official_rust_rescore')
            folder = RUN/'rust_check'
            folder.mkdir(parents=True,exist_ok=True)
            case_map = {case.name:case for case in train}
            rows = [row for result in results for row in result['cases']]
            valid_rows = [row for row in rows if row['status']=='AC']
            for i,row in enumerate(valid_rows):
                (folder/f'{i:04d}.in').write_text(case_map[row['name']].text,encoding='utf-8')
                (folder/f'{i:04d}.out').write_text(row['stdout'],encoding='utf-8')
            if valid_rows:
                with evaluation_slot(RUN):
                    subprocess.run(['docker','run','--rm','--network=none',
                        '--mount',f'type=bind,source={ROOT / "data/reference/ahc001/tools"},target=/tools,readonly',
                        '--mount',f'type=bind,source={folder},target=/work',
                        '--mount',f'type=bind,source={ROOT / "scripts/rust_score_batch.py"},target=/rust_score.py,readonly',
                        '--workdir=/work',expected['image'],'python3','/rust_score.py'],check=True,timeout=300)
                official = read_json(folder/'rust_scores.json')
            else:
                official = []
            matched = {int(item['file'].split('.')[0]):item['score'] for item in official}
            mismatches = [i for i,row in enumerate(valid_rows) if matched.get(i)!=row['score']]
            report = {**expected,'mean_score':statistics.mean(r['mean'] for r in results)*1e9,
                      'repeat_mean_scores':[r['mean']*1e9 for r in results],
                      'status_counts':dict(Counter(row['status'] for row in rows)),
                      'max_seconds':max(row['seconds'] for row in rows),
                      'rust_checked':len(official),'rust_mismatches':mismatches}
            save_json(RUN/'report.json',report)
            if mismatches:
                raise ValueError('Rust scorer mismatch')
            status('completed',mean_score=report['mean_score'])
            print(report,flush=True)
        except Exception as error:
            status('failed',error=str(error))
            raise


def launch():
    if RUN.exists():
        raise ValueError('Experiment already exists; refuse new run over history')
    expected, _ = protocol()
    RUN.mkdir(parents=True)
    save_json(RUN/'protocol.json',expected)
    save_json(RUN/'status.json',{'phase':'launching'})
    with (RUN/'console.log').open('a',encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable,'-B',str(SCRIPT),'--worker'],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
    save_json(RUN/'launch.json',{'pid':child.pid,'started_at_utc':datetime.now(timezone.utc).isoformat()})
    print('Frozen initial Python source; evaluation PID',child.pid,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args()
    worker() if args.worker else launch()
