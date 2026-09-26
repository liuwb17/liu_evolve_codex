"""Reconstruct source from model output, including exact one-edit source ancestry."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json,save_json,digest
from aad.v4_protocol import plan,program,apply_edit
from aad.v3_provider import PROBLEM

def audit(root):
    root=Path(root);state=read_json(root/'state.json');records=state['records'];rows=[]
    def source(ident):
        text=(root/'candidates'/ident/'solution.cpp').read_text(encoding='utf-8')
        assert digest(text)==ident,'Source hash mismatch'
        return text
    for ident,record in records.items():
        calls=[record['plan_call'],record['implementation_call']]
        assert calls[0]<calls[1],'Noncausal implementation'
        responses=[]
        for call in calls:
            request=read_json(root/'llm'/f'{call:05d}.request.json')
            task=json.loads(request['messages'][1]['content'])
            assert task['problem']==PROBLEM,'Problem changed'
            supplied={key:digest(task[key]) for key in ('parent_source','donor_source') if task.get(key)}
            assert supplied.get('parent_source')==record.get('parent'),'Parent provenance mismatch'
            assert supplied.get('donor_source')==record.get('donor'),'Donor provenance mismatch'
            for value in supplied.values():
                assert value in records and records[value]['implementation_call']<call,'External or noncausal source'
            response=read_json(root/'llm'/f'{call:05d}.response.json')
            assert response['choices'][0]['finish_reason']=='stop','Incomplete response used'
            responses.append(response['choices'][0]['message']['content'])
        assert plan(responses[0])==record['plan'],'Plan mismatch'
        expected=program(responses[1]) if record['implementation_kind']=='program' else apply_edit(source(record['parent']),responses[1])
        assert digest(expected)==ident and expected==source(ident),'Reconstructed source differs'
        rows.append({'source':ident,'calls':calls,'mode':record['implementation_kind'],'parent':record['parent']})
    result={'version':4,'count':len(rows),'all_origins_verified':True,'candidates':rows,
            'limitation':'Cannot exclude model pretraining knowledge or establish performance superiority'}
    save_json(root/'source_audit.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);args=p.parse_args()
    result=audit(args.run_dir);print({k:v for k,v in result.items() if k!='candidates'})
