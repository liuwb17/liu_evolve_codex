"""Run the authorized v5 cold-start comparison through frozen terminal evaluation."""
import getpass
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json,save_json
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'runs/v5_flash_256_20260924'
OUT=ROOT/'runs/v5_flash_256_system_1000'

def main():
    RUN.mkdir(parents=True,exist_ok=True)
    policy={'version':5,'model':'deepseek-flash','max_requests':256,'token_cap':None,
        'train_count':40,'validation_count':10,'private_count':1000,'search_seconds':4.8,
        'hard_seconds':4.95,'workers':6,'cpu_per_case':1,'memory_mib':512,
        'cold_start':True,'old_solver_sources_supplied':False,'private_feedback_supplied':False,
        'one_call_per_proposal':True,'human_baseline':'runs/v3_kusano_private_20260922'}
    path=RUN/'orchestration.json'
    if path.exists():
        old=read_json(path)
        if old['policy']!=policy:raise ValueError('Policy changed')
        if old['phase']=='completed':print('Already completed');return
    state=read_json(RUN/'state.json') if (RUN/'state.json').exists() else {}
    if not state.get('frozen') and not state.get('validation_started') and not state.get('development_closed'):
        os.environ['AAD_API_KEY']=getpass.getpass('AAD API key (hidden): ')
    def phase(name,**extra):
        save_json(path,{'policy':policy,'phase':name,**extra});print(name,extra,flush=True)
    def invoke(script,*args):
        with (RUN/'console.log').open('a',encoding='utf-8') as log:
            process=subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/script),*map(str,args)],
                                   cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if process.returncode:
            phase('failed',step=script,returncode=process.returncode);raise RuntimeError(f'{script} failed; inspect console.log')
    common=['--run-dir',RUN,'--calls',256,'--token-budget',0,'--model','deepseek-flash']
    if not state.get('frozen') and not state.get('validation_started') and not state.get('development_closed'):
        phase('evolution');invoke('v5_search.py',*common,*(['--resume'] if state else []))
        if (RUN/'STOP_AFTER_CURRENT').exists():phase('cooperatively_stopped');return
    if not read_json(RUN/'state.json').get('frozen'):
        phase('public_validation_and_freeze');invoke('v5_search.py',*common,'--resume','--finalize')
    phase('source_audit');invoke('v5_audit.py','--run-dir',RUN)
    phase('private_1000_terminal_evaluation');invoke('v5_system_test.py','--run-dir',RUN,'--output',OUT,
        '--paired-baseline',ROOT/'runs/v3_kusano_private_20260922')
    phase('completed')

if __name__=='__main__':main()
