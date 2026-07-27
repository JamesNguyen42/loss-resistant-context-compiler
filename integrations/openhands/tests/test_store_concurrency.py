from __future__ import annotations

import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty
from typing import Any

from store_helpers import message_event, source_record, store_path

from ctxc_openhands.storage import SQLiteGenerationStore


def _thread_append(
    path: Path,
    *,
    request_id: str,
    barrier: threading.Barrier,
) -> dict[str, Any]:
    event = message_event(0)
    record = source_record(event, 0, session_id="thread-session")
    store = SQLiteGenerationStore(path, busy_timeout_seconds=30)
    barrier.wait(timeout=30)
    return store.append_source_event(
        session_id="thread-session",
        host_event=event,
        source_record=record,
        request_id=request_id,
    ).to_dict()


def _process_append(
    path: str,
    request_id: str,
    barrier: Any,
    results: Any,
) -> None:
    try:
        event = message_event(0)
        record = source_record(event, 0, session_id="process-session")
        store = SQLiteGenerationStore(path, busy_timeout_seconds=30)
        barrier.wait(timeout=30)
        result = store.append_source_event(
            session_id="process-session",
            host_event=event,
            source_record=record,
            request_id=request_id,
        )
        results.put(("ok", result.to_dict()))
    except BaseException as exc:
        results.put(("error", f"{type(exc).__name__}: {exc}"))


def test_thread_contenders_append_one_exact_event_without_duplication(tmp_path) -> None:
    path = store_path(tmp_path)
    SQLiteGenerationStore(path)
    worker_count = 12
    barrier = threading.Barrier(worker_count)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _thread_append,
                path,
                request_id=f"thread-request-{index}",
                barrier=barrier,
            )
            for index in range(worker_count)
        ]
        results = [future.result(timeout=45) for future in futures]

    assert sum(result["accepted"] for result in results) == 1
    reopened = SQLiteGenerationStore(path)
    snapshot = reopened.snapshot("thread-session")
    assert snapshot.source_count == 1
    assert [record.id for record in snapshot.records] == ["event-0"]
    assert reopened.integrity_report()["passed"] is True


def test_spawned_process_contenders_append_one_exact_event_without_duplication(
    tmp_path,
) -> None:
    path = store_path(tmp_path)
    SQLiteGenerationStore(path)
    context = multiprocessing.get_context("spawn")
    worker_count = 4
    barrier = context.Barrier(worker_count)
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_append,
            args=(
                str(path),
                f"process-request-{index}",
                barrier,
                results,
            ),
        )
        for index in range(worker_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=45)

    hung = [process for process in processes if process.is_alive()]
    for process in hung:
        process.terminate()
        process.join(timeout=10)
    assert hung == []
    assert all(process.exitcode == 0 for process in processes)
    outcomes = []
    for _ in processes:
        try:
            outcomes.append(results.get(timeout=10))
        except Empty as exc:
            raise AssertionError("worker did not report an outcome") from exc
    errors = [payload for status, payload in outcomes if status != "ok"]
    assert errors == []
    successes = [payload for status, payload in outcomes if status == "ok"]
    assert sum(result["accepted"] for result in successes) == 1

    reopened = SQLiteGenerationStore(path)
    snapshot = reopened.snapshot("process-session")
    assert snapshot.source_count == 1
    assert [record.id for record in snapshot.records] == ["event-0"]
    assert reopened.integrity_report()["passed"] is True
