"""Independently replay the best completed public-training outputs with the official Rust scorer."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import digest,read_json,save_json
from v3_search import PROJECT,public_cases
from official_tools import docker


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',type=Path,default=PROJECT/'runs/v3_pro_fromscratch')
    args=parser.parse_args();root=args.run_dir.resolve()
    state=read_json(root/'state.json');protocol=read_json(root/'protocol.json')
    eligible={k:r for k,r in state['records'].items() if r.get('full',{}).get('valid')}
    ident=max(eligible,key=lambda k:eligible[k]['full']['mean'])
    rows=eligible[ident]['full']['cases']
    cases={c.name:c for c in public_cases()[0]}
    assert len(rows)==40 and {r['name'] for r in rows}==set(protocol['train'])
    assert all(cases[r['name']].fingerprint==r['input_sha256'] for r in rows)
    folder=root/'public_rust_check'/ident;folder.mkdir(parents=True,exist_ok=True)
    receipt={'source_sha256':ident,'inputs':{r['name']:r['input_sha256'] for r in rows},
             'outputs':{r['name']:digest(r['stdout']) for r in rows}}
    if (folder/'receipt.json').exists() and read_json(folder/'receipt.json')!=receipt:
        raise ValueError('Public replay receipt changed')
    save_json(folder/'receipt.json',receipt)
    for i,row in enumerate(rows):
        (folder/f'{i:04d}.in').write_text(cases[row['name']].text,encoding='utf-8')
        (folder/f'{i:04d}.out').write_text(row['stdout'],encoding='utf-8')
    tools=PROJECT/'data/reference/ahc001/tools'
    docker(['run','--rm','--network=none','--mount',f'type=bind,source={tools},target=/tools,readonly',
        '--mount',f'type=bind,source={folder},target=/work',
        '--mount',f'type=bind,source={PROJECT/"scripts"},target=/scripts,readonly',
        '--workdir=/work','python:3.11-slim','python','/scripts/rust_score_batch.py'])
    official={int(r['file'].split('.')[0]):r['score'] for r in read_json(folder/'rust_scores.json')}
    differences=[rows[i]['name'] for i in range(len(rows)) if official.get(i)!=rows[i]['score']]
    result={'source_sha256':ident,'public_train_count':len(rows),'rust_checked':len(official),
            'mismatches':differences,'rust_mean_score':sum(official.values())/len(rows),
            'private_inputs_used':False}
    save_json(folder/'report.json',result);print(result,flush=True)
    if differences or len(official)!=len(rows):raise ValueError('Official public score mismatch')


if __name__=='__main__':main()
