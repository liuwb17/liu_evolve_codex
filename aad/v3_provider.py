"""Whole-program generation from problem-only prompts, with audited request limits."""
import json
import time
import urllib.error
import urllib.request
from .provider import ChatProposer,BudgetExhausted
from .io import save_json

PROBLEM='''Design a standalone C++17 optimizer. Input: n in [50,200], then n lines of
distinct integer x_i,y_i in [0,9999] and positive integer target areas r_i summing
to 100000000. Output n integer rectangles a_i b_i c_i d_i in input order,
0<=a_i<c_i<=10000, 0<=b_i<d_i<=10000, with no positive-area overlaps.
If a rectangle contains its point (x_i+0.5,y_i+0.5), satisfaction is
1-(1-min(area_i,r_i)/max(area_i,r_i))^2; otherwise satisfaction is zero.
External score is round(1e9 * mean satisfaction). Maximize generalizable score.
Read stdin; write only n rectangles to stdout. Standard C++17 library only.
Your full program must parse argv[1] as its wall-clock search budget in seconds
(default 4.8), supporting both 0.6 and 4.8 second evaluations. Finish promptly,
including input/output and setup: hard process limit is min(5.0,budget+0.15).
One CPU, 512 MiB. No files, networking, system commands, environment variables,
runtime compilation, known-input lookup, or evaluator inspection. Do not rely on
hardware cycle-frequency constants. Algorithm randomness must be reproducible.
You are responsible for the entire initial algorithm and all subsequent code.
No initial solution or human-written algorithm skeleton is provided.'''

class ProgramProposer(ChatProposer):
    def generate(self,task):
        if self.requests>=self.config['max_requests']:raise BudgetExhausted('Request budget exhausted')
        system=('You are an independent optimization algorithm researcher. Invent and test ideas from the problem specification. '
            'Return only valid JSON with hypothesis (string), family (short algorithm-family description), '
            'and code (complete standalone C++17 source string). Do not cite or reconstruct named contest submissions. '
            'Use only the supplied problem and this experiment\'s candidate lineage. '
            'Reason about complexity, feasibility, and score improvement before writing code. '
            'Do not put explanations outside JSON. The code must be fully executable, not pseudocode.')
        if task['operation']=='parameter_defaults':
            system=('Return valid JSON with hypothesis, family, and code strings. '
                'Here code must contain ONLY the requested C++ constexpr parameter-declaration block, '
                'including its exact BEGIN and END markers. Copy the supplied numeric defaults exactly. '
                'Do not return a whole program, do not alter algorithm logic, and do not add other declarations or comments. '
                'The controller will splice this model-written block into the supplied, previously model-generated parent.')
        body={'model':self.config['model'],'messages':[{'role':'system','content':system},
            {'role':'user','content':json.dumps({'problem':PROBLEM,**task},ensure_ascii=False)}],
            'max_tokens':self.config['max_output_tokens'],**self.config.get('request_options',{})}
        exploring=task['operation'] in ('independent_initial_design','independent_restart','structural_redesign','novel_design') or task.get('donor_selection_reason')=='recent-structural-mechanism'
        if body.get('thinking',{}).get('type')=='enabled' and self.config.get('exploration_effort') and exploring:
            body['reasoning_effort']=self.config['exploration_effort']
        ident=self.requests
        save_json(self.folder/f'{ident:05d}.request.json',body)
        entry={'id':ident,'status':'started','usage':{}}
        self.ledger.append(entry);save_json(self.ledger_path,self.ledger)
        request=urllib.request.Request(self.url,data=json.dumps(body).encode(),headers={
            'Content-Type':'application/json','Authorization':'Bearer '+self.key})
        started=time.monotonic()
        try:
            with urllib.request.urlopen(request,timeout=self.config['request_timeout']) as response:
                raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise ValueError('Response exceeds 2 MB')
            payload=json.loads(raw);save_json(self.folder/f'{ident:05d}.response.json',payload)
            entry['usage']=payload.get('usage',{})
            entry['requested_model']=body['model']
            entry['response_model']=payload.get('model')
            entry['system_fingerprint']=payload.get('system_fingerprint')
            content=payload['choices'][0]['message']['content']
            if not isinstance(content,str):raise ValueError('Missing final text answer; check finish_reason and reasoning budget')
            if content.strip().startswith('```'):
                content='\n'.join(content.strip().splitlines()[1:-1])
            proposal=json.loads(content)
            if not isinstance(proposal,dict) or not all(isinstance(proposal.get(k),str) for k in ('hypothesis','family','code')):
                raise ValueError('Expected hypothesis/family/code strings')
            json.dumps(proposal,allow_nan=False)
            proposal['code'].encode('utf-8')
            if len(proposal['code'])>120000:raise ValueError('Program too large')
            entry['status']='ok'
            return {**proposal,'call_id':ident,'usage':entry['usage']}
        except urllib.error.HTTPError as error:
            entry['status']=f'http_{error.code}'
            raise RuntimeError(f'Model endpoint HTTP {error.code}') from None
        except (urllib.error.URLError,TimeoutError) as error:
            entry['status']='network_error'
            reason=getattr(error,'reason',error)
            entry['network_error_type']=type(reason).__name__
            entry['network_errno']=getattr(reason,'errno',None)
            raise RuntimeError(f'Model request failed or timed out ({type(reason).__name__}, errno={getattr(reason,"errno",None)})') from None
        except (KeyError,IndexError,TypeError,ValueError) as error:
            entry['status']='invalid_response'
            raise ValueError(f'Invalid generated program response: {error}') from None
        finally:
            entry['seconds']=time.monotonic()-started
            save_json(self.ledger_path,self.ledger)
