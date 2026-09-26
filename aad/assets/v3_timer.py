"""Trusted standalone candidate timer. No solver, dataset or credential is mounted."""
import json
import os
import signal
import subprocess
import sys
import threading
import time

data=sys.stdin.buffer.read()
seconds=float(sys.argv[1])
limit=min(5.0,seconds+0.15)
start=time.monotonic()
process=subprocess.Popen(['/work/solution',str(seconds)],stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,
    env={'PATH':'/usr/bin:/bin'})
buffers=[bytearray(),bytearray()]
overflow=threading.Event()
def drain(stream,buffer):
    while True:
        block=stream.read(4096)
        if not block:break
        remaining=65536-len(buffer)
        buffer.extend(block[:max(0,remaining)])
        if len(block)>remaining:overflow.set()
def feed():
    try:process.stdin.write(data)
    except (BrokenPipeError,OSError):pass
    finally:process.stdin.close()
threads=[threading.Thread(target=drain,args=(stream,buffer),daemon=True)
         for stream,buffer in zip((process.stdout,process.stderr),buffers)]
threads.append(threading.Thread(target=feed,daemon=True))
for thread in threads:thread.start()
status='AC'
while process.poll() is None:
    if overflow.is_set():status='OLE';break
    if time.monotonic()-start>limit:status='TLE';break
    time.sleep(.002)
if process.poll() is None:
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
process.wait(timeout=2)
for thread in threads:thread.join(timeout=1)
if overflow.is_set():status='OLE'
if status=='AC' and process.returncode:status='RE'
print(json.dumps({'status':status,'stdout':buffers[0].decode(errors='replace'),
    'stderr':buffers[1].decode(errors='replace'),'seconds':time.monotonic()-start,
    'returncode':process.returncode,'candidate_limit_seconds':limit}))
