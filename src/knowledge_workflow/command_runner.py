"""Bounded subprocess execution with visible progress during quiet stages."""
import os
import queue
import subprocess
import sys
import threading
import time


def run(argv, *, phase, env=None, cwd=None, timeout=900):
    print(phase + ": starting", file=sys.stderr, flush=True)
    process = subprocess.Popen(list(map(str, argv)), cwd=cwd, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        from .owned_process import track, terminate
        track(process)
    except ModuleNotFoundError as exc:
        if exc.name != "psutil":
            raise
        # The standard-library bootstrap runs before its isolated dependencies exist.
        # A forced stop is explicitly unconfirmed instead of claiming child cleanup.
        def terminate(process):
            process.terminate()
            process.wait(timeout=3)
            raise RuntimeError(phase + ": forced bootstrap cleanup requires verification")
    output = []
    events = queue.Queue()
    def read():
        for line in process.stdout:
            events.put(line)
        events.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    started = last = time.monotonic()
    ended = False
    try:
        while not ended or process.poll() is None:
            if time.monotonic() - started > timeout:
                terminate(process)
                raise TimeoutError(phase + " exceeded its deadline")
            try:
                event = events.get(timeout=.25)
                if event is None:
                    ended = True
                else:
                    output.append(event)
                    if sum(map(len, output)) > 64000:
                        output = output[-80:]
            except queue.Empty:
                pass
            if time.monotonic() - last >= 5:
                print(f"{phase}: working ({int(time.monotonic()-started)}s)", file=sys.stderr, flush=True)
                last = time.monotonic()
        code = process.wait(timeout=3)
        if code:
            raise RuntimeError(phase + " failed: " + "".join(output)[-8000:])
        print(phase + ": complete", file=sys.stderr, flush=True)
        return "".join(output)
    finally:
        if process.poll() is None:
            terminate(process)
        reader.join(timeout=3)
        process.stdout.close()
