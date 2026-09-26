"""v4 cold-start runner. Real API execution is explicit; never starts private evaluation."""
import argparse
import getpass
import hashlib
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.v4_provider import V4Provider
from aad.v4_engine import V4Engine
from aad.v3_evaluator import CleanEvaluator
from v3_search import public_cases

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--calls',type=int,default=128)
    p.add_argument('--token-budget',type=int,default=8000000,help='Total token accounting cap; 0 disables this cap, not the API request cap')
    p.add_argument('--model',default='deepseek-flash')
    p.add_argument('--ask-key',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--finalize',action='store_true',help='Only public validation and freezing, no further API calls')
    args=p.parse_args();root=args.run_dir.resolve()
    if args.calls<1 or args.token_budget<0:raise ValueError('Positive call budget and nonnegative token cap required')
    if (root/'state.json').exists()!=args.resume:raise ValueError('Use a fresh run directory or explicit --resume')
    config={'model':args.model,'calls':args.calls,'token_budget':args.token_budget,
            'initial_valid_roots':4,'max_initial_attempts':12,'repeats':3,
            'margin':.0001,'validation_margin':.0002,'search_seconds':4.8,'hard_seconds':4.95}
    config['image']=subprocess.run(['docker','image','inspect','mosaic-cpp:1','--format','{{.Id}}'],
                                  capture_output=True,text=True,check=True).stdout.strip()
    project=Path(__file__).resolve().parents[1]
    config['implementation_hashes']={name:hashlib.sha256((project/name).read_bytes()).hexdigest()
        for name in ('aad/v4_engine.py','aad/v4_protocol.py','aad/v4_provider.py','aad/v3_evaluator.py','aad/assets/v3_timer.py')}
    key='unused-offline-finalization' if args.finalize else (getpass.getpass('AAD API key (hidden): ') if args.ask_key else None)
    provider=V4Provider(root,args.model,args.calls,args.token_budget,key)
    cases,_=public_cases();train=[c for i,c in enumerate(cases) if i%5!=4];validation=[c for i,c in enumerate(cases) if i%5==4]
    evaluators={}
    def evaluate(code,subset,seconds,replica):
        cache_key=(seconds,replica)
        if cache_key not in evaluators:
            evaluators[cache_key]=CleanEvaluator(root/'measurements'/replica,workers=6,seconds=seconds)
            if evaluators[cache_key].image!=config['image']:raise ValueError('Evaluation image changed')
        return evaluators[cache_key].evaluate(code,subset)
    engine=V4Engine(root,provider,evaluate,train,validation,config)
    if args.finalize:print('Frozen:',engine.finalize())
    else:engine.run()

if __name__=='__main__':main()
