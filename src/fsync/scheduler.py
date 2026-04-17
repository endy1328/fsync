from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
import threading
import time

from fsync.config import AppConfig, JobConfig
from fsync.syncer import SyncEngine


logger = logging.getLogger("fsync.scheduler")


@dataclass(slots=True)
class JobState:
    config: JobConfig
    next_run_at: float = 0.0
    running: bool = False
    last_future: Future | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class Scheduler:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.executor = ThreadPoolExecutor(max_workers=config.max_workers, thread_name_prefix="fsync")
        self.engine = SyncEngine()
        self.states = [JobState(config=job) for job in config.jobs]
        now = time.monotonic()
        for state in self.states:
            state.next_run_at = now

    def run_once(self) -> None:
        futures = [self.executor.submit(self.engine.sync_job, state.config) for state in self.states]
        errors: list[Exception] = []
        try:
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    logger.exception("1회 실행 중 job이 실패했습니다")
                    errors.append(exc)
        finally:
            self.shutdown()

        if errors:
            raise RuntimeError(f"1회 실행 중 {len(errors)}개의 job이 실패했습니다") from errors[0]

    def run_forever(self) -> None:
        logger.info("스케줄러를 시작합니다. 전체 job 수: %d", len(self.states))
        while True:
            now = time.monotonic()
            for state in self.states:
                self._submit_if_due(state, now)
            time.sleep(0.5)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)

    def _submit_if_due(self, state: JobState, now: float) -> None:
        with state.lock:
            if state.running or now < state.next_run_at:
                return

            state.running = True
            future = self.executor.submit(self.engine.sync_job, state.config)
            state.last_future = future
            future.add_done_callback(lambda fut, job_state=state: self._complete(job_state, fut))

    def _complete(self, state: JobState, future: Future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("job '%s' 실행이 실패했습니다", state.config.name)
        finally:
            with state.lock:
                state.running = False
                state.next_run_at = time.monotonic() + state.config.interval_seconds
