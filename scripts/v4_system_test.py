"""Explicit frozen v4 terminal evaluation, reusing verified v3 sandbox/scorer."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json
from v4_audit import audit
import v3_system_test

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--paired-baseline',type=Path,default=Path('runs/v3_kusano_private_20260922'))
    args=p.parse_args();state=read_json(args.run_dir/'state.json')
    if not state.get('frozen'):raise ValueError('Freeze before private data access')
    audit(args.run_dir)
    sys.argv=['v3_system_test','--run-dir',str(args.run_dir),'--output',str(args.output),
              '--paired-baseline',str(args.paired_baseline)]
    v3_system_test.main()

if __name__=='__main__':main()
