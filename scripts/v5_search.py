"""Real cold-start v5 experiment; public data only until explicit terminal testing."""
import argparse
import getpass
import hashlib
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.v5_provider import V5Provider
from aad.v5_engine import V5Engine
from aad.v3_evaluator import CleanEvaluator
from v3_search import public_cases

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--calls',type=int,default=256);parser.add_argument('--token-budget',type=int,default=0)
    parser.add_argument('--model',default='deepseek-flash');parser.add_argument('--ask-key',action='store_true')
    parser.add_argument('--resume',action='store_true');parser.add_argument('--finalize',action='store_true')
    args=parser.parse_args();root=args.run_dir.resolve()
    if args.calls<1 or args.token_budget<0:raise ValueError('Invalid budgets')
    if (root/'state.json').exists()!=args.resume:raise ValueError('Use new directory or --resume')
    project=Path(__file__).resolve().parents[1]
    config={'model':args.model,'calls':args.calls,'token_budget':args.token_budget,'repeats':3,
        'validation_margin':.0002,'training_mean_margin':.00005,'initial_valid_roots':4,
        'max_initial_attempts':20,'search_seconds':4.8,'hard_seconds':4.95,
        'schedule':'bounded-eight-slot-with-runtime-intervention-v1'}
    config['image']=subprocess.run(['docker','image','inspect','mosaic-cpp:1','--format','{{.Id}}'],capture_output=True,text=True,check=True).stdout.strip()
    config['implementation_hashes']={name:hashlib.sha256((project/name).read_bytes()).hexdigest() for name in
        ('aad/v5_engine.py','aad/v5_policy.py','aad/v5_provider.py','aad/v4_engine.py','aad/v4_provider.py',
         'aad/v4_protocol.py','aad/v3_provider.py','aad/v3_diagnostics.py','aad/v3_evaluator.py','aad/assets/v3_timer.py')}
    key='unused-finalization' if args.finalize else (getpass.getpass('AAD API key (hidden): ') if args.ask_key else None)
    provider=V5Provider(root,args.model,args.calls,args.token_budget,key)
    cases,_=public_cases();train=[c for i,c in enumerate(cases) if i%5!=4];validation=[c for i,c in enumerate(cases) if i%5==4]
    evaluators={}
    def evaluate(code,subset,seconds,replica):
        key=(seconds,replica)
        if key not in evaluators:
            evaluators[key]=CleanEvaluator(root/'measurements'/replica,workers=6,seconds=seconds)
            if evaluators[key].image!=config['image']:raise ValueError('Image changed')
        return evaluators[key].evaluate(code,subset)
    engine=V5Engine(root,provider,evaluate,train,validation,config)
    if args.finalize:print('Frozen:',engine.finalize())
    else:engine.run()

if __name__=='__main__':main()
