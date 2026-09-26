"""Download a pinned, unmodified author-published human reference."""
import base64
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BLOB='96f04fdaafdb5b9c785ee8dcc2061409e22f144a'
url=f'https://api.github.com/repos/kusano/ahc001/git/blobs/{BLOB}'
request=urllib.request.Request(url,headers={'User-Agent':'MOSAIC-human-reference-evaluation'})
data=json.load(urllib.request.urlopen(request,timeout=30))
raw=base64.b64decode(data['content'])
assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==BLOB
folder=ROOT/'data/human_references/kusano'
folder.mkdir(parents=True,exist_ok=True)
(folder/'original.cpp').write_bytes(raw)
record={'author':'kusano','source':'https://github.com/kusano/ahc001',
        'blob_sha1':BLOB,'sha256':hashlib.sha256(raw).hexdigest(),
        'source_modified':False,'champion_status':'not verified; do not label champion',
        'provenance':'author-published repository, not bundled ALE-Bench human source',
        'retrieved_date':'2026-09-20'}
(folder/'provenance.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record,indent=2))
