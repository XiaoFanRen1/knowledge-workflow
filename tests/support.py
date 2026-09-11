import tempfile
import time
from pathlib import Path

from knowledge_workflow.config import initialize
from knowledge_workflow.storage import Store
from knowledge_workflow.service import KnowledgeService


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kw-public-test-")
        self.root = Path(self.temp.name).resolve()
        self.config = initialize(self.root / "知识 library", self.root / "missing-model")
        Store(self.config).initialize_empty()
        self.service = KnowledgeService(self.config)

    def close(self):
        from knowledge_workflow import jobs, runner
        self.service.close()
        store = Store(self.config)
        with store.control() as db:
            ids = [row[0] for row in db.execute("SELECT job_id FROM jobs")]
        for jid in ids:
            result = jobs.cancel(self.config, jid)
            until = time.monotonic() + 10
            while time.monotonic() < until:
                result = jobs.status(self.config, jid)
                owned = [(result[k + "_pid"], result[k + "_created"])
                         for k in ("supervisor", "launcher") if result.get(k + "_pid")]
                if not owned or all(jobs._identity(pid, created) is False for pid, created in owned):
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("owned_test_process_cleanup_unconfirmed: " + str(self.root))
        if Path(self.temp.name).resolve() != self.root:
            raise RuntimeError("fixture_identity_changed")
        if runner.status(self.config)["state"] != "stopped":
            stopped = runner.stop(self.config)
            if not stopped["ok"]:
                raise RuntimeError("fixture runner cleanup unconfirmed")
        self.temp.cleanup()

    def capture(self, op="note-1", **kwargs):
        return self.service.capture_knowledge(operation_id=op,
            title=kwargs.pop("title", "Garden sensor reset"),
            body=kwargs.pop("body", "The garden sensor clears its sample ring after a confirmed reset acknowledgement. " * 8),
            **kwargs)
