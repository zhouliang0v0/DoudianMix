"""Exercise legacy job persistence using synthetic local data only."""

from concurrent import futures
import errno
import json
import os
from pathlib import Path
import shutil
import threading

import pytest

from backend import job_store
from backend.errors import AppError
from backend.job_store import JobStore

_ID = "33333333-3333-4333-8333-333333333333"
_OTHER_ID = "44444444-4444-4444-8444-444444444444"


@pytest.fixture(name="store")
def job_store_fixture(tmp_path):
    """Copy synthetic legacy fixtures into an isolated writable directory."""
    source = Path(__file__).parent / "fixtures" / "legacy_data" / "jobs"
    shutil.copytree(source, tmp_path / "jobs")
    return JobStore(tmp_path)


def test_reads_legacy_job_and_waits_for_credentials(store):
    store.recover()
    job = store.read(_ID)
    assert job["status"] == "waiting_credentials"
    assert job["stage"] == "waiting_credentials"
    assert job["modules"][1]["status"] == "pending"
    assert job["modules"][0]["status"] == "completed"
    assert (store.job_dir(_ID) / "results" / "hero.png").read_bytes()
    assert job["createdAt"] == 1728000000000
    assert isinstance(job["updatedAt"], int)
    assert job["updatedAt"] > 1728000000000


def test_preserves_legacy_extra_fields(store):
    assert store.read(_ID)["legacyExtra"] == "keep"
    store.update(_ID, lambda job: job.update(stage="custom"))
    persisted = store.read(_ID)
    assert persisted["legacyExtra"] == "keep"
    assert persisted["modules"][0]["extra"] == {"retained": True}


def test_atomic_update_survives_interruption(store, monkeypatch):
    path = store.job_dir(_ID) / "job.json"
    original = path.read_bytes()

    def interrupt(source, target):
        del source, target
        raise OSError(errno.EIO, "synthetic interruption")

    monkeypatch.setattr(job_store.os, "replace", interrupt)
    with pytest.raises(OSError):
        store.update(_ID, lambda job: job.update(status="completed"))
    assert json.loads(path.read_text("utf-8"))["id"] == _ID
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "status", ["queued", "analyzing", "planning", "generating"]
)
def test_recovery_handles_all_active_statuses(store, status):
    store.update(_ID, lambda job: job.update(status=status))
    store.recover()
    assert store.read(_ID)["status"] == "waiting_credentials"


@pytest.mark.parametrize(
    "status",
    ["completed", "partial", "failed", "canceled", "waiting_credentials"],
)
def test_recovery_leaves_inactive_jobs_unchanged(store, status):
    store.update(_ID, lambda job: job.update(status=status))
    before = (store.job_dir(_ID) / "job.json").read_bytes()
    store.recover()
    assert (store.job_dir(_ID) / "job.json").read_bytes() == before


def test_create_and_list_newest_first_ignores_non_jobs(store):
    job = {
        "id": _OTHER_ID,
        "createdAt": 1728000000001,
        "modules": [],
        "status": "completed",
    }
    assert store.create(job) == job
    (store.job_dir(_ID).parent / "not-a-job").mkdir()
    assert [item["id"] for item in store.list_jobs()] == [_OTHER_ID, _ID]
    store.remove(_OTHER_ID)
    store.remove(_OTHER_ID)
    with pytest.raises(AppError) as caught:
        store.read(_OTHER_ID)
    assert caught.value.status_code == 404


def test_false_callback_skips_write_and_timestamp(store):
    path = store.job_dir(_ID) / "job.json"
    before = path.read_bytes()

    def skip(job):
        job["stage"] = "in-memory-only"
        return False

    assert store.update(_ID, skip)["stage"] == "in-memory-only"
    assert path.read_bytes() == before


def test_updates_are_serialized_and_other_jobs_can_progress(store):
    store.create({"id": _OTHER_ID, "createdAt": 1, "count": 0})
    started = threading.Event()
    release = threading.Event()

    def first(job):
        started.set()
        assert release.wait(5)
        job["count"] = 1

    def second(job):
        job["count"] += 1

    with futures.ThreadPoolExecutor(max_workers=3) as pool:
        pending = pool.submit(store.update, _ID, first)
        assert started.wait(5)
        next_update = pool.submit(store.update, _ID, second)
        independent = pool.submit(store.update, _OTHER_ID, second)
        assert independent.result(timeout=5)["count"] == 1
        release.set()
        pending.result(timeout=5)
        assert next_update.result(timeout=5)["count"] == 2
    assert store.read(_ID)["count"] == 2


def test_remove_waits_for_inflight_update(store):
    started = threading.Event()
    release = threading.Event()
    removing = threading.Event()

    def mutate(job):
        started.set()
        assert release.wait(5)
        job["stage"] = "finished"

    def remove():
        removing.set()
        store.remove(_ID)

    with futures.ThreadPoolExecutor(max_workers=2) as pool:
        update = pool.submit(store.update, _ID, mutate)
        assert started.wait(5)
        deletion = pool.submit(remove)
        assert removing.wait(5)
        assert not deletion.done()
        release.set()
        update.result(timeout=5)
        deletion.result(timeout=5)
    assert not store.job_dir(_ID).exists()


def test_failed_callback_does_not_block_later_updates(store):
    def fail(job):
        job["status"] = "completed"
        raise ValueError("synthetic callback failure")

    with pytest.raises(ValueError):
        store.update(_ID, fail)
    assert store.read(_ID)["status"] == "generating"
    store.update(_ID, lambda job: job.update(stage="later"))
    assert store.read(_ID)["stage"] == "later"


@pytest.mark.parametrize("error_code", [errno.EACCES, errno.EPERM, errno.EBUSY])
def test_atomic_replace_retries_file_contention(store, monkeypatch, error_code):
    replace = job_store.os.replace
    attempts = 0

    def busy(source, target):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise OSError(error_code, "synthetic file contention")
        replace(source, target)

    monkeypatch.setattr(job_store.os, "replace", busy)
    monkeypatch.setattr(job_store.time, "sleep", lambda _delay: None)
    store.update(_ID, lambda job: job.update(stage="saved"))
    assert store.read(_ID)["stage"] == "saved"


def test_atomic_replace_retry_is_bounded(store, monkeypatch):
    path = store.job_dir(_ID) / "job.json"
    before = path.read_bytes()
    attempts = 0

    def busy(source, target):
        nonlocal attempts
        del source, target
        attempts += 1
        if attempts > 13:
            pytest.fail("replacement retry exceeded the Node bound")
        raise PermissionError(errno.EACCES, "synthetic locked file")

    monkeypatch.setattr(job_store.os, "replace", busy)
    monkeypatch.setattr(job_store.time, "sleep", lambda _delay: None)
    with pytest.raises(PermissionError):
        store.update(_ID, lambda job: job.update(stage="unsaved"))
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("error_code", [errno.EACCES, errno.EPERM, errno.EBUSY])
def test_read_retries_transient_file_contention(store, monkeypatch, error_code):
    read_text = Path.read_text
    path = store.job_dir(_ID) / "job.json"
    before = path.read_bytes()
    attempts = 0

    def busy(self, *args, **kwargs):
        nonlocal attempts
        if self == path:
            attempts += 1
            if attempts == 1:
                raise OSError(error_code, "synthetic read contention")
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", busy)
    monkeypatch.setattr(job_store.time, "sleep", lambda _delay: None)
    assert store.read(_ID)["legacyExtra"] == "keep"
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("error_code", [errno.EACCES, errno.EIO])
def test_failed_read_is_bounded_and_leaves_files_unchanged(
    store, monkeypatch, error_code
):
    directory = store.job_dir(_ID)

    def snapshot():
        return {
            path: (
                path.stat().st_mtime_ns,
                None if path.is_dir() else path.read_bytes(),
            )
            for path in (directory, *directory.rglob("*"))
        }

    before = snapshot()
    attempts = 0

    def busy(self, *args, **kwargs):
        nonlocal attempts
        del self, args, kwargs
        attempts += 1
        if attempts > 13:
            pytest.fail("read contention retries exceeded the bounded window")
        raise OSError(error_code, "synthetic persistent read failure")

    monkeypatch.setattr(Path, "read_text", busy)
    monkeypatch.setattr(job_store.time, "sleep", lambda _delay: None)
    with pytest.raises(OSError) as caught:
        store.read(_ID)
    assert caught.value.errno == error_code
    assert attempts == (13 if error_code == errno.EACCES else 1)
    assert snapshot() == before


def test_missing_read_does_not_create_directories(tmp_path):
    store = JobStore(tmp_path / "absent")
    with pytest.raises(AppError) as caught:
        store.read(_ID)
    assert caught.value.status_code == 404
    assert not (tmp_path / "absent").exists()


def test_read_waits_for_same_job_update_and_other_job_can_be_read(store):
    store.create({"id": _OTHER_ID, "createdAt": 1, "stage": "independent"})
    started = threading.Event()
    release = threading.Event()
    reading = threading.Event()
    finished = threading.Event()

    def mutate(job):
        started.set()
        assert release.wait(5)
        job["stage"] = "updated"

    def read():
        reading.set()
        result = store.read(_ID)
        finished.set()
        return result

    with futures.ThreadPoolExecutor(max_workers=3) as pool:
        update = pool.submit(store.update, _ID, mutate)
        assert started.wait(5)
        pending = pool.submit(read)
        assert reading.wait(5)
        try:
            independent = pool.submit(store.read, _OTHER_ID)
            assert independent.result(timeout=5)["stage"] == "independent"
            assert not finished.wait(0.2), "read bypassed same-job update"
        finally:
            release.set()
        update.result(timeout=5)
        assert pending.result(timeout=5)["stage"] == "updated"


@pytest.mark.parametrize("identifier", ["../outside", "", "not-a-uuid", None])
def test_rejects_invalid_job_paths(store, identifier):
    with pytest.raises(AppError) as caught:
        store.job_dir(identifier)
    assert caught.value.status_code == 400


def test_list_propagates_corrupt_json(store):
    (store.job_dir(_ID) / "job.json").write_text("{", "utf-8")
    with pytest.raises(json.JSONDecodeError):
        store.list_jobs()


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive paths")
@pytest.mark.parametrize("operation", ["update", "remove"])
def test_case_variant_operations_wait_for_same_job(store, operation):
    identifier = "abcdefab-cdef-4abc-8def-abcdefabcdef"
    store.create({"id": identifier, "createdAt": 1, "count": 0})
    entered = threading.Event()
    release = threading.Event()
    attempted = threading.Event()
    finished = threading.Event()

    def first(job):
        entered.set()
        assert release.wait(5)
        job["count"] += 1

    def increment(job):
        job["count"] += 1

    def case_variant():
        attempted.set()
        if operation == "update":
            store.update(identifier.upper(), increment)
        else:
            store.remove(identifier.upper())
        finished.set()

    with futures.ThreadPoolExecutor(max_workers=2) as pool:
        original = pool.submit(store.update, identifier, first)
        assert entered.wait(5)
        variant = pool.submit(case_variant)
        assert attempted.wait(5)
        try:
            assert not finished.wait(0.2), "case variant bypassed active update"
        finally:
            release.set()
        original.result(timeout=5)
        variant.result(timeout=5)
    if operation == "update":
        assert store.read(identifier)["count"] == 2
        assert store.read(identifier)["id"] == identifier
        assert store.job_dir(identifier.upper()).name == identifier.upper()
    else:
        assert not store.job_dir(identifier).exists()
