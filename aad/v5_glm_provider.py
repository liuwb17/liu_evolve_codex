"""BigModel-only v5 transport; no changes to version-pinned DeepSeek providers."""
import json
from datetime import datetime, timezone
import http.client
import time
import urllib.error
import urllib.request

from .io import digest, save_json
from .provider import BudgetExhausted, environment
from .v3_provider import PROBLEM
from .v4_provider import V4Provider
from .glm_stream import collect_stream, IDLE_TIMEOUT, TOTAL_TIMEOUT

ENDPOINT = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
MODEL = 'glm-5.2'


class GLMTransportError(Exception):
    """Stop the controller on service/configuration errors; never retry invisibly."""


class GLM5Provider(V4Provider):
    def __init__(self, root, model, max_calls, max_tokens_total, key=None):
        if model != MODEL:
            raise ValueError('This transport only permits glm-5.2')
        key = key or environment('ZHIPU_API_KEY')
        if not key or not key.strip():
            raise ValueError('ZHIPU_API_KEY is required; no DeepSeek key fallback')
        super().__init__(root, model, max_calls, max_tokens_total, key)

    def request(self, kind, task):
        if kind not in ('program', 'edit'):
            raise ValueError('Unknown v5 request kind')
        # Kept identical to v5_provider; regression test compares both request bodies.
        rule = ('Return only complete executable C++17 source, NOT JSON. Start with a concise comment describing the hypothesis and changed mechanism, at most eight lines. End with // AAD_V5_COMPLETE.' if kind == 'program' else
                'Return only JSON with hypothesis (short string), parent_sha256, and edits (1..6 objects each containing old and new source strings). Anchors must be unique, disjoint substrings of the ORIGINAL parent. Related declarations, helpers and call sites may be changed together. Keep unrelated code unchanged. Preserve // AAD_V5_COMPLETE.')
        system = ('You are an optimization algorithm researcher. Invent from the problem and this run only; never reconstruct named submissions. '
                  'Reason about the actual search and feasible states before implementation. Do not sacrifice algorithm quality merely to minimize source size. ' + rule)
        body = {'model': self.model, 'max_tokens': 65536, 'stream': True,
                'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low',
                'messages': [{'role': 'system', 'content': system},
                             {'role': 'user', 'content': json.dumps({'problem': PROBLEM, **task}, ensure_ascii=False)}]}
        if kind == 'edit':
            body['response_format'] = {'type': 'json_object'}
        reserve = len(json.dumps(body).encode('utf-8')) + 1024 + 65536
        if self.requests >= self.max_calls or (self.max_tokens_total > 0 and self.charged_tokens + reserve > self.max_tokens_total):
            raise BudgetExhausted('v5 GLM budget exhausted before HTTP')
        call = self.requests
        save_json(self.folder / f'{call:05d}.request.json', body)
        row = {'id': call, 'kind': kind, 'status': 'started', 'reserved_tokens': reserve,
               'requested_model': self.model, 'endpoint': ENDPOINT, 'stream': True,
               'started_at_utc': datetime.now(timezone.utc).isoformat()}
        self.ledger.append(row)
        save_json(self.path, self.ledger)
        start = time.monotonic()
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
        try:
            row['stage'] = 'connecting'
            save_json(self.path, self.ledger)
            with urllib.request.urlopen(request, timeout=IDLE_TIMEOUT) as response:
                row['stage'] = 'streaming'
                row['headers_seconds'] = time.monotonic() - start
                save_json(self.path, self.ledger)
                payload = collect_stream(response, self.folder, call, self.key)
            save_json(self.folder / f'{call:05d}.response.json', payload)
            row.update(usage=payload.get('usage', {}), response_model=payload.get('model'),
                       system_fingerprint=payload.get('system_fingerprint'))
            if str(payload.get('model', '')).lower() != MODEL:
                row['status'] = 'model_mismatch'
                raise GLMTransportError('Returned model is not glm-5.2; experiment stopped')
            choice = payload['choices'][0]
            row['finish_reason'] = choice.get('finish_reason')
            if choice.get('finish_reason') != 'stop':
                raise ValueError('Incomplete response: ' + str(choice.get('finish_reason')))
            content = choice['message'].get('content')
            if not isinstance(content, str) or not content.strip():
                raise ValueError('Empty final response')
            row.update(status='ok', content_sha256=digest(content))
            return {'call_id': call, 'content': content, 'usage': row['usage']}
        except urllib.error.HTTPError as error:
            row['status'] = f'http_{error.code}'
            try:
                detail = json.loads(error.read(8192)).get('error', {})
                row['service_error'] = str(detail).replace(self.key, '[REDACTED]')[:500]
            except (ValueError, OSError, AttributeError):
                pass
            raise GLMTransportError(f'BigModel HTTP {error.code}; stopped without retry') from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            row['status'] = 'network_error'
            row['error_type'] = type(error).__name__
            row['error_detail'] = str(error).replace(self.key, '[REDACTED]')[:500]
            raise GLMTransportError('BigModel network failure; usage unknown; stopped without retry') from None
        except (ValueError, TypeError, KeyError, IndexError) as error:
            row['status'] = 'invalid_response'
            raise ValueError(str(error)) from None
        finally:
            row['seconds'] = time.monotonic() - start
            save_json(self.path, self.ledger)
