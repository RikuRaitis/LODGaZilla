"""Shared process-pool orchestration for independent batch job types."""

from __future__ import annotations

import multiprocessing
import queue
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any, Callable, Iterable, TypeVar


Job = TypeVar("Job")
Result = TypeVar("Result")


def drain_progress_queue(progress_queue, emit: Callable[[Any], None]) -> None:  # type: ignore[no-untyped-def]
    """Forward all currently available worker progress events to the caller."""
    while True:
        try:
            emit(progress_queue.get_nowait())
        except queue.Empty:
            return


def run_process_jobs(
    jobs: Iterable[Job],
    *,
    worker_count: int,
    worker: Callable[..., Result],
    initializer: Callable[..., None],
    emit_progress: Callable[[Any], None],
    make_failure_result: Callable[[Job, Exception], Result],
    worker_args: tuple[Any, ...] = (),
    poll_seconds: float = 0.2,
    cancel_event=None,
    cancel_grace_seconds: float = 3.0,
) -> list[Result]:  # type: ignore[no-untyped-def]
    """Run pickle-safe top-level workers while relaying their queue events.

    ``worker`` and ``initializer`` must be importable top-level functions: the
    application deliberately uses the Windows-safe ``spawn`` start method.
    Domain modules keep ownership of their job/result types and exception
    policy through ``make_failure_result``.

    ``initializer`` receives ``(progress_queue, cancel_event)``.  When the
    optional multiprocessing ``cancel_event`` is set, queued jobs are
    cancelled, running workers get ``cancel_grace_seconds`` to notice the
    event, and any worker still alive after that is terminated.  Results of
    cancelled jobs are omitted.
    """
    context = multiprocessing.get_context("spawn")
    progress_queue = context.Queue()
    results: list[Result] = []
    executor = ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=context,
        initializer=initializer,
        initargs=(progress_queue, cancel_event),
    )
    try:
        futures = {
            executor.submit(worker, job, *worker_args): job
            for job in jobs
        }
        pending = set(futures)
        while pending:
            if cancel_event is not None and cancel_event.is_set():
                cancel_executor(executor, pending, cancel_grace_seconds)
                break
            drain_progress_queue(progress_queue, emit_progress)
            done, pending = wait(pending, timeout=poll_seconds, return_when=FIRST_COMPLETED)
            for future in done:
                job = futures[future]
                try:
                    results.append(future.result())
                except Exception as error:  # noqa: BLE001 - isolate failed jobs.
                    results.append(make_failure_result(job, error))
        drain_progress_queue(progress_queue, emit_progress)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return results


def cancel_executor(executor: ProcessPoolExecutor, pending, grace_seconds: float) -> None:  # type: ignore[no-untyped-def]
    """Stop a pool: drop queued jobs, wait briefly, then terminate stragglers."""
    # ``shutdown`` clears the executor's process table, so capture it first.
    processes = list((getattr(executor, "_processes", None) or {}).values())
    executor.shutdown(wait=False, cancel_futures=True)
    wait(pending, timeout=max(0.0, grace_seconds))
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(1)
