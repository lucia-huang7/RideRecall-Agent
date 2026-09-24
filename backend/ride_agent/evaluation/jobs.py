"""Jobs."""

import asyncio
import copy
import time
import uuid


class EvaluationJobs:
    def __init__(self, capacity=8, retained=50, timeout=1800):
        self.capacity, self.retained, self.timeout = (capacity, retained, timeout)
        self.jobs = {}
        self.tasks = set()
        self.lock = asyncio.Lock()

    def submit(self, runner):
        if sum((j["status"] in ("queued", "running") for j in self.jobs.values())) >= self.capacity:
            raise RuntimeError("Evaluation queue is full; retry later")
        for key in list(self.jobs):
            if len(self.jobs) < self.retained:
                break
            if self.jobs[key]["status"] not in ("queued", "running"):
                del self.jobs[key]
        job = dict(
            job_id=uuid.uuid4().hex,
            status="queued",
            completed=0,
            total=0,
            created_at=time.time(),
            result=None,
            error=None,
        )
        self.jobs[job["job_id"]] = job
        task = asyncio.create_task(self._execute(job, runner))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.get(job["job_id"])

    def get(self, job_id):
        return copy.deepcopy(self.jobs.get(job_id))

    async def _execute(self, job, runner):
        try:
            async with self.lock:
                job["status"] = "running"

                def progress(completed, total):
                    job.update(completed=completed, total=total)

                job["result"] = await asyncio.wait_for(runner(progress), self.timeout)
                job["status"] = "completed"
        except asyncio.CancelledError:
            job.update(status="cancelled", error="Service stopped; evaluation cancelled")
            raise
        except asyncio.TimeoutError:
            job.update(status="failed", error="Evaluation exceeded its time limit")
        except Exception as ex:
            job.update(status="failed", error=f"Evaluation failed: {type(ex).__name__}")
        finally:
            job["finished_at"] = time.time()

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
