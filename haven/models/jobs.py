"""Background download jobs: fire-and-forget installs with observable state.

The Download entry path stays explicit -- `ModelManager.install_from_url`
is unchanged -- but the web surface needs installs that outlive a single
HTTP request. `DownloadJobManager` wraps the same manager core: `start()`
resolves the URL synchronously (a resolution failure becomes a failed job,
never a raised exception), then downloads, verifies, and registers on a
daemon thread, transitioning the job queued -> downloading -> verifying ->
ready exactly as the synchronous path transitions the model record
REGISTERED -> VERIFIED -> READY. Jobs are capped at two concurrent
downloads; the rest wait as queued. Cancellation is cooperative: the cancel
flag reaches the downloader's between-chunks check, and cancellation is
never a load failure -- the job ends cancelled and the record stays
explicit. The job map lives behind a lock, callbacks fire outside it, and
callbacks receive an immutable snapshot on every state transition, which is
what the SSE stream in `haven.web.server` forwards to the UI.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from haven.core.time import require_aware_utc

from .downloader import DownloadCancelledError, ModelSourceError, resolve_model_url
from .manager import ModelManager

_STAGING_DIRNAME = ".staging"
_MAX_CONCURRENT_DOWNLOADS = 2


class JobState(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATES = (JobState.READY, JobState.FAILED, JobState.CANCELLED)


@dataclass(frozen=True)
class DownloadJob:
    """An immutable snapshot of one background download job."""

    job_id: str
    url: str
    state: JobState
    received_bytes: int
    total_bytes: int | None
    current_file: str | None
    error: str | None
    manifest_id: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        object.__setattr__(self, "updated_at", require_aware_utc(self.updated_at, name="updated_at"))


def job_to_dict(job: DownloadJob) -> dict[str, Any]:
    """The wire shape for API envelopes and SSE payloads."""

    return {
        "job_id": job.job_id,
        "url": job.url,
        "state": job.state.value,
        "received_bytes": job.received_bytes,
        "total_bytes": job.total_bytes,
        "current_file": job.current_file,
        "error": job.error,
        "manifest_id": job.manifest_id,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


class DownloadJobManager:
    """Thread-safe job facade over one ModelManager; callbacks feed SSE."""

    def __init__(self, manager: ModelManager, *, max_concurrent: int = _MAX_CONCURRENT_DOWNLOADS) -> None:
        self._manager = manager
        self._slots = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._jobs: dict[str, DownloadJob] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._callbacks: list[Callable[[DownloadJob], None]] = []
        # job_id -> (current file, last received count, last total) plus the
        # rolled-up totals of finished files: byte progress is reported per
        # file, so a finished file's final count rolls into the job's overall
        # received_bytes/total_bytes when the next file starts.
        self._file_progress: dict[str, tuple[str, int, int | None]] = {}
        self._completed_bytes: dict[str, int] = {}
        self._completed_totals: dict[str, int | None] = {}

    def start(self, url: str) -> str:
        """Resolve synchronously, then download+verify+register on a thread.

        Returns the job id in every case; a resolution failure leaves the
        job in FAILED with the error message, so the caller can choose
        between the error envelope and the jobs list.
        """

        now = datetime.now(timezone.utc)
        job = DownloadJob(
            job_id=uuid.uuid4().hex,
            url=url,
            state=JobState.QUEUED,
            received_bytes=0,
            total_bytes=None,
            current_file=None,
            error=None,
            manifest_id=None,
            created_at=now,
            updated_at=now,
        )
        flag = threading.Event()
        with self._lock:
            self._jobs[job.job_id] = job
            self._cancel_flags[job.job_id] = flag
        self._notify(job)
        try:
            resolved = resolve_model_url(url)
        except ModelSourceError as exc:
            self._transition(job.job_id, state=JobState.FAILED, error=str(exc))
            return job.job_id
        self._transition(job.job_id, manifest_id=resolved.manifest.id)
        thread = threading.Thread(
            target=self._run,
            args=(job.job_id, resolved, url, flag),
            daemon=True,
            name=f"haven-download-{job.job_id[:8]}",
        )
        thread.start()
        return job.job_id

    def status(self, job_id: str) -> DownloadJob:
        """The current snapshot; raises KeyError for an unknown job."""

        with self._lock:
            return self._jobs[job_id]

    def list(self) -> tuple[DownloadJob, ...]:
        with self._lock:
            jobs = tuple(self._jobs.values())
        return tuple(sorted(jobs, key=lambda job: (job.created_at, job.job_id)))

    def cancel(self, job_id: str) -> bool:
        """Cooperatively cancel; False for unknown or already-terminal jobs."""

        with self._lock:
            job = self._jobs.get(job_id)
            flag = self._cancel_flags.get(job_id)
            if job is None or flag is None or job.state in _TERMINAL_STATES:
                return False
            flag.set()
        if job.state is JobState.QUEUED:
            self._transition(job_id, state=JobState.CANCELLED)
        return True

    def subscribe(self, callback: Callable[[DownloadJob], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[DownloadJob], None]) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    # -- internals ---------------------------------------------------------------

    def _run(self, job_id: str, resolved, url: str, flag: threading.Event) -> None:
        staging = self._manager.models_root / _STAGING_DIRNAME / resolved.manifest.id
        with self._slots:
            if flag.is_set():
                self._finish(job_id, JobState.CANCELLED)
                return
            self._transition(job_id, state=JobState.DOWNLOADING)
            try:
                self._manager._install_resolved(
                    resolved,
                    url,
                    progress=lambda received, total, name: self._progress(job_id, received, total, name),
                    cancel=flag.is_set,
                    staging_dir=staging,
                    on_downloaded=lambda: self._finish(job_id, JobState.VERIFYING),
                )
            except DownloadCancelledError:
                self._finish(job_id, JobState.CANCELLED)
            except Exception as exc:
                # Hash mismatch, storage failure, network error -- any
                # exception from the shared install core ends the job as
                # FAILED; a job thread never dies unobserved.
                self._finish(job_id, JobState.FAILED, error=str(exc))
            else:
                self._finish(job_id, JobState.READY)
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    def _progress(self, job_id: str, received: int, total: int | None, name: str) -> None:
        # Byte progress is bookkeeping, not a state transition: update the
        # snapshot silently so status() stays fresh without flooding the
        # SSE stream with one event per chunk.
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state in _TERMINAL_STATES:
                return
            completed = self._completed_bytes.get(job_id, 0)
            completed_total = self._completed_totals.get(job_id, 0)
            previous = self._file_progress.get(job_id)
            if previous is not None and previous[0] != name:
                # the finished file rolls its final counts into the job totals
                completed += previous[1]
                self._completed_bytes[job_id] = completed
                if completed_total is not None:
                    completed_total = None if previous[2] is None else completed_total + previous[2]
                    self._completed_totals[job_id] = completed_total
            self._file_progress[job_id] = (name, received, total)
            if job_id not in self._completed_totals:
                overall_total: int | None = total
            elif completed_total is None or total is None:
                overall_total = None  # one file's size is unknown
            else:
                overall_total = completed_total + total
            self._jobs[job_id] = replace(
                job,
                received_bytes=completed + received,
                total_bytes=overall_total,
                current_file=name,
                updated_at=datetime.now(timezone.utc),
            )

    def _finish(self, job_id: str, state: JobState, *, error: str | None = None) -> None:
        with self._lock:
            self._file_progress.pop(job_id, None)
            self._completed_bytes.pop(job_id, None)
            self._completed_totals.pop(job_id, None)
            current = self._jobs.get(job_id)
            if current is None or current.state in _TERMINAL_STATES:
                return
        self._transition(job_id, state=state, error=error)

    def _transition(self, job_id: str, **changes) -> DownloadJob:
        with self._lock:
            job = self._jobs[job_id]
            updated = replace(job, updated_at=datetime.now(timezone.utc), **changes)
            self._jobs[job_id] = updated
            callbacks = tuple(self._callbacks)
        self._notify(updated, callbacks)
        return updated

    def _notify(
        self,
        job: DownloadJob,
        callbacks: tuple[Callable[[DownloadJob], None], ...] | None = None,
    ) -> None:
        if callbacks is None:
            with self._lock:
                callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback(job)
            except Exception:
                pass  # a misbehaving subscriber must never break the worker


__all__ = [
    "DownloadJob",
    "DownloadJobManager",
    "JobState",
    "job_to_dict",
]
