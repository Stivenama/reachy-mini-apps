"""Local Spanish worker with bounded decoder lifetime and throttled hypotheses."""
import base64
import json
import os
import sys
import time
from pathlib import Path

def main():
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    model=Model(os.getenv('CLASSROOM_SPANISH_MODEL',str(Path.home()/'apps/classroom_models/vosk-model-small-es-0.42')))
    def recognizer(): return KaldiRecognizer(model,16000)
    rec=recognizer()
    turn=samples=decoder_samples=processed=0
    block=None
    pending=bytearray()
    last_partial=''
    last_check=last_progress=0.
    def emit(data): print(json.dumps(data,ensure_ascii=False),flush=True)
    def progress(force=False):
        nonlocal last_progress
        now=time.monotonic()
        if force or now-last_progress>=.25:
            emit({'type':'progress','processed_bytes':processed})
            last_progress=now
    def final(force=False):
        nonlocal rec,turn,samples,decoder_samples,last_partial
        data=json.loads(rec.FinalResult() if force else rec.Result())
        emit({'type':'final','block':block,'turn':turn,'text':data.get('text',''),'seconds':samples/16000})
        turn+=1; samples=0; last_partial=''
        if force or decoder_samples>=16000*60:
            # A fresh decoder frees its acoustic/history state; model weights remain shared.
            rec=recognizer(); decoder_samples=0
            emit({'type':'renewed'})
    def consume(pcm):
        nonlocal samples,decoder_samples,processed,last_partial,last_check
        samples+=len(pcm)//2; decoder_samples+=len(pcm)//2
        if rec.AcceptWaveform(pcm): final()
        elif samples>=16000*60: final(True)
        else:
            now=time.monotonic()
            # Throttle BEFORE computing the expensive lattice hypothesis.
            if now-last_check>=.3:
                last_check=now
                text=json.loads(rec.PartialResult()).get('partial','')
                if text!=last_partial:
                    emit({'type':'partial','block':block,'turn':turn,'text':text,'seconds':samples/16000})
                    last_partial=text
        processed+=len(pcm); progress()
    emit({'type':'ready','language':'es','engine':'vosk-es'})
    for line in sys.stdin:
        msg=json.loads(line)
        if msg['type']=='feed':
            if block is not None and msg['block']!=block and pending:
                consume(bytes(pending));pending.clear();final(True)
            block=msg['block']
            pending.extend(base64.b64decode(msg['audio'],validate=True))
            while len(pending)>=3200:
                consume(bytes(pending[:3200]));del pending[:3200]
        elif msg['type']=='flush':
            if pending: consume(bytes(pending));pending.clear()
            final(True);progress(True)
            emit({'type':'flushed','token':msg['token']})
        elif msg['type']=='stop': return

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'type':'error','message':str(exc)}),flush=True)
        raise
