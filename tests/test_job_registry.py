"""Tests for cerebral.llm.job_registry (ADR-0020 amendment, H2-S3)."""

import asyncio

import pytest

from cerebral.llm.job_registry import JobRegistry


async def test_job_appears_running_then_done():
    reg = JobRegistry()
    started = asyncio.Event()
    release = asyncio.Event()

    async def work():
        started.set()
        await release.wait()
        return "result"

    job = reg.start(work(), description="do a thing")
    await started.wait()

    assert reg.list() == [job]
    assert job.status == "running"
    assert job.finished_at is None

    release.set()
    result = await job.task

    assert result == "result"
    assert job.status == "done"
    assert job.finished_at is not None


async def test_failed_job_recorded_not_dropped():
    reg = JobRegistry()

    async def boom():
        raise ValueError("kaboom")

    job = reg.start(boom(), description="doomed")

    with pytest.raises(ValueError):
        await job.task

    assert job.status == "failed"
    assert job.error is not None and "kaboom" in job.error
    assert reg.list() == [job]


async def test_cancel_stops_job():
    reg = JobRegistry()
    started = asyncio.Event()

    async def hang():
        started.set()
        await asyncio.sleep(10)

    job = reg.start(hang(), description="long running")
    await started.wait()

    assert reg.cancel(job.id) is True

    with pytest.raises(asyncio.CancelledError):
        await job.task

    assert job.status == "cancelled"


async def test_cancel_unknown_job_returns_false():
    reg = JobRegistry()
    assert reg.cancel(12345) is False


async def test_cancel_already_finished_job_returns_false():
    reg = JobRegistry()

    async def quick():
        return "ok"

    job = reg.start(quick(), description="quick")
    await job.task

    assert reg.cancel(job.id) is False
