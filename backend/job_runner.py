"""Single-consumer, recoverable generation using process-local credentials."""

import asyncio
import contextlib
import json
import re
import time

import httpx

from backend import asset_store
from backend import errors
from backend import job_store
from backend import profile_store
from backend import prompt_builder
from backend.providers import image
from backend.providers import text
from backend.providers import vision


async def _disk(function, *args):
    """Drain a started disk operation before acknowledging cancellation."""
    operation = asyncio.create_task(asyncio.to_thread(function, *args))
    canceled = False
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            canceled = True
        except Exception:  # pylint: disable=broad-exception-caught
            # Retrieve disk errors below; cancellation retains precedence.
            break
    if canceled:
        with contextlib.suppress(Exception):
            operation.result()
        raise asyncio.CancelledError
    return operation.result()


class JobRunner:
    """Own the queue, provider lock and background task lifetime."""

    def __init__(
        self,
        jobs: job_store.JobStore,
        assets: asset_store.AssetStore,
        profiles: profile_store.ProfileStore,
        client: httpx.AsyncClient,
    ):
        self.jobs = jobs
        self.assets = assets
        self.profiles = profiles
        self.client = client
        self.model_lock = asyncio.Lock()
        self._queue = asyncio.Queue()
        self._idle = {}
        self._active = {}
        self._rerun = set()
        self._action_locks = {}
        self._closing = False
        self._worker = None
        self._cleaner = None

    async def start(self) -> None:
        """Recover interrupted jobs and start the sole consumer."""
        if self._worker is not None:
            return
        self._closing = False
        await _disk(self._recover)
        self._worker = asyncio.create_task(self._pump())
        self._cleaner = asyncio.create_task(self._cleanup())

    def _recover(self):
        directory = self.jobs.job_dir(
            "00000000-0000-0000-0000-000000000000"
        ).parent
        if directory.exists():
            self.jobs.recover()

    def _list_existing(self):
        directory = self.jobs.job_dir(
            "00000000-0000-0000-0000-000000000000"
        ).parent
        return self.jobs.list_jobs() if directory.exists() else []

    def _cleanup_existing(self):
        directory = (
            self.jobs.job_dir(
                "00000000-0000-0000-0000-000000000000"
            ).parent.parent
            / "drafts"
        )
        if directory.exists():
            self.assets.cleanup_expired(time.time_ns() // 1_000_000)

    async def close(self) -> None:
        """Stop requests and wait for owned background tasks to exit."""
        self._closing = True
        self._rerun.clear()
        try:
            for task in (self._worker, self._cleaner):
                if task is not None:
                    task.cancel()
            for task in (self._worker, self._cleaner):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
        finally:
            try:
                await _disk(self._recover)
            finally:
                for event in self._idle.values():
                    event.set()
                self._worker = self._cleaner = None

    async def _cleanup(self):
        while True:
            # A locked or disappearing draft must not end periodic cleanup.
            with contextlib.suppress(Exception):
                await _disk(self._cleanup_existing)
            await asyncio.sleep(3600)

    async def enqueue(self, job_id: str) -> None:
        """Schedule a job once, retaining an event until it becomes idle."""
        job_id = self._canonical_id(job_id)
        if job_id in self._active:
            self._rerun.add(job_id)
            return
        if job_id in self._idle and not self._idle[job_id].is_set():
            return
        self._idle[job_id] = asyncio.Event()
        await self._queue.put(job_id)

    async def resume_waiting(self) -> None:
        """Retry credential checks after a role configuration changes."""
        for job in await _disk(self._list_existing):
            if job["status"] == "waiting_credentials":
                await self.enqueue(job["id"])

    async def wait_idle(self, job_id: str) -> None:
        """Wait for queued and active work for this ID."""
        job_id = self._canonical_id(job_id)
        if job_id in self._idle:
            await self._idle[job_id].wait()

    def _canonical_id(self, job_id):
        """Share one validated identity for Windows UUID case aliases."""
        self.jobs.job_dir(job_id)
        return job_id.lower()

    def _action_lock(self, job_id):
        return self._action_locks.setdefault(
            self._canonical_id(job_id), asyncio.Lock()
        )

    async def retry(self, job_id: str, module_id: object) -> dict:
        """Queue only the selected failed module, preserving other results."""
        job_id = self._canonical_id(job_id)
        async with self._action_lock(job_id):

            def mutate(job):
                if job["status"] == "canceled":
                    raise errors.AppError("已取消任务不能重试")
                module = next(
                    (
                        item
                        for item in job["modules"]
                        if item["id"] == str(module_id)
                    ),
                    None,
                )
                if module is None or module["status"] != "failed":
                    raise errors.AppError("只能重试失败模块")
                module.update(status="pending", error=None, errorCode=None)
                job.update(status="queued", stage="queued", error=None)

            job = await _disk(self.jobs.update, job_id, mutate)
            await self.enqueue(job_id)
            return job

    async def cancel(self, job_id: str) -> dict:
        """Cancel active work and preserve terminal job status and assets."""
        job_id = self._canonical_id(job_id)
        async with self._action_lock(job_id):
            return await self._cancel(job_id, preserve_terminal=True)

    async def _cancel(self, job_id, preserve_terminal=False):
        """Persist cancellation and drain the current request or disk write."""
        job_id = self._canonical_id(job_id)

        def mutate(job):
            if preserve_terminal and job["status"] in (
                "completed",
                "partial",
                "failed",
                "canceled",
            ):
                return False
            job.update(status="canceled", stage="canceled")
            return None

        job = await _disk(self.jobs.update, job_id, mutate)
        if job["status"] != "canceled":
            return job
        self._rerun.discard(job_id)
        task = self._active.get(job_id)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return await self._save(job_id, reset_running=True)

    async def delete(self, job_id: str) -> None:
        """Wait for all writes before removing this job and its linked draft."""
        job_id = self._canonical_id(job_id)
        async with self._action_lock(job_id):
            job = await _disk(self.jobs.read, job_id)
            await self._cancel(job_id)
            await self.wait_idle(job_id)
            await _disk(self.jobs.remove, job_id)
            await _disk(self.assets.remove_draft, job["draftId"])

    async def _pump(self):
        while True:
            job_id = await self._queue.get()
            task = asyncio.create_task(self.run_job(job_id))
            self._active[job_id] = task
            try:
                await task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except errors.AppError as error:
                with contextlib.suppress(Exception):
                    await self._save(
                        job_id,
                        status="failed",
                        stage="failed",
                        error=str(error),
                        errorCode=error.code or "provider",
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                # Isolate malformed jobs so the queue keeps processing.
                with contextlib.suppress(Exception):
                    await self._save(
                        job_id,
                        status="failed",
                        stage="failed",
                        error="任务执行失败",
                    )
            finally:
                self._active.pop(job_id, None)
                if job_id in self._rerun and not self._closing:
                    self._rerun.discard(job_id)
                    self._queue.put_nowait(job_id)
                else:
                    self._idle[job_id].set()
                self._queue.task_done()

    async def _save(
        self,
        job_id,
        module_id=None,
        module_values=None,
        reset_running=False,
        **values,
    ):
        def mutate(job):
            if (
                job["status"] == "canceled"
                and values.get("status") != "canceled"
            ):
                if not reset_running:
                    return False
            job.update(values)
            for module in job["modules"]:
                if module["id"] == module_id:
                    module.update(module_values)
                if reset_running and module["status"] == "running":
                    module["status"] = "pending"
            return None

        return await _disk(self.jobs.update, job_id, mutate)

    def _profile(self, job, role):
        config = self.profiles.get_role(role)
        if any(
            config[key] != job[role + "Profile"][key]
            for key in ("provider", "baseUrl", "model")
        ):
            raise errors.AppError(
                "模型设置已变更；请恢复任务创建时的模型 ID 和接口地址",
                code="credentials",
            )
        return config

    def _originals(self, job):
        directory = self.jobs.job_dir(job["id"]) / "originals"
        result = []
        for record in job["images"]:
            path = directory / record["originalName"]
            if path.resolve().parent != directory.resolve():
                raise errors.AppError("图片路径无效")
            result.append((path.read_bytes(), record["mimeType"]))
        return result

    def _write_image(self, job_id, module_id, data):
        if not str(module_id).isdigit():
            raise errors.AppError("模块 ID 无效")
        directory = self.jobs.job_dir(job_id) / "results"
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{module_id}.png"
        temporary = directory / (filename + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(directory / filename)
        return filename

    async def run_job(self, job_id: str) -> None:
        """Analyze once and generate only pending modules in saved order."""
        job_id = self._canonical_id(job_id)
        job = await _disk(self.jobs.read, job_id)
        if job["status"] in ("canceled", "completed"):
            return
        try:
            vision_profile = self._profile(job, "vision")
            image_profile = self._profile(job, "image")
        except errors.AppError as error:
            await self._save(
                job_id,
                status="waiting_credentials",
                stage="waiting_credentials",
                error=str(error),
            )
            return
        references = await _disk(self._originals, job)
        if not job["analysis"]:
            await self._save(
                job_id, status="analyzing", stage="analyzing", error=None
            )
            async with self.model_lock:
                analysis = await vision.analyze_vision(
                    vision_profile,
                    references,
                    job["brief"],
                    job["settings"],
                    self.client,
                )
            analysis["responseModel"] = analysis.pop("response_model")
            job = await self._save(
                job_id, analysis=analysis, status="planning", stage="planning"
            )
        for module in job["modules"]:
            current = await _disk(self.jobs.read, job_id)
            if current["status"] == "canceled":
                return
            if module["status"] != "pending":
                continue
            await self._generate_module(
                job, module, references, vision_profile, image_profile
            )
        current = await _disk(self.jobs.read, job_id)
        done = sum(
            module["status"] == "completed" for module in current["modules"]
        )
        status = (
            "completed"
            if done == len(current["modules"])
            else "partial"
            if done
            else "failed"
        )
        await self._save(
            job_id, status=status, stage=status, currentModule=None
        )

    async def _generate_module(
        self, job, module, references, vision_profile, image_profile
    ):
        job_id = job["id"]
        module_id = module["id"]
        await self._save(
            job_id,
            module_id,
            dict(status="running", attempts=module["attempts"] + 1, error=None),
            status="planning",
            stage="planning",
        )
        try:
            prompt = prompt_builder.build_module_brief(
                module["name"], job["brief"], job["analysis"], job["settings"]
            )
            plan_prompt = (
                f"请为商品详情页模块「{module['name']}」写简短标题和说明，严格只使用以下已知事实。未知信息写“待补充”。"
                '以 JSON 输出 {"headline":"...","body":"..."}。'
                f"已知事实：{'；'.join(job['analysis']['facts'])}；"
                f"未知：{'；'.join(job['analysis']['unknowns'])}；"
                f"用户资料：{job['brief']}。语言：{job['settings'].get('language', '')}"
            )
            async with self.model_lock:
                plan = await text.call_text(
                    vision_profile, plan_prompt, self.client
                )
            try:
                planned = json.loads(
                    re.sub(
                        r"\s*```$",
                        "",
                        re.sub(r"^```(?:json)?\s*", "", plan, flags=re.I),
                    )
                )
                if not isinstance(planned, dict):
                    raise ValueError("Expected object")
            except ValueError:
                planned = dict(headline=module["name"], body=plan)
            headline = str(planned.get("headline") or module["name"])[:160]
            body = str(planned.get("body") or "待补充")[:2000]
            await self._save(
                job_id,
                module_id,
                dict(headline=headline, body=body),
                status="generating",
                stage="generating",
                currentModule=module["name"],
            )
            async with self.model_lock:
                result = await image.generate_image(
                    image_profile,
                    references,
                    f"{prompt}\n模块标题：{headline}\n说明：{body}",
                    prompt_builder.module_ratio(
                        module["name"], job["settings"].get("ratio", "自动适配")
                    ),
                    job["settings"].get("quality", ""),
                    self.client,
                )
            filename = await _disk(
                self._write_image, job_id, module_id, result.data
            )
            await self._save(
                job_id,
                module_id,
                dict(
                    imageFile=filename,
                    imageUrl=f"/api/jobs/{job_id}/images/{module_id}",
                    status="completed",
                    error=None,
                ),
            )
        except errors.AppError as error:
            await self._save(
                job_id,
                module_id,
                dict(
                    status="failed",
                    error=str(error),
                    errorCode=error.code or "provider",
                ),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            # Persist unexpected module failures without exposing secrets.
            await self._save(
                job_id,
                module_id,
                dict(
                    status="failed", error="模块生成失败", errorCode="provider"
                ),
            )
