import asyncio
import pytest
from evaluation.jobs import EvaluationJobs


def test_jobs_return_immediately_and_run_serially_with_progress():
    async def run():
        jobs = EvaluationJobs(capacity=2)
        started, release = asyncio.Event(), asyncio.Event()
        async def runner(progress):
            progress(1, 2)
            started.set()
            await release.wait()
            progress(2, 2)
            return {'status': 'partial', 'total': 2}
        first = jobs.submit(runner)
        second = jobs.submit(runner)
        assert first['status'] == second['status'] == 'queued'
        with pytest.raises(RuntimeError):
            jobs.submit(runner)
        await started.wait()
        assert jobs.get(first['job_id'])['completed'] == 1
        assert jobs.get(second['job_id'])['status'] == 'queued'
        release.set()
        await asyncio.gather(*jobs.tasks)
        assert jobs.get(first['job_id'])['result']['status'] == 'partial'
        assert jobs.get(second['job_id'])['status'] == 'completed'
        assert jobs.get('missing') is None
        await jobs.close()
    asyncio.run(run())


def test_timeout_error_and_shutdown_are_terminal():
    async def run():
        jobs = EvaluationJobs(timeout=.01)
        async def slow(progress):
            await asyncio.sleep(10)
        job = jobs.submit(slow)
        await asyncio.gather(*jobs.tasks)
        assert jobs.get(job['job_id'])['status'] == 'failed'
        job = jobs.submit(slow)
        await asyncio.sleep(0)
        await jobs.close()
        assert jobs.get(job['job_id'])['status'] == 'cancelled'
        async def fail(progress):
            raise ValueError('private provider detail')
        job = jobs.submit(fail)
        await asyncio.gather(*jobs.tasks)
        assert 'private' not in jobs.get(job['job_id'])['error']
    asyncio.run(run())
