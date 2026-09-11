"""Launch only the installed package with an explicit configuration argument."""
import os
import sys
from pathlib import Path


def command(*args):
    return [sys.executable, "-I", "-B", "-X", "utf8", str(Path(__file__).with_name("entry.py")), *map(str, args)]


def environment():
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "KB_ROOT", "KB_CACHE_DIR", "KB_MODEL_HOME"):
        env.pop(key, None)
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_PROGRESS_BARS="1", TOKENIZERS_PARALLELISM="false")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    return env
