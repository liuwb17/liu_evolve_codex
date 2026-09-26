"""Infrastructure-only C++ fixture. NOT an initial algorithm or benchmark result."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.v3_evaluator import CleanEvaluator
from aad.problem import Case
from aad.io import save_json

def main():
    root=Path(__file__).resolve().parents[1]/'runs/v4_infrastructure_smoke'
    # Deliberately tiny test-only input/fixture, never fed to evolution.
    code='#include <iostream>\nint main(){int n,x,y,r;std::cin>>n;while(n--){std::cin>>x>>y>>r;std::cout<<x<<" "<<y<<" "<<x+1<<" "<<y+1<<"\\n";}}\n// AAD_V4_COMPLETE\n'
    cases=[Case('infrastructure_fixture','2\n1 1 1\n3 3 1\n')]
    reports=[]
    for replica in range(3):
        reports.append(CleanEvaluator(root/f'repeat{replica}',workers=1,seconds=.6).evaluate(code,cases))
    assert all(r['valid'] and r['mean']==1 for r in reports)
    save_json(root/'report.json',{'purpose':'infrastructure only; no LLM and no benchmark claim',
        'independent_cache_roots':3,'results':reports})
    print('Three isolated-cache Docker executions passed; not an AHC001 performance result.')

if __name__=='__main__':main()
