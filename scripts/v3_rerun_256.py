"""New cold-start run; no old candidate sources; 256 cumulative calls in this run."""
import getpass
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json,save_json

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'runs/v3_rerun_256_20260922'
PRIOR=ROOT/'runs/v3_kusano_private_20260922/report.json'

def main():
    os.environ['AAD_API_KEY']=getpass.getpass('AAD API key (hidden): ')
    RUN.mkdir(parents=True,exist_ok=True)
    policy={'new_run_api_cap':256,'regular_phase_cap':252,'numeric_phase_cumulative_cap':256,
            'prior_run_requests_not_reused':64,'model_requested':'deepseek-flash',
            'existing_solver_sources_supplied':False,'private_results_supplied':False,
            'numeric_variants':64,'automatic_private_evaluation':False}
    target=RUN/'orchestration.json'
    if target.exists() and read_json(target)['policy']!=policy:raise ValueError('Run policy changed')
    def status(phase,**extra):
        save_json(target,{'policy':policy,'phase':phase,**extra})
        print(phase,extra,flush=True)
    status('waiting_for_prior_terminal_test')
    # Do not compete for CPU with the already-running matched baseline test.
    # Only inspect completion marker existence, never feed its scores to evolution.
    while not PRIOR.exists():
        if (RUN/'STOP_AFTER_CURRENT').exists():status('stopped_before_search');return
        time.sleep(5)
    if (RUN/'STOP_AFTER_CURRENT').exists():status('stopped_before_search');return
    status('structural_evolution')
    command=[sys.executable,'-B',str(ROOT/'scripts/v3_search.py'),'--run-dir',str(RUN),
             '--calls','252','--initial','8','--model','deepseek-flash','--thinking','enabled',
             '--effort','low','--exploration-effort','high','--max-tokens','65536',
             '--full-evaluation','--restart-patience','6']
    if (RUN/'state.json').exists():command.append('--resume')
    with (RUN/'console.log').open('a',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:status('structural_process_failed',returncode=result.returncode);return
        if (RUN/'STOP_AFTER_CURRENT').exists():status('cooperatively_stopped');return
        if not read_json(RUN/'state.json')['records']:status('no_candidates');return
        status('numeric_evolution')
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/v3_parameter_search.py'),
            '--run-dir',str(RUN),'--calls','256','--variants','64','--prepare-only'],
            cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        status('parameter_interface_needs_inspection' if result.returncode==0 else 'parameterization_process_failed',
               returncode=result.returncode)
    # Stop for inspection before running model-selected numeric interfaces.
    # No automatic freeze or private evaluation.

if __name__=='__main__':main()
