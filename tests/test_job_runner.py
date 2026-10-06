"""Runner integration tests exercise persistence and provider ordering."""

import asyncio
import contextlib
import threading
import io
from PIL import Image
import httpx
from backend import main, job_runner, errors
from backend.api.jobs import create_job
from backend.providers import image


def fixture(tmp_path, monkeypatch):
    app = main.create_app(tmp_path)
    state = app.state
    for role in ("vision", "image"):
        state.profiles.save_role(
            dict(
                role=role,
                provider="openai",
                baseUrl="https://example.com/v1",
                model=role,
                apiKey="secret",
            )
        )
    draft = state.assets.create_draft()["draftId"]
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    for name in ("a.png", "b.png"):
        state.assets.add_image(draft, buffer.getvalue(), name)
    payload = dict(
        draftId=draft,
        brief="Tea",
        modules=["首屏主视觉", "商品细节图", "品牌故事图"],
        settings={"ratio": "自动适配"},
    )

    async def analyze(*args):
        assert len(args[1]) == 2
        return dict(
            facts=["tea"],
            unknowns=["size"],
            text="tea",
            response_model="vision",
        )

    async def plan(*_):
        return '{"headline":"Tea","body":"待补充"}'

    monkeypatch.setattr(job_runner.vision, "analyze_vision", analyze)
    monkeypatch.setattr(job_runner.text, "call_text", plan)
    return state, payload, buffer.getvalue()


def test_two_images_three_modules_partial_failure_and_resume(
    tmp_path, monkeypatch
):
    state, payload, png = fixture(tmp_path, monkeypatch)
    calls = 0

    async def generate(*args):
        nonlocal calls
        calls += 1
        assert len(args[1]) == 2
        if calls == 2:
            raise errors.AppError("failed", 502, "provider")
        return image.GeneratedImage(png, "image/png", "image")

    monkeypatch.setattr(job_runner.image, "generate_image", generate)

    async def run():
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            await runner.start()
            job = create_job(payload, state.assets, state.jobs, state.profiles)
            await runner.enqueue(job["id"])
            await runner.wait_idle(job["id"])
            job = state.jobs.read(job["id"])
            assert job["status"] == "partial"
            assert (
                state.jobs.job_dir(job["id"])
                / "results"
                / job["modules"][0]["imageFile"]
            ).exists()
            assert job["analysis"]["responseModel"] == "vision"

            def retry(current):
                current["status"] = "waiting_credentials"
                current["modules"][1]["status"] = "pending"

            state.jobs.update(job["id"], retry)
            await runner.resume_waiting()
            await runner.wait_idle(job["id"])
            job = state.jobs.read(job["id"])
            assert job["status"] == "completed"
            assert [m["attempts"] for m in job["modules"]] == [1, 2, 1]
            await runner.close()

    asyncio.run(run())


def test_runner_serializes_two_jobs(tmp_path, monkeypatch):
    state, payload, png = fixture(tmp_path, monkeypatch)
    active = max_active = 0

    async def generate(*_):
        nonlocal active, max_active
        active += 1
        max_active = max(active, max_active)
        await asyncio.sleep(0.01)
        active -= 1
        return image.GeneratedImage(png, "image/png", "image")

    monkeypatch.setattr(job_runner.image, "generate_image", generate)

    async def run():
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            await runner.start()
            ids = [
                create_job(payload, state.assets, state.jobs, state.profiles)[
                    "id"
                ]
                for _ in range(2)
            ]
            for identifier in ids:
                await runner.enqueue(identifier)
            await asyncio.gather(
                *(runner.wait_idle(identifier) for identifier in ids)
            )
            assert all(
                state.jobs.read(identifier)["status"] == "completed"
                for identifier in ids
            )
            await runner.close()

    asyncio.run(run())
    assert max_active == 1


def test_output_is_saved_before_completed_status(tmp_path, monkeypatch):
    state, payload, png = fixture(tmp_path, monkeypatch)

    async def generate(*_):
        return image.GeneratedImage(png, "image/png", "image")

    monkeypatch.setattr(job_runner.image, "generate_image", generate)
    # Inspect lifecycle ordering at the real operation boundary.
    # pylint: disable-next=protected-access
    write = state.jobs._write
    observed = []

    def checked(job):
        for module in job["modules"]:
            if module["status"] == "completed":
                assert (
                    state.jobs.job_dir(job["id"])
                    / "results"
                    / module["imageFile"]
                ).exists()
                observed.append(module["id"])
        return write(job)

    monkeypatch.setattr(state.jobs, "_write", checked)

    async def run():
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            await runner.start()
            job = create_job(payload, state.assets, state.jobs, state.profiles)
            await runner.enqueue(job["id"])
            await runner.wait_idle(job["id"])
            await runner.close()

    asyncio.run(run())
    assert set(observed) == {"1", "2", "3"}


def test_restart_preserves_completed_and_cancel_stops_request(
    tmp_path, monkeypatch
):
    state, payload, png = fixture(tmp_path, monkeypatch)

    async def run():
        entered = asyncio.Event()

        async def generate(*_):
            entered.set()
            await asyncio.Event().wait()
            return image.GeneratedImage(png, "image/png", "image")

        monkeypatch.setattr(job_runner.image, "generate_image", generate)
        job = create_job(payload, state.assets, state.jobs, state.profiles)

        def interrupted(current):
            current["status"] = "generating"
            current["modules"][0].update(status="completed", attempts=1)
            current["modules"][1]["status"] = "running"

        state.jobs.update(job["id"], interrupted)
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            await runner.start()
            saved = state.jobs.read(job["id"])
            assert saved["status"] == "waiting_credentials"
            assert saved["modules"][0]["status"] == "completed"
            assert saved["modules"][1]["status"] == "pending"
            await runner.resume_waiting()
            await asyncio.wait_for(entered.wait(), 2)
            await runner.cancel(job["id"])
            await runner.wait_idle(job["id"])
            saved = state.jobs.read(job["id"])
            assert saved["status"] == "canceled"
            assert saved["modules"][0]["attempts"] == 1
            assert saved["modules"][1]["status"] == "pending"
            assert saved["modules"][2]["attempts"] == 0
            await runner.close()

    asyncio.run(run())


def test_analysis_failure_keeps_provider_error_code(tmp_path, monkeypatch):
    state, payload, _ = fixture(tmp_path, monkeypatch)

    async def fail(*_):
        raise errors.AppError("模型不支持视觉", 502, "vision_unsupported")

    monkeypatch.setattr(job_runner.vision, "analyze_vision", fail)

    async def run():
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            await runner.start()
            job = create_job(payload, state.assets, state.jobs, state.profiles)
            await runner.enqueue(job["id"])
            await runner.wait_idle(job["id"])
            saved = state.jobs.read(job["id"])
            assert saved["errorCode"] == "vision_unsupported"
            assert saved["error"] == "模型不支持视觉"
            await runner.close()

    asyncio.run(run())


def test_cancel_waits_for_inflight_file_write(tmp_path, monkeypatch):

    state, payload, png = fixture(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    async def generate(*_):
        return image.GeneratedImage(png, "image/png", "image")

    monkeypatch.setattr(job_runner.image, "generate_image", generate)

    async def run():
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            # Inspect lifecycle ordering at the real operation boundary.
            # pylint: disable-next=protected-access
            original = runner._write_image

            def delayed(*args):
                entered.set()
                release.wait(3)
                return original(*args)

            monkeypatch.setattr(runner, "_write_image", delayed)
            await runner.start()
            job = create_job(payload, state.assets, state.jobs, state.profiles)
            await runner.enqueue(job["id"])
            await asyncio.to_thread(entered.wait, 2)
            canceled = asyncio.create_task(runner.cancel(job["id"]))
            try:
                await asyncio.sleep(0.05)
                assert not canceled.done()
            finally:
                release.set()
                await canceled
                await runner.close()

    asyncio.run(run())


def test_resume_during_active_waiting_transition_is_not_lost(
    tmp_path, monkeypatch
):
    state, payload, png = fixture(tmp_path, monkeypatch)

    async def generate(*_):
        return image.GeneratedImage(png, "image/png", "image")

    monkeypatch.setattr(job_runner.image, "generate_image", generate)

    async def run():
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            await runner.start()
            job = create_job(payload, state.assets, state.jobs, state.profiles)
            config = state.profiles.get_role("vision")
            config["model"] = "changed"
            waiting = asyncio.Event()
            release = asyncio.Event()
            # Inspect lifecycle ordering at the real operation boundary.
            # pylint: disable-next=protected-access
            save = runner._save

            async def pause(*args, **kwargs):
                result = await save(*args, **kwargs)
                if kwargs.get("status") == "waiting_credentials":
                    waiting.set()
                    await release.wait()
                return result

            monkeypatch.setattr(runner, "_save", pause)
            await runner.enqueue(job["id"])
            await asyncio.wait_for(waiting.wait(), 2)
            config["model"] = "vision"
            await runner.resume_waiting()
            await runner.resume_waiting()
            release.set()
            await asyncio.wait_for(runner.wait_idle(job["id"]), 2)
            saved = state.jobs.read(job["id"])
            try:
                assert saved["status"] == "completed"
                assert [module["attempts"] for module in saved["modules"]] == [
                    1,
                    1,
                    1,
                ]
            finally:
                await runner.close()

    asyncio.run(run())


def test_repeated_cancel_drains_disk_operation(tmp_path):

    entered = threading.Event()
    release = threading.Event()
    target = tmp_path / "output"

    def write():
        entered.set()
        release.wait(3)
        target.write_bytes(b"finished")

    async def run():
        # Inspect lifecycle ordering at the real operation boundary.
        # pylint: disable-next=protected-access
        operation = asyncio.create_task(job_runner._disk(write))
        await asyncio.to_thread(entered.wait, 2)
        try:
            operation.cancel()
            await asyncio.sleep(0.01)
            operation.cancel()
            await asyncio.sleep(0.01)
            assert not operation.done()
        finally:
            release.set()

            with contextlib.suppress(asyncio.CancelledError):
                await operation
        assert target.read_bytes() == b"finished"

    asyncio.run(run())


def test_missing_queued_job_does_not_stop_next_job(tmp_path, monkeypatch):
    state, payload, png = fixture(tmp_path, monkeypatch)

    async def generate(*_):
        return image.GeneratedImage(png, "image/png", "image")

    monkeypatch.setattr(job_runner.image, "generate_image", generate)

    async def run():

        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            await runner.start()
            job = create_job(payload, state.assets, state.jobs, state.profiles)
            await runner.enqueue("00000000-0000-0000-0000-000000000001")
            await runner.enqueue(job["id"])
            try:
                await asyncio.wait_for(runner.wait_idle(job["id"]), 2)
                assert state.jobs.read(job["id"])["status"] == "completed"
            finally:
                with contextlib.suppress(errors.AppError):
                    await runner.close()

    asyncio.run(run())


def test_cleanup_error_is_retried_on_next_interval(tmp_path, monkeypatch):
    state, _, _ = fixture(tmp_path, monkeypatch)

    async def run():

        retried = asyncio.Event()
        attempts = 0

        def cleanup():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary file lock")

        sleep = asyncio.sleep

        async def interval(delay):
            if delay == 3600:
                if attempts > 1:
                    retried.set()
                    await asyncio.Event().wait()
                await sleep(0)
            else:
                await sleep(delay)

        monkeypatch.setattr(job_runner.asyncio, "sleep", interval)
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            monkeypatch.setattr(runner, "_cleanup_existing", cleanup)
            await runner.start()
            try:
                await asyncio.wait_for(retried.wait(), 2)
                assert attempts == 2
            finally:
                with contextlib.suppress(OSError):
                    await runner.close()

    asyncio.run(run())


def test_close_finalizes_after_background_task_failure(tmp_path, monkeypatch):
    state, payload, _ = fixture(tmp_path, monkeypatch)

    async def fail():
        raise OSError("cleanup failure")

    async def run():
        async with httpx.AsyncClient() as client:
            runner = job_runner.JobRunner(
                state.jobs, state.assets, state.profiles, client
            )
            monkeypatch.setattr(runner, "_cleanup", fail)
            await runner.start()
            job = create_job(payload, state.assets, state.jobs, state.profiles)
            # Inspect lifecycle ordering at the real operation boundary.
            # pylint: disable-next=protected-access
            runner._idle[job["id"]] = asyncio.Event()
            await asyncio.sleep(0)
            await runner.close()
            assert state.jobs.read(job["id"])["status"] == "waiting_credentials"
            # Inspect lifecycle ordering at the real operation boundary.
            # pylint: disable-next=protected-access
            assert runner._idle[job["id"]].is_set()
            # Inspect lifecycle ordering at the real operation boundary.
            # pylint: disable-next=protected-access
            assert runner._worker is None

    asyncio.run(run())
