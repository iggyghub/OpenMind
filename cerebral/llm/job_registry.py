"""In-process job registry for sub-agent delegations (ADR-0020 amendment,
"background-job registration", decision 3).

Observability, not parallelism: local delegation stays sequential and
blocking (ADR-0020 decision 5). Registering a delegation here does not make
it run concurrently with anything -- the caller still `await`s the
delegation start to finish. What registration buys is a seam for a future
`ctx.jobs` equivalent: while that one `await` is suspended, a long sub-run
is now *listable* (what's running, since when) and *cancellable* (stop it
without an opaque wait), instead of a black box.

ponytail: in-memory dict, dies with the process -- no DB table, no
cross-process visibility. Upgrade to persistence only when a caller needs
to list/cancel jobs from a process other than the one that started them
(that is a DB table + IPC problem, not a job-registry problem).
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Coroutine

Status = str  # "running" | "done" | "failed" | "cancelled"


@dataclass
class Job:
    """A snapshot of one registered delegation. `task` is the live asyncio
    Task backing it -- present for the registry's own use (cancel()); callers
    that just want to list jobs should stick to the other fields.
    """

    id: int
    description: str
    status: Status = "running"
    started_at: float = field(default_factory=time.monotonic)
    finished_at: "float | None" = None
    error: "str | None" = None
    task: "asyncio.Task | None" = field(default=None, repr=False)


class JobRegistry:
    """Tracks delegations started via `start()`. In-memory, per-process."""

    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)

    def start(self, coro: "Coroutine[Any, Any, Any]", *, description: str) -> Job:
        """Schedule `coro` as an asyncio Task and register it. The caller is
        expected to `await job.task` itself right away (see subagent.py) --
        this does not change when the result becomes available, it only
        makes the in-flight run inspectable via list()/cancel() while that
        await is suspended.
        """
        task = asyncio.ensure_future(coro)
        job = Job(id=next(self._ids), description=description, task=task)
        self._jobs[job.id] = job
        task.add_done_callback(lambda t, job=job: self._finish(job, t))
        return job

    def _finish(self, job: Job, task: "asyncio.Task") -> None:
        job.finished_at = time.monotonic()
        if task.cancelled():
            job.status = "cancelled"
        elif task.exception() is not None:
            job.status = "failed"
            job.error = str(task.exception())
        else:
            job.status = "done"

    def list(self) -> list[Job]:
        """All jobs this process has seen, running or finished."""
        return list(self._jobs.values())

    def cancel(self, job_id: int) -> bool:
        """Request cancellation of a running job. Returns False if the job
        id is unknown or already finished (mirrors asyncio.Task.cancel()).
        """
        job = self._jobs.get(job_id)
        if job is None or job.task is None:
            return False
        return job.task.cancel()


# One process-wide registry, same lifetime as the process (ponytail: a
# module-level singleton is the deliberately small answer -- promote to a
# constructor arg only if a caller ever needs more than one registry, e.g.
# per-test isolation beyond what a fresh JobRegistry() in the test gives).
registry = JobRegistry()


if __name__ == "__main__":
    async def _demo() -> None:
        async def ok():
            await asyncio.sleep(0)
            return "done"

        async def boom():
            raise ValueError("nope")

        async def hang():
            await asyncio.sleep(10)

        reg = JobRegistry()
        j1 = reg.start(ok(), description="ok")
        j2 = reg.start(boom(), description="boom")
        j3 = reg.start(hang(), description="hang")

        await j1.task
        try:
            await j2.task
        except ValueError:
            pass
        assert reg.cancel(j3.id) is True
        try:
            await j3.task
        except asyncio.CancelledError:
            pass

        by_id = {j.id: j for j in reg.list()}
        assert by_id[j1.id].status == "done"
        assert by_id[j2.id].status == "failed" and "nope" in by_id[j2.id].error
        assert by_id[j3.id].status == "cancelled"
        assert reg.cancel(999) is False
        print("job_registry self-check OK")

    asyncio.run(_demo())
