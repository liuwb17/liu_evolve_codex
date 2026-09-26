"""Bounded, auditable Chat Completions SSE reception for long GLM reasoning."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from .io import save_json

IDLE_TIMEOUT = 180
TOTAL_TIMEOUT = 3600


def collect_stream(response, folder, call, secret):
    folder = Path(folder)
    progress_file = folder / f'{call:05d}.progress.json'
    progress = {'call_id': call, 'phase': 'receiving', 'events': 0,
                'content_chars': 0, 'reasoning_chars': 0}
    start = time.monotonic()
    last_saved = -10.0
    content, reasoning, data, fields = [], [], [], {}
    finish = None
    received = 0
    save_json(progress_file, progress)
    try:
        with (folder / f'{call:05d}.stream.jsonl').open('x', encoding='utf-8') as transcript:
            while True:
                elapsed = time.monotonic() - start
                if elapsed > TOTAL_TIMEOUT:
                    raise TimeoutError('GLM stream total deadline exceeded')
                raw = response.readline(1_000_001)
                received += len(raw)
                if len(raw) > 1_000_000 or received > 64_000_000:
                    raise OSError('SSE response exceeds transport size bound')
                if not raw:
                    raise OSError('SSE disconnected before DONE; partial source rejected')
                progress['last_activity_utc'] = datetime.now(timezone.utc).isoformat()
                line = raw.decode('utf-8').rstrip('\r\n')
                if line.startswith('data:'):
                    data.append(line[5:].lstrip(' '))
                elif not line and data:
                    event = '\n'.join(data)
                    data = []
                    if event == '[DONE]':
                        if finish is None:
                            raise OSError('SSE ended without finish_reason')
                        progress['phase'] = 'complete'
                        return {**fields, 'choices': [{'index': 0, 'finish_reason': finish,
                            'message': {'role': 'assistant', 'content': ''.join(content),
                                        'reasoning_content': ''.join(reasoning)}}]}
                    chunk = json.loads(event)
                    if not isinstance(chunk, dict):
                        raise OSError('Invalid SSE event type')
                    if 'error' in chunk:
                        raise OSError('Server sent SSE error: ' + str(chunk['error']).replace(secret, '[REDACTED]')[:300])
                    transcript.write(json.dumps(chunk, ensure_ascii=False) + '\n')
                    transcript.flush()
                    progress['events'] += 1
                    for key in ('id', 'created', 'model', 'system_fingerprint', 'request_id'):
                        if chunk.get(key) is not None:
                            if key == 'model' and fields.get(key) and fields[key] != chunk[key]:
                                raise OSError('Model identity changed during stream')
                            fields[key] = chunk[key]
                    if chunk.get('usage'):
                        fields['usage'] = chunk['usage']
                        progress['usage'] = chunk['usage']
                    if chunk.get('model'):
                        progress['response_model'] = chunk['model']
                    for choice in chunk.get('choices') or []:
                        if choice.get('index', 0) != 0:
                            raise OSError('Unexpected multiple completions')
                        delta = choice.get('delta') or {}
                        for name, target, metric in (('content', content, 'content_chars'),
                                                     ('reasoning_content', reasoning, 'reasoning_chars')):
                            value = delta.get(name)
                            if value is not None:
                                if not isinstance(value, str):
                                    raise OSError('Unexpected non-text delta')
                                target.append(value)
                                progress[metric] += len(value)
                        if choice.get('finish_reason'):
                            finish = choice['finish_reason']
                            progress['finish_reason'] = finish
                elapsed = time.monotonic() - start
                if elapsed - last_saved >= 5:
                    progress['elapsed_seconds'] = elapsed
                    save_json(progress_file, progress)
                    last_saved = elapsed
    finally:
        if progress['phase'] != 'complete':
            progress['phase'] = 'interrupted'
        progress['elapsed_seconds'] = time.monotonic() - start
        save_json(progress_file, progress)
