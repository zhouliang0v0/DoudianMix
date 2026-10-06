"""Persist legacy job dictionaries with per-job atomic updates."""

from collections.abc import Callable
import errno
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from backend.errors import AppError

_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
_ACTIVE = frozenset(("queued", "analyzing", "planning", "generating"))
_RETRY_ERRORS = frozenset((errno.EPERM, errno.EACCES, errno.EBUSY))


class JobStore:
    """Read and update Node-compatible jobs in a single service process."""

    def __init__(self, data_dir: Path) -> None:
        """Initialize storage under the supplied local data directory.

        Args:
            data_dir: Root containing the legacy jobs directory.
        """
        self._jobs_dir = Path(data_dir) / "jobs"
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def job_dir(self, job_id: str) -> Path:
        """Return the directory for a validated UUID.

        Args:
            job_id: Legacy UUID identifying the job.

        Returns:
            The job's directory, whether or not it exists.

        Raises:
            AppError: The ID is invalid (400).
        """
        if not isinstance(job_id, str) or not _UUID.fullmatch(job_id):
            raise AppError("任务 ID 无效")
        return self._jobs_dir / job_id

    def _lock(self, job_id: str) -> threading.RLock:
        self.job_dir(job_id)
        lock_id = job_id.lower()
        with self._locks_guard:
            # Keep lock identities stable for callers already waiting on them.
            # UUID case variants refer to the same directory on Windows.
            return self._locks.setdefault(lock_id, threading.RLock())

    def read(self, job_id: str) -> dict:
        """Read legacy JSON under the job lock, retrying brief file contention.

        Args:
            job_id: UUID of the job to read.

        Returns:
            The persisted dictionary, including unknown fields.

        Raises:
            AppError: The ID is invalid (400) or metadata is missing (404).
            json.JSONDecodeError: Metadata contains invalid JSON.
            OSError: Existing metadata cannot be read.
        """
        path = self.job_dir(job_id) / "job.json"
        with self._lock(job_id):
            for attempt in range(13):
                try:
                    return json.loads(path.read_text("utf-8"))
                except FileNotFoundError as error:
                    raise AppError("任务不存在", 404) from error
                except OSError as error:
                    if error.errno not in _RETRY_ERRORS or attempt == 12:
                        raise
                    time.sleep((20 + attempt * 10) / 1000)

    def _write(self, job: dict) -> dict:
        directory = self.job_dir(job["id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f"{uuid.uuid4()}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(job, output, ensure_ascii=False, indent=2)
            for attempt in range(13):
                try:
                    os.replace(temporary, directory / "job.json")
                    break
                except OSError as error:
                    if error.errno not in _RETRY_ERRORS or attempt == 12:
                        raise
                    time.sleep((20 + attempt * 10) / 1000)
        finally:
            temporary.unlink(missing_ok=True)
        return job

    def create(self, job: dict) -> dict:
        """Persist a caller-supplied legacy job dictionary.

        Args:
            job: JSON-compatible dictionary with a UUID id field.

        Returns:
            The supplied dictionary after successful atomic persistence.

        Raises:
            AppError: The job ID is invalid (400).
            OSError: Metadata cannot be written or replaced.
        """
        with self._lock(job["id"]):
            return self._write(job)

    def update(
        self, job_id: str, mutate: Callable[[dict], None | bool]
    ) -> dict:
        """Serialize a mutation and atomically save it unless it returns False.

        Args:
            job_id: UUID of the job to update.
            mutate: Callback editing the loaded dictionary in place. False
                skips persistence and leaves the stored timestamp unchanged.

        Returns:
            The mutated dictionary, with updatedAt in milliseconds when saved.

        Raises:
            AppError: The ID is invalid (400) or the job is missing (404).
            OSError: Metadata cannot be read, written, or replaced.
            Exception: Callback failures propagate without writing metadata.
        """
        with self._lock(job_id):
            job = self.read(job_id)
            if mutate(job) is not False:
                job["updatedAt"] = time.time_ns() // 1_000_000
                self._write(job)
            return job

    def list_jobs(self) -> list[dict]:
        """List persisted UUID jobs newest first by legacy createdAt.

        Returns:
            Job dictionaries sorted by descending creation milliseconds.

        Raises:
            json.JSONDecodeError: Existing metadata is corrupt.
            OSError: Jobs cannot be listed or read.
        """
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        jobs = []
        for directory in self._jobs_dir.iterdir():
            if not _UUID.fullmatch(directory.name):
                continue
            try:
                jobs.append(self.read(directory.name))
            except AppError as error:
                if error.status_code != 404:
                    raise
        return sorted(jobs, key=lambda job: job["createdAt"], reverse=True)

    def recover(self) -> None:
        """Pause active jobs for credentials while retaining completed assets.

        Raises:
            AppError: A job cannot be updated.
            OSError: Jobs cannot be read or saved.
        """
        for job in self.list_jobs():
            if job["status"] in _ACTIVE:
                self.update(job["id"], self._recover_job)

    @staticmethod
    def _recover_job(job: dict) -> None:
        job["status"] = "waiting_credentials"
        job["stage"] = "waiting_credentials"
        for module in job["modules"]:
            if module["status"] == "running":
                module["status"] = "pending"

    def remove(self, job_id: str) -> None:
        """Wait for updates on this job and delete its directory if present.

        Args:
            job_id: UUID of the job to remove.

        Raises:
            AppError: The job ID is invalid (400).
            OSError: An existing job directory cannot be removed.
        """
        with self._lock(job_id):
            try:
                shutil.rmtree(self.job_dir(job_id))
            except FileNotFoundError:
                pass
