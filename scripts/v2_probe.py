import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.cpp import CppEvaluator
from aad.problem import Case,synthetic_case
from aad.io import save_json

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path('aad/assets/seed_v2.cpp'))
    parser.add_argument('--seconds',type=float,default=0.5)
    parser.add_argument('--count',type=int,default=12)
    parser.add_argument('--offset',type=int,default=40000)
    parser.add_argument('--output',type=Path,default=Path('runs/v2_probe'))
    args=parser.parse_args()
    cases=[Case(f'dev_{i}',synthetic_case(i)) for i in range(args.offset,args.offset+args.count)]
    evaluator=CppEvaluator(Path('runs/cpp_evaluations'),workers=4,seconds=args.seconds)
    result=evaluator.evaluate(args.source.read_text(encoding='utf-8'),cases)
    save_json(args.output.with_suffix('.json'),result)
    print({k:v for k,v in result.items() if k!='cases'},flush=True)
if __name__=='__main__':main()
