"""Parent-side model lifecycle; query serialization never owns the state lock."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from .owned_process import track, remember, terminate as terminate_process


class ModelWorkerClient:
    def __init__(self,*,entry,cache,timeout=30,idle_seconds=600,startup_seconds=60,command=None):
        if min(timeout,idle_seconds,startup_seconds)<=0:
            raise ValueError("worker time budgets must be positive")
        self.entry,self.cache=Path(entry),Path(cache)
        self.command = command
        self.timeout,self.idle_seconds,self.startup_seconds=timeout,idle_seconds,startup_seconds
        self.process=None
        self.key=None
        self.generation=None
        self.phase="stopped"
        self.epoch=0
        self.starts=0
        self.request_number=0
        self.active=None
        self.reply=None
        self.last_error=""
        self.initialization={}
        self.started_at=0.0
        self.startup_deadline=0.0
        self.last_used=0.0
        self.lock=threading.Lock()
        self.condition=threading.Condition(threading.RLock())
        self.disposal_lock=threading.Lock()
        self.retired=[]
        self.spawning=False
        self.closed=threading.Event()
        self.watcher=threading.Thread(target=self._watch,name="knowledge-model-lifecycle",daemon=True)
        self.watcher.start()
        atexit.register(self.close)

    def _detach_locked(self,phase,error=""):
        process,self.process=self.process,None
        self.epoch+=1
        self.phase,self.last_error=phase,error
        self.active,self.reply=None,None
        if process is not None and process not in self.retired:self.retired.append(process)
        self.condition.notify_all()
        return process

    def _dispose(self,process):
        if process is None:return
        with self.disposal_lock:
            terminate_process(process)
            for stream in (process.stdin,process.stdout):
                if stream is not None and not stream.closed:stream.close()
            with self.condition:
                if process in self.retired:self.retired.remove(process)
                self.condition.notify_all()

    def _fail(self,epoch,error):
        with self.condition:
            process=self._detach_locked("failed",error) if epoch==self.epoch else None
        self._dispose(process)

    def _watch(self):
        interval=min(.25,self.idle_seconds/2,self.startup_seconds/4)
        while not self.closed.wait(interval):
            process=None
            with self.condition:
                now=time.monotonic()
                if self.process is None:continue
                if self.phase=="starting" and now>=self.startup_deadline:
                    process=self._detach_locked("failed","semantic_startup_timeout")
                elif self.active is not None and now>=self.active["deadline"]:
                    process=self._detach_locked("failed","semantic_timeout")
                elif self.phase=="ready" and self.active is None and now-self.last_used>=self.idle_seconds:
                    process=self._detach_locked("stopped")
            try:self._dispose(process)
            except (OSError,RuntimeError,subprocess.SubprocessError):
                # Retain the owned handle. Starting another worker is refused
                # until cleanup succeeds, rather than hiding a live process.
                with self.condition:self.last_error="semantic_termination_failed"

    def terminate(self):
        with self.condition:
            process=self._detach_locked("closed" if self.closed.is_set() else "stopped","worker_closed" if self.closed.is_set() else "worker_cancelled")
        self._dispose(process)

    def close(self):
        self.closed.set()
        self.terminate()
        with self.condition:
            deadline=time.monotonic()+5
            while self.spawning:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise RuntimeError("semantic_spawn_cleanup_pending")
                self.condition.wait(timeout=remaining)
        with self.condition:retired=list(self.retired)
        for process in retired:self._dispose(process)
        if self.watcher is not threading.current_thread():self.watcher.join(timeout=2)

    def _environment(self):
        from .processes import environment
        env=environment()
        if not any(name in env for name in ("OPENBLAS_NUM_THREADS","OPENBLAS_DEFAULT_NUM_THREADS","GOTO_NUM_THREADS","OMP_NUM_THREADS")):
            env["OPENBLAS_NUM_THREADS"]="1"
        return env

    @staticmethod
    def _send(process,message):
        process.stdin.write(json.dumps(message,ensure_ascii=False)+"\n")
        process.stdin.flush()

    def _start(self,key,payload=None):
        payload=payload or {}
        with self.condition:
            if self.closed.is_set():raise RuntimeError("worker_closed")
            previous=self._detach_locked("stopped")
        self._dispose(previous)
        with self.condition:
            if self.retired:raise RuntimeError("semantic_cleanup_pending")
            if self.closed.is_set():raise RuntimeError("worker_closed")
            self.epoch+=1
            epoch=self.epoch
            self.phase="starting"
            self.key,self.generation=key,payload.get("generation")
            self.started_at=time.monotonic()
            self.startup_deadline=self.started_at+self.startup_seconds
            self.last_used=self.started_at
            self.last_error=""
            self.initialization={}
            self.spawning=True
        try:
            self.cache.mkdir(parents=True,exist_ok=True)
            with (self.cache/f"worker-{os.getpid()}.log").open("ab") as log:
                process=subprocess.Popen(self.command or [sys.executable,"-B","-X","utf8",str(self.entry),"--worker"],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding="utf-8",bufsize=1,
                    env=self._environment(),creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            track(process)
            with self.condition:
                if self.closed.is_set() or self.epoch!=epoch:
                    self.retired.append(process)
                    cancelled=True
                else:
                    self.process=process
                    self.starts+=1
                    cancelled=False
            if cancelled:
                self._dispose(process)
                raise RuntimeError("worker_cancelled")
        except (OSError,ValueError,RuntimeError):
            self._fail(epoch,"semantic_start_failed")
            raise
        finally:
            with self.condition:
                self.spawning=False
                self.condition.notify_all()
        threading.Thread(target=self._read,args=(process,epoch),daemon=True,name="knowledge-model-reader").start()
        try:
            self._send(process,{"protocol":1,"type":"prepare","epoch":epoch,
                       "payload":{"generation":self.generation,"fingerprint":key}})
        except (OSError,ValueError):
            self._fail(epoch,"semantic_start_failed")
            raise

    def _read(self,process,epoch):
        try:
            for line in process.stdout:
                try:message=json.loads(line)
                except (ValueError,TypeError):
                    self._fail(epoch,"semantic_protocol_error");return
                error=""
                with self.condition:
                    if process is not self.process or epoch!=self.epoch:return
                    if not isinstance(message,dict) or message.get("protocol")!=1:
                        error="semantic_protocol_error"
                    elif message.get("epoch")!=epoch:
                        continue
                    elif message.get("type")=="hello":
                        try:remember(process,message["pid"],message["created"])
                        except (KeyError,ValueError,RuntimeError,OSError) as exc:error=str(exc)
                    elif message.get("type")=="ready":
                        if self.phase!="starting":continue
                        if message.get("generation")!=self.generation or message.get("fingerprint")!=self.key:
                            error="semantic_ready_identity_mismatch"
                        elif time.monotonic()>=self.startup_deadline:
                            error="semantic_startup_timeout"
                        else:
                            self.phase="ready"
                            self.initialization=message.get("runtime",{})
                            self.last_used=time.monotonic()
                            self.condition.notify_all()
                    elif message.get("type")=="result":
                        if self.active is None or message.get("id")!=self.active["id"]:continue
                        if time.monotonic()>=self.active["deadline"]:
                            error="semantic_timeout"
                        elif not isinstance(message.get("payload"),dict):
                            error="semantic_protocol_error"
                        else:
                            self.reply=message["payload"]
                            self.active=None
                            self.last_used=time.monotonic()
                            self.condition.notify_all()
                    elif message.get("type")=="error":
                        error=str(message.get("error") or "semantic_start_failed")
                    else:
                        error="semantic_protocol_error"
                if error:
                    self._fail(epoch,error);return
        except (OSError,ValueError):
            return
        finally:
            self._fail(epoch,"semantic_worker_exited")

    def _state_error(self,epoch):
        if self.closed.is_set():raise RuntimeError("worker_closed")
        if epoch!=self.epoch or self.process is None or self.phase in {"failed","stopped","closed"}:
            reason=self.last_error or "worker_cancelled"
            if reason in {"semantic_timeout","semantic_startup_timeout"}:raise TimeoutError(reason)
            raise RuntimeError(reason)

    def call(self,payload,key):
        deadline=time.monotonic()+self.timeout
        if not self.lock.acquire(timeout=max(.001,deadline-time.monotonic())):
            raise TimeoutError("semantic_queue_timeout")
        epoch=None
        try:
            with self.condition:
                if self.closed.is_set():raise RuntimeError("worker_closed")
                start=self.process is None or self.process.poll() is not None or self.key!=key or self.generation!=payload.get("generation")
            if start:self._start(key,payload)
            with self.condition:
                epoch=self.epoch
                self.last_used=time.monotonic()
                while self.phase=="starting":
                    self._state_error(epoch)
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError("semantic_initializing")
                    self.condition.wait(timeout=min(remaining,max(.001,self.startup_deadline-time.monotonic())))
                self._state_error(epoch)
                if time.monotonic()>=deadline:raise TimeoutError("semantic_initializing")
                self.request_number+=1
                identifier=self.request_number
                self.active={"id":identifier,"deadline":deadline}
                self.reply=None
                process=self.process
            # A large stdin write must not hold the lifecycle state lock. The
            # watchdog can terminate a wedged worker even during this write.
            self._send(process,{"protocol":1,"type":"query","epoch":epoch,"id":identifier,"payload":payload})
            with self.condition:
                while self.reply is None:
                    self._state_error(epoch)
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError("semantic_timeout")
                    self.condition.wait(timeout=remaining)
                result=self.reply
                self.reply=None
            if result.get("error"):
                raise RuntimeError(result["error"])
            return result
        except TimeoutError as exc:
            if epoch is not None and str(exc)!="semantic_initializing":self._fail(epoch,str(exc))
            raise
        except (OSError,ValueError,RuntimeError) as exc:
            if epoch is not None:self._fail(epoch,str(exc))
            raise
        finally:
            with self.condition:self.last_used=time.monotonic()
            self.lock.release()

    def status(self):
        with self.condition:
            live=self.process is not None and self.process.poll() is None
            return {"state":"resident" if live else "stopped","pid":self.process.pid if live else None,
                    "idle_seconds":self.idle_seconds,"deadline_seconds":self.timeout,"model_fingerprint":self.key,
                    "phase":self.phase,"generation":self.generation,"epoch":self.epoch,"starts":self.starts,
                    "startup_deadline_seconds":self.startup_seconds,"last_error":self.last_error,
                    "initialization":self.initialization,"spawning":self.spawning,
                    "cleanup_pending_pids":[p.pid for p in self.retired if p.poll() is None]}
