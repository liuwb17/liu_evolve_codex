"""Finish reviewed numeric interface, freeze using public validation, terminal private test."""
import getpass
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json,save_json
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'runs/v3_rerun_256_20260922'
OUT=ROOT/'runs/v3_flash_256_system_1000'

def main():
    os.environ['AAD_API_KEY']=getpass.getpass('AAD API key (hidden): ')
    orchestration=read_json(RUN/'orchestration.json')
    orchestration['policy']['new_run_api_cap']=272
    orchestration['policy']['numeric_phase_cumulative_cap']=272
    orchestration['policy']['automatic_private_evaluation']=True
    orchestration['policy']['completion_authorized']='User requested completion with relaxed API limit on 2026-09-23'
    def phase(name):
        orchestration['phase']=name;save_json(RUN/'orchestration.json',orchestration)
        print(name,flush=True)
    def run(script,*args):
        with (RUN/'completion.log').open('a',encoding='utf-8') as log:
            result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/script),*map(str,args)],
                                  cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            phase('failed_'+script);raise RuntimeError(f'{script}: exit {result.returncode}; inspect completion.log')
    state=read_json(RUN/'state.json')
    if not state.get('frozen'):
        if not state.get('validation_started'):
            review=read_json(RUN/'numeric_search/interface_review.json')
            numeric=read_json(RUN/'numeric_search/state.json')
            assert review['accepted'] and review['candidate']==numeric['parameterized']
            phase('numeric_evolution')
            run('v3_parameter_search.py','--run-dir',RUN,'--calls',272,'--variants',64)
            if (RUN/'STOP_AFTER_CURRENT').exists():phase('cooperatively_stopped');return
            if read_json(RUN/'numeric_search/state.json')['phase'] not in ('complete','failed'):
                raise RuntimeError('Numeric phase not finished')
        phase('public_validation_and_freeze')
        calls=len(read_json(RUN/'llm/ledger.json'))
        assert calls<=272
        run('v3_search.py','--run-dir',RUN,'--calls',calls,'--initial',8,'--model','deepseek-flash',
            '--thinking','enabled','--effort','low','--exploration-effort','high','--max-tokens',65536,
            '--full-evaluation','--restart-patience',6,'--resume','--finalize')
    phase('source_audit')
    run('v3_audit.py','--run-dir',RUN)
    phase('private_1000_terminal_evaluation')
    run('v3_system_test.py','--run-dir',RUN,'--output',OUT,
        '--paired-baseline',ROOT/'runs/v3_kusano_private_20260922')
    phase('completed')

if __name__=='__main__':main()
