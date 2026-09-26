"""Verify one-request program origins and coordinated edit ancestry."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import read_json,save_json,digest
from aad.v5_policy import program,edit
from aad.v3_provider import PROBLEM

def audit(root):
    root=Path(root);state=read_json(root/'state.json');records=state['records'];rows=[]
    def source(ident):
        code=(root/'candidates'/ident/'solution.cpp').read_text(encoding='utf-8')
        assert digest(code)==ident,'Source changed';return code
    for ident,record in records.items():
        call=record['implementation_call'];request=read_json(root/'llm'/f'{call:05d}.request.json')
        task=json.loads(request['messages'][1]['content']);assert task['problem']==PROBLEM
        for field,key in [('parent_source','parent'),('donor_source','donor')]:
            value=digest(task[field]) if task.get(field) else None
            assert value==record.get(key),'Ancestry mismatch'
            if value:assert value in records and records[value]['implementation_call']<call,'External or noncausal source'
        response=read_json(root/'llm'/f'{call:05d}.response.json');choice=response['choices'][0]
        assert choice['finish_reason']=='stop','Incomplete response used'
        expected=program(choice['message']['content']) if record['implementation_kind']=='program' else edit(source(record['parent']),choice['message']['content'])[0]
        assert source(ident)==expected,'Source does not reconstruct'
        rows.append({'source':ident,'call':call,'kind':record['implementation_kind'],'parent':record['parent']})
    result={'version':5,'count':len(rows),'all_origins_verified':True,'candidates':rows,
            'limitation':'Cannot exclude model pretraining familiarity'}
    save_json(root/'source_audit.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);r=audit(p.parse_args().run_dir)
    print({k:v for k,v in r.items() if k!='candidates'})
