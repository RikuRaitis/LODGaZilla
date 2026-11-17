"""Parallel batch import support for data-folder HDF5 cache rebuilds."""

from __future__ import annotations

import logging
import os
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.data.folder_info import SOURCE_IMPORT, is_info_file as is_folder_info_file, record_sources, source_entry
from src.jobs.cache_files import is_cache_output, is_cache_temp_file
from src.jobs.import_data import (
    DEFAULT_CONFIG_DIR,
    default_output_path,
    ensure_replaceable_cache,
    load_import_configuration,
    importer_plugin_identity,
    load_import_configurations,
    select_importer,
    write_hdf5_cache,
)
from src.jobs.parallel_jobs import run_process_jobs


from src.jobs.policy import MAX_IMPORT_PROCESSES, PROGRESS_TOTAL, configured_import_worker_count
PARSE_PROGRESS_LIMIT = 500
WRITE_PROGRESS_START = PARSE_PROGRESS_LIMIT

_WORKER_PROGRESS_QUEUE: Any = None
_WORKER_CANCEL_EVENT: Any = None


class ImportCancelled(RuntimeError):
    """Raised inside a worker when the batch has been cancelled."""


@dataclass(frozen=True)
class ImportJob:
    source_path: str
    folder_path: str
    config_path: str
    config_name: str
    # The GUI's current HDF5 settings; None reads the saved settings file.
    hdf5_settings: dict[str, Any] | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class ImportJobResult:
    source_path: str
    folder_path: str
    config_name: str
    output_path: str
    imported: bool
    error: str = ""
    traceback_text: str = ""
    input_megabytes: float = 0.0
    parse_seconds: float = 0.0
    write_seconds: float = 0.0
    total_seconds: float = 0.0
    config_path: str = ""


def run_reimport_batch(
    data_folder: Path,
    events,
    *,
    recursive: bool,
    logger: logging.Logger,
    process_limit: int = MAX_IMPORT_PROCESSES,
    enabled_plugins: dict[str, bool] | None = None,
    hdf5_settings: dict[str, Any] | None = None,
    cancel_event=None,
    sources: tuple[str, ...] = (),
    importer_id: str = "",
) -> None:  # type: ignore[no-untyped-def]
    """Rebuild HDF5 caches under one folder using a bounded parser process pool.

    ``sources`` limits the run to those files (a Source data row); ``importer_id``
    then names the importer plugin to read them with.
    """

    logger.info("Process batch started for %s (recursive=%s)", data_folder, recursive)
    remove_stale_cache_temp_files(data_folder, recursive, logger)
    jobs_by_folder = discover_import_jobs(
        data_folder, recursive, logger, enabled_plugins=enabled_plugins, hdf5_settings=hdf5_settings,
        sources=sources, importer_id=importer_id,
    )
    job_count = sum(len(jobs) for jobs in jobs_by_folder.values())
    if _cancelled(cancel_event):
        events.put(("finished", 0, 0, "H5 cache creation was cancelled."))
        return
    if job_count == 0:
        events.put(("finished", 0, 0, "No compatible source files found."))
        return

    worker_count = configured_import_worker_count(job_count, process_limit)
    duplicate_outputs = duplicate_output_paths(jobs_by_folder)
    if duplicate_outputs:
        formatted = ", ".join(str(path) for path in duplicate_outputs)
        events.put(("finished", 0, 0, f"Multiple source files map to the same HDF5 cache: {formatted}"))
        return

    logger.info("Importing %s source file(s) with %s parser process(es)", job_count, worker_count)
    events.put(("message", f"Importing {job_count} source file(s) with {worker_count} parser process(es)"))

    if worker_count <= 1:
        imported, failed, errors = run_import_jobs_serial(jobs_by_folder, events, logger, cancel_event)
    else:
        imported, failed, errors = run_import_jobs_parallel(
            jobs_by_folder, worker_count, events, logger, cancel_event,
        )

    if _cancelled(cancel_event):
        logger.info("Batch cancelled: %s imported, %s failed", imported, failed)
        events.put(("finished", imported, failed, "H5 cache creation was cancelled."))
        return
    logger.info("Batch completed: %s imported, %s failed", imported, failed)
    events.put(("finished", imported, failed, "\n".join(errors[:3])))


def _cancelled(cancel_event) -> bool:  # type: ignore[no-untyped-def]
    return cancel_event is not None and cancel_event.is_set()


def remove_stale_cache_temp_files(data_folder: Path, recursive: bool, logger: logging.Logger) -> int:
    """Delete cache temp files left behind by an interrupted earlier batch.

    Overlapping H5 batches never run at the same time, so any temp file in
    this scope belongs to a writer that no longer exists.  A file that is
    still open elsewhere cannot be removed on Windows and is skipped.
    """
    removed = 0
    try:
        candidates = data_folder.rglob("*.tmp") if recursive else data_folder.glob("*.tmp")
        for path in candidates:
            if not is_cache_temp_file(path) or not path.is_file():
                continue
            try:
                path.unlink()
            except OSError as error:
                logger.debug("Could not remove stale cache temp file %s: %s", path, error)
                continue
            removed += 1
            logger.info("Removed stale cache temp file %s", path)
    except OSError as error:
        logger.warning("Could not scan %s for stale cache temp files: %s", data_folder, error)
    return removed


def discover_import_jobs(
    data_folder: Path,
    recursive: bool,
    logger: logging.Logger,
    *,
    enabled_plugins: dict[str, bool] | None = None,
    hdf5_settings: dict[str, Any] | None = None,
    sources: tuple[str, ...] = (),
    importer_id: str = "",
) -> dict[Path, list[ImportJob]]:
    configurations = load_import_configurations(
        DEFAULT_CONFIG_DIR, enabled_plugins=enabled_plugins, hdf5_settings=hdf5_settings,
    )
    if importer_id:
        # A Source data row names its importer; another enabled one never takes its place.
        configurations = [configuration for configuration in configurations
                          if importer_plugin_identity(configuration.path)[0] == importer_id]
    logger.info("Loaded %s importer configuration(s)", len(configurations))

    jobs_by_folder: dict[Path, list[ImportJob]] = defaultdict(list)
    if sources:
        source_candidates = (data_folder / name for name in sources)
    else:
        source_candidates = data_folder.rglob("*") if recursive else data_folder.iterdir()
    for source_path in source_candidates:
        if (
            not source_path.is_file()
            or is_cache_output(source_path)
            or is_folder_info_file(source_path)
        ):
            continue
        try:
            configuration, _importer = select_importer(source_path, configurations)
        except (OSError, ValueError) as error:
            logger.debug("Skipped %s: %s", source_path, error)
            continue

        logger.debug("Accepted %s with %s", source_path, configuration.name)
        jobs_by_folder[source_path.parent].append(
            ImportJob(
                source_path=str(source_path),
                folder_path=str(source_path.parent),
                config_path=str(configuration.path),
                config_name=configuration.name,
                hdf5_settings=dict(hdf5_settings) if hdf5_settings is not None else None,
            )
        )
    return jobs_by_folder


def duplicate_output_paths(jobs_by_folder: dict[Path, list[ImportJob]]) -> list[Path]:
    output_paths = [
        default_output_path(Path(job.source_path)).resolve()
        for jobs in jobs_by_folder.values()
        for job in jobs
    ]
    counts: dict[Path, int] = defaultdict(int)
    for output_path in output_paths:
        counts[output_path] += 1
    return sorted(path for path, count in counts.items() if count > 1)

def run_import_jobs_serial(
    jobs_by_folder: dict[Path, list[ImportJob]],
    events,
    logger: logging.Logger,
    cancel_event=None,
) -> tuple[int, int, list[str]]:  # type: ignore[no-untyped-def]
    jobs = flatten_jobs(jobs_by_folder)
    results = ImportBatchResults()
    inline_progress = InlineProgressQueue(events.put)

    for job in jobs:
        if _cancelled(cancel_event):
            break
        result = run_import_job(job, inline_progress, cancel_event)
        if _cancelled(cancel_event) and not result.imported:
            break
        results.add(result, logger)

    return results.imported, results.failed, results.errors


def run_import_jobs_parallel(
    jobs_by_folder: dict[Path, list[ImportJob]],
    worker_count: int,
    events,
    logger: logging.Logger,
    cancel_event=None,
) -> tuple[int, int, list[str]]:  # type: ignore[no-untyped-def]
    jobs = flatten_jobs(jobs_by_folder)
    results = ImportBatchResults()

    for result in run_process_jobs(
        jobs,
        worker_count=worker_count,
        worker=run_import_job,
        initializer=initialize_import_worker,
        emit_progress=events.put,
        make_failure_result=make_import_failure_result,
        cancel_event=cancel_event,
    ):
        results.add(result, logger)

    return results.imported, results.failed, results.errors


def flatten_jobs(jobs_by_folder: dict[Path, list[ImportJob]]) -> list[ImportJob]:
    return [
        job
        for _folder, jobs in sorted(jobs_by_folder.items())
        for job in sorted(jobs, key=lambda item: item.source_path)
    ]


def initialize_import_worker(progress_queue, cancel_event=None) -> None:  # type: ignore[no-untyped-def]
    global _WORKER_PROGRESS_QUEUE, _WORKER_CANCEL_EVENT
    _WORKER_PROGRESS_QUEUE = progress_queue
    _WORKER_CANCEL_EVENT = cancel_event


def run_import_job(
    job: ImportJob,
    progress_queue=None,
    cancel_event=None,
) -> ImportJobResult:  # type: ignore[no-untyped-def]
    """Parse one source file and write one HDF5 cache file."""

    source_path = Path(job.source_path)
    config_path = Path(job.config_path)
    output_path = default_output_path(source_path)
    source_name = source_path.name
    worker_id = str(os.getpid())
    source_megabytes = input_megabytes(source_path)
    started = time.perf_counter()
    progress = progress_queue if progress_queue is not None else _WORKER_PROGRESS_QUEUE
    cancel = cancel_event if cancel_event is not None else _WORKER_CANCEL_EVENT

    def emit(event: tuple[Any, ...]) -> None:
        if progress is not None:
            progress.put(event)

    def check_cancelled() -> None:
        # Progress callbacks are the importer's only regular checkpoints.
        if cancel is not None and cancel.is_set():
            raise ImportCancelled(f"Cancelled import of {source_name}")

    try:
        check_cancelled()
        emit(("process_started", job.folder_path, worker_id, source_name, PROGRESS_TOTAL))
        # Fail before parsing; the writer checks again before replacing.
        ensure_replaceable_cache(output_path, source_path)
        configuration = load_import_configuration(config_path, hdf5_config=job.hdf5_settings)
        parser_instance = configuration.parser_instance
        if parser_instance is None:
            raise ValueError(f"{configuration.name}: missing parser instance")
        last_update = 0.0

        def report_parse_progress(fraction: float) -> None:
            nonlocal last_update
            check_cancelled()
            now = time.monotonic()
            if now - last_update >= 1.0:
                completed = int(bounded_fraction(fraction) * PARSE_PROGRESS_LIMIT)
                emit(("process_progress", job.folder_path, worker_id, source_name, completed, PROGRESS_TOTAL))
                last_update = now

        last_write_update = 0.0

        def report_write_progress(fraction: float) -> None:
            nonlocal last_write_update
            if fraction < 1.0:
                check_cancelled()
            completed = WRITE_PROGRESS_START + int(
                bounded_fraction(fraction) * (PROGRESS_TOTAL - WRITE_PROGRESS_START)
            )
            completed = min(PROGRESS_TOTAL, max(WRITE_PROGRESS_START, completed))
            now = time.monotonic()
            if completed >= PROGRESS_TOTAL or now - last_write_update >= 0.5:
                emit(("process_progress", job.folder_path, worker_id, source_name, completed, PROGRESS_TOTAL))
                last_write_update = now

        import_started = time.perf_counter()
        result = parser_instance.parse(source_path, progress_callback=report_parse_progress)
        parse_seconds = time.perf_counter() - import_started
        check_cancelled()

        emit(("process_progress", job.folder_path, worker_id, source_name, WRITE_PROGRESS_START, PROGRESS_TOTAL))
        write_started = time.perf_counter()
        write_hdf5_cache(
            output_path=output_path,
            input_path=source_path,
            selected_config=configuration,
            import_result=result,
            progress_callback=report_write_progress,
        )
        write_seconds = time.perf_counter() - write_started
        total_seconds = time.perf_counter() - started
        emit(("process_finished", job.folder_path, worker_id, source_name, True))
        return ImportJobResult(
            source_path=str(source_path),
            folder_path=job.folder_path,
            config_name=configuration.name,
            output_path=str(output_path),
            imported=True,
            input_megabytes=source_megabytes,
            parse_seconds=parse_seconds,
            write_seconds=write_seconds,
            total_seconds=total_seconds,
            config_path=job.config_path,
        )
    except Exception as error:  # noqa: BLE001 - return the failure so the batch can continue.
        emit(("process_finished", job.folder_path, worker_id, source_name, False))
        return ImportJobResult(
            source_path=str(source_path),
            folder_path=job.folder_path,
            config_name=job.config_name,
            output_path=str(output_path),
            imported=False,
            error=str(error),
            traceback_text=traceback.format_exc(),
            input_megabytes=source_megabytes,
            total_seconds=time.perf_counter() - started,
        )

def make_import_failure_result(job: ImportJob, error: Exception) -> ImportJobResult:
    """Convert an unexpected worker crash into a normal failed import result."""
    return ImportJobResult(
        source_path=job.source_path,
        folder_path=job.folder_path,
        config_name=job.config_name,
        output_path=str(default_output_path(Path(job.source_path))),
        imported=False,
        error=str(error),
        traceback_text=traceback.format_exc(),
    )


def input_megabytes(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def bounded_fraction(fraction: float) -> float:
    return max(0.0, min(1.0, fraction))


class InlineProgressQueue:
    def __init__(self, emit) -> None:  # type: ignore[no-untyped-def]
        self._emit = emit

    def put(self, event: tuple[Any, ...]) -> None:
        self._emit(event)


def record_import_source(result: ImportJobResult, logger: logging.Logger) -> None:
    """Note an imported file in its folder's Source data (``_info.json``)."""
    source, output = Path(result.source_path), Path(result.output_path)
    plugin_id, title = importer_plugin_identity(result.config_path) if result.config_path else ("", result.config_name)
    try:
        record_sources(Path(result.folder_path), [source_entry(
            SOURCE_IMPORT, source=source.name, output=output.name, plugin=title, plugin_id=plugin_id)])
    except (OSError, ValueError, TimeoutError) as error:
        # The H5 file is written; only its Source data record is missing.
        logger.warning("Could not record the source of %s: %s", output, error)


class ImportBatchResults:
    def __init__(self) -> None:
        self.imported = 0
        self.failed = 0
        self.errors: list[str] = []

    def add(self, result: ImportJobResult, logger: logging.Logger) -> None:
        source_path = Path(result.source_path)
        if result.imported:
            self.imported += 1
            logger.info(
                "Timing %s: input=%.1f MiB parse=%.2fs hdf5_lod_write=%.2fs total=%.2fs throughput=%.2f MiB/s",
                source_path.name,
                result.input_megabytes,
                result.parse_seconds,
                result.write_seconds,
                result.total_seconds,
                result.input_megabytes / result.total_seconds if result.total_seconds else 0.0,
            )
            logger.info("Created %s", result.output_path)
            record_import_source(result, logger)
            return

        self.failed += 1
        self.errors.append(f"{source_path.name}: {result.error}")
        logger.error("Failed to import %s\n%s", source_path, result.traceback_text)
