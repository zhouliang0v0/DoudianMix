"""HTTP lifecycle tests using real persistence and the serial runner."""

import asyncio
import contextlib
import io
import threading

import httpx
from PIL import Image
import pytest

from backend import errors
from backend import job_runner
from backend import main
from backend.api import jobs
from backend.providers import image


@contextlib.asynccontextmanager
async def _session(tmp_path, monkeypatch):
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
    draft_id = state.assets.create_draft()["draftId"]
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    png = buffer.getvalue()
    state.assets.add_image(draft_id, png, "tea.png")
    payload = dict(
        draftId=draft_id,
        brief="Tea",
        modules=[
            "首屏主视觉",
            "商品细节图",
            "品牌故事图",
        ],
    )

    async def analyze(*_):
        return dict(
            facts=["tea"], unknowns=[], text="tea", response_model="vision"
        )

    async def plan(*_):
        return '{"headline":"Tea","body":"Tea"}'

    monkeypatch.setattr(job_runner.vision, "analyze_vision", analyze)
    monkeypatch.setattr(job_runner.text, "call_text", plan)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:8000",
        ) as client:
            yield state, client, payload, png


def test_retry_only_target_failed_module(tmp_path, monkeypatch):
    async def run():
        async with _session(tmp_path, monkeypatch) as (
            state,
            client,
            payload,
            png,
        ):
            calls = {"1": 0, "2": 0, "3": 0}

            async def generate(*args):
                module_id = (
                    "1"
                    if "首屏主视觉" in args[2]
                    else "2"
                    if "商品细节图" in args[2]
                    else "3"
                )
                calls[module_id] += 1
                if module_id != "1" and calls[module_id] == 1:
                    raise errors.AppError("failed", 502, "provider")
                return image.GeneratedImage(png, "image/png", "image")

            monkeypatch.setattr(job_runner.image, "generate_image", generate)
            response = await client.post("/api/jobs", json=payload)
            job_id = response.json()["jobId"]
            await state.runner.wait_idle(job_id)
            before = state.jobs.read(job_id)
            response = await client.post(
                f"/api/jobs/{job_id}/retry", json={"moduleId": "2"}
            )
            assert response.status_code == 200
            assert response.json() == {"status": "queued"}
            await state.runner.wait_idle(job_id)
            after = state.jobs.read(job_id)
            assert calls["1"] == 1
            assert calls["3"] == 1
            assert calls["2"] == 2
            assert after["modules"][0] == before["modules"][0]
            assert after["modules"][2] == before["modules"][2]
            assert after["analysis"] == before["analysis"]
            assert after["status"] == "partial"

    asyncio.run(run())


def test_cancel_stops_next_module_and_preserves_result(tmp_path, monkeypatch):
    async def run():
        async with _session(tmp_path, monkeypatch) as (
            state,
            client,
            payload,
            png,
        ):
            entered = asyncio.Event()
            calls = 0

            async def generate(*_):
                nonlocal calls
                calls += 1
                if calls == 2:
                    entered.set()
                    await asyncio.Event().wait()
                return image.GeneratedImage(png, "image/png", "image")

            monkeypatch.setattr(job_runner.image, "generate_image", generate)
            job_id = (await client.post("/api/jobs", json=payload)).json()[
                "jobId"
            ]
            await asyncio.wait_for(entered.wait(), 2)
            response = await client.post(f"/api/jobs/{job_id}/cancel")
            assert response.status_code == 200
            assert response.json() == {"status": "canceled"}
            await state.runner.wait_idle(job_id)
            saved = state.jobs.read(job_id)
            assert calls == 2
            assert saved["modules"][2]["attempts"] == 0
            assert saved["modules"][0]["status"] == "completed"
            assert (
                state.jobs.job_dir(job_id) / "results" / "1.png"
            ).read_bytes() == png
            assert (await client.post(f"/api/jobs/{job_id}/cancel")).json() == {
                "status": "canceled"
            }
            retry = await client.post(
                f"/api/jobs/{job_id}/retry", json={"moduleId": "2"}
            )
            assert retry.status_code == 400
            assert retry.json() == {"error": "已取消任务不能重试"}

    asyncio.run(run())


@pytest.mark.parametrize("uppercase", [False, True])
def test_delete_waits_and_removes_linked_assets(
    tmp_path, monkeypatch, uppercase
):
    async def run():
        async with _session(tmp_path, monkeypatch) as (
            state,
            client,
            payload,
            png,
        ):
            entered = threading.Event()
            release = threading.Event()
            # Exercise an actual disk operation that cannot be canceled.
            # pylint: disable-next=protected-access
            original = state.runner._write_image

            def delayed(*args):
                entered.set()
                release.wait(3)
                return original(*args)

            async def generate(*_):
                return image.GeneratedImage(png, "image/png", "image")

            monkeypatch.setattr(state.runner, "_write_image", delayed)
            monkeypatch.setattr(job_runner.image, "generate_image", generate)
            other = jobs.create_job(
                payload, state.assets, state.jobs, state.profiles
            )
            job_id = (await client.post("/api/jobs", json=payload)).json()[
                "jobId"
            ]
            assert await asyncio.to_thread(entered.wait, 2)
            request_id = job_id.upper() if uppercase else job_id
            deletion = asyncio.create_task(
                client.delete(f"/api/jobs/{request_id}")
            )
            try:
                await asyncio.sleep(0.05)
                assert not deletion.done()
            finally:
                release.set()
            response = await deletion
            assert response.status_code == 200
            assert response.json() == {"ok": True}
            assert (await client.get(f"/api/jobs/{job_id}")).status_code == 404
            assert not state.jobs.job_dir(job_id).exists()
            assert not (tmp_path / "drafts" / payload["draftId"]).exists()
            assert state.jobs.read(other["id"])["id"] == other["id"]
            assert (state.jobs.job_dir(other["id"]) / "originals").is_dir()

    asyncio.run(run())


def test_uppercase_cancel_aborts_planning_before_image_request(
    tmp_path, monkeypatch
):
    async def run():
        async with _session(tmp_path, monkeypatch) as (
            state,
            client,
            payload,
            png,
        ):
            planning = asyncio.Event()
            canceled = asyncio.Event()
            release = asyncio.Event()
            image_calls = 0

            async def plan(*_):
                planning.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    canceled.set()
                    raise
                return '{"headline":"Tea","body":"Tea"}'

            async def generate(*_):
                nonlocal image_calls
                image_calls += 1
                return image.GeneratedImage(png, "image/png", "image")

            monkeypatch.setattr(job_runner.text, "call_text", plan)
            monkeypatch.setattr(job_runner.image, "generate_image", generate)
            job_id = (await client.post("/api/jobs", json=payload)).json()[
                "jobId"
            ]
            await asyncio.wait_for(planning.wait(), 2)
            try:
                response = await client.post(
                    f"/api/jobs/{job_id.upper()}/cancel"
                )
                assert response.status_code == 200
                assert response.json() == {"status": "canceled"}
                assert canceled.is_set()
            finally:
                release.set()
                await state.runner.wait_idle(job_id)
            assert image_calls == 0
            assert state.jobs.read(job_id)["status"] == "canceled"

    asyncio.run(run())


def test_uppercase_enqueue_shares_idle_and_cancel_identity(
    tmp_path, monkeypatch
):
    async def run():
        async with _session(tmp_path, monkeypatch) as (state, _, payload, png):
            entered = asyncio.Event()

            async def generate(*_):
                entered.set()
                await asyncio.Event().wait()
                return image.GeneratedImage(png, "image/png", "image")

            monkeypatch.setattr(job_runner.image, "generate_image", generate)
            job = jobs.create_job(
                payload, state.assets, state.jobs, state.profiles
            )
            await state.runner.enqueue(job["id"].upper())
            await asyncio.wait_for(entered.wait(), 2)
            idle = asyncio.create_task(state.runner.wait_idle(job["id"]))
            try:
                await asyncio.sleep(0.01)
                assert not idle.done()
            finally:
                await state.runner.cancel(job["id"].upper())
                await idle
            assert state.jobs.read(job["id"])["status"] == "canceled"

    asyncio.run(run())


def test_read_contract_and_terminal_cancel(tmp_path, monkeypatch):
    async def run():
        async with _session(tmp_path, monkeypatch) as (
            state,
            client,
            payload,
            _,
        ):
            assert (await client.get("/api/jobs")).json() == {"jobs": []}
            job = jobs.create_job(
                payload, state.assets, state.jobs, state.profiles
            )
            state.jobs.update(
                job["id"],
                lambda current: current.update(status="failed", stage="failed"),
            )
            saved = state.jobs.read(job["id"])
            response = await client.get(f"/api/jobs/{job['id']}")
            assert response.status_code == 200
            assert response.json() == saved
            assert response.headers["cache-control"] == "no-store"
            assert (await client.get("/api/jobs")).json() == {
                "jobs": [
                    dict(
                        id=job["id"],
                        status="failed",
                        createdAt=job["createdAt"],
                        modules=3,
                        completed=0,
                    )
                ]
            }
            assert (
                await client.post(f"/api/jobs/{job['id']}/cancel")
            ).json() == {"status": "failed"}
            assert state.jobs.read(job["id"])["updatedAt"] == saved["updatedAt"]
            for body in ({}, {"moduleId": "1"}, {"moduleId": "9"}):
                response = await client.post(
                    f"/api/jobs/{job['id']}/retry", json=body
                )
                assert response.status_code == 400
                assert response.json() == {"error": "只能重试失败模块"}
            missing = "00000000-0000-0000-0000-000000000001"
            for method, suffix in (
                ("GET", ""),
                ("DELETE", ""),
                ("POST", "/cancel"),
                ("POST", "/retry"),
            ):
                response = await client.request(
                    method, f"/api/jobs/{missing}{suffix}", json={}
                )
                assert response.status_code == 404
                assert response.json() == {"error": "任务不存在"}

    asyncio.run(run())


def test_duplicate_retry_and_cancel_are_serialized(tmp_path, monkeypatch):
    async def run():
        async with _session(tmp_path, monkeypatch) as (
            state,
            client,
            payload,
            png,
        ):
            entered = asyncio.Event()
            calls = 0

            async def generate(*_):
                nonlocal calls
                calls += 1
                entered.set()
                await asyncio.Event().wait()
                return image.GeneratedImage(png, "image/png", "image")

            monkeypatch.setattr(job_runner.image, "generate_image", generate)
            job = jobs.create_job(
                payload, state.assets, state.jobs, state.profiles
            )

            def fail(current):
                current.update(status="failed", stage="failed")
                for module in current["modules"]:
                    module.update(status="failed", attempts=1)

            state.jobs.update(job["id"], fail)
            url = f"/api/jobs/{job['id']}"
            results = await asyncio.gather(
                *(
                    client.post(url + "/retry", json={"moduleId": "1"})
                    for _ in range(2)
                )
            )
            assert sorted(response.status_code for response in results) == [
                200,
                400,
            ]
            await asyncio.wait_for(entered.wait(), 2)
            results = await asyncio.gather(
                *(client.post(url + "/cancel") for _ in range(2))
            )
            assert all(
                response.json() == {"status": "canceled"}
                for response in results
            )
            await state.runner.wait_idle(job["id"])
            saved = state.jobs.read(job["id"])
            assert calls == 1
            assert [module["attempts"] for module in saved["modules"]] == [
                2,
                1,
                1,
            ]

    asyncio.run(run())
