"""Child message loop: prepare models before accepting any real query."""
from __future__ import annotations

import json
import sys
import time
from contextlib import redirect_stdout


def serve(prepare,execute):
    from .runtime import model_imports
    prepared=None
    for line in sys.stdin:
        message={}
        try:
            message=json.loads(line)
            if not isinstance(message,dict) or message.get("protocol")!=1:
                raise ValueError("semantic_protocol_error")
            epoch=message["epoch"]
            if message["type"]=="prepare":
                if prepared is not None:raise ValueError("duplicate_prepare")
                import os
                import psutil
                print(json.dumps({"protocol":1,"type":"hello","epoch":epoch,"pid":os.getpid(),
                    "created":psutil.Process().create_time()}),flush=True)
                started=time.perf_counter()
                with redirect_stdout(sys.stderr),model_imports() as runtime:
                    stages=prepare(message["payload"])
                prepared={"epoch":epoch,**message["payload"]}
                response={"protocol":1,"type":"ready",**prepared,
                          "runtime":{**runtime,"prepare_ms":round((time.perf_counter()-started)*1000,3),"prepare_stages":stages}}
            elif message["type"]=="query":
                payload=message["payload"]
                if prepared is None or epoch!=prepared["epoch"] or any(payload.get(key)!=prepared[key] for key in ("generation","fingerprint")):
                    raise ValueError("query_prepare_identity_mismatch")
                started=time.perf_counter()
                try:
                    with redirect_stdout(sys.stderr),model_imports() as runtime:
                        result=execute(payload)
                    result["runtime"]={**runtime,"worker_ms":round((time.perf_counter()-started)*1000,3)}
                except Exception as exc:
                    result={"error":f"{type(exc).__name__}: {exc}"}
                response={"protocol":1,"type":"result","epoch":epoch,"id":message["id"],"payload":result}
            else:
                raise ValueError("unknown_worker_message")
        except Exception as exc:
            response={"protocol":1,"type":"error","epoch":message.get("epoch") if isinstance(message,dict) else None,"error":f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response,ensure_ascii=False),flush=True)
