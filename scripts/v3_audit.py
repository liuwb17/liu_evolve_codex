"""Audit that indexed v3 programs exactly match model responses and prompt lineage."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aad.io import digest,read_json,save_json
from aad.v3_provider import PROBLEM
from aad.parameters import assemble_parameters

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-dir',type=Path,default=Path('runs/v3_pro_fromscratch'))
    args=parser.parse_args();root=args.run_dir
    state=read_json(root/'state.json');records=state['records'];audit=[]
    for ident,record in records.items():
        call=record['call_id']
        request=read_json(root/'llm'/f'{call:05d}.request.json')
        response=read_json(root/'llm'/f'{call:05d}.response.json')
        text=response['choices'][0]['message']['content'].strip()
        if text.startswith('```'):text='\n'.join(text.splitlines()[1:-1])
        proposal=json.loads(text)
        code=(root/'candidates'/ident/'solution.cpp').read_text(encoding='utf-8')
        mode=record.get('assembly',{}).get('type','full-program')
        if mode=='parameter-block':
            assembly=record['assembly'];parent=assembly['parent']
            assert parent in records and records[parent]['call_id']<call,'Invalid block parent'
            parent_code=(root/'candidates'/parent/'solution.cpp').read_text(encoding='utf-8')
            assert digest(parent_code)==parent,'Block parent changed'
            expected=assemble_parameters(parent_code,proposal['code'],assembly['schema'],assembly['values'])
        else:
            assert mode=='full-program','Unknown source assembly mode'
            expected=proposal['code']
        assert digest(expected)==digest(code)==ident,'Source differs from audited API origin'
        if 'parameter_schema_call_id' in record:
            metadata=read_json(root/'llm'/f"{record['parameter_schema_call_id']:05d}.response.json")
            text=metadata['choices'][0]['message']['content'].strip()
            if text.startswith('```'):text='\n'.join(text.splitlines()[1:-1])
            metadata_proposal=json.loads(text)
            assert digest(metadata_proposal['code'])==ident,'Metadata supplied for different code'
            assert metadata_proposal['parameter_schema']==record['parameter_schema'],'Metadata differs from API response'
        task=json.loads(request['messages'][1]['content'])
        assert task['problem']==PROBLEM,'Problem changed'
        if mode=='parameter-block':
            assert task['operation']=='parameter_defaults','Wrong operation for block assembly'
            assert digest(task['parent_source'])==assembly['parent'],'Block parent differs from request'
            assert task['parameter_schema']==assembly['schema'] and task['selected_values']==assembly['values'],'Block specification differs from request'
        supplied=[digest(task[k]) for k in ('parent_source','donor_source') if task.get(k)]
        assert all(k in records for k in supplied),'External program supplied'
        assert all(records[k]['call_id']<call for k in supplied),'Non-causal lineage'
        if task['operation'] in ('independent_initial_design','independent_restart'):
            assert not supplied,'Initial design received seed code'
        audit.append({'source_sha256':ident,'call_id':call,'operation':task['operation'],
                      'actual_prompt_source_parents':supplied,'source_origin_mode':mode,
                      'source_matches_audited_origin':True,'source_matches_api':mode=='full-program'})
    result={'indexed_programs':len(audit),'all_sources_match_api':all(r['source_matches_api'] for r in audit),
            'all_sources_match_audited_origin':True,
            'model_written_parameter_blocks':sum(r['source_origin_mode']=='parameter-block' for r in audit),
            'all_supplied_parent_sources_belong_to_this_run':True,'candidates':audit,
            'scope':'Indexed programs and actual source inputs; cannot exclude model pretraining familiarity.'}
    save_json(root/'source_audit.json',result)
    print({k:v for k,v in result.items() if k!='candidates'})

if __name__=='__main__':main()
