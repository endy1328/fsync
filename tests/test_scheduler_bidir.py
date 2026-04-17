from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fsync.bidir import BidirectionalSyncResult
from fsync.config import AppConfig, BidirectionalJobConfig, JobConfig
from fsync.scheduler import Scheduler
from fsync.syncer import JobSyncResult


class SchedulerBidirectionalTests(unittest.TestCase):
    def test_run_once_routes_jobs_to_matching_engines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            one_way = JobConfig(
                name="one-way",
                source=root / "source",
                targets=(root / "target",),
            )
            bidir = BidirectionalJobConfig(
                name="bidir",
                left=root / "left",
                right=root / "right",
                state_file=root / "state.json",
            )
            scheduler = Scheduler(AppConfig(max_workers=2, log_level="INFO", jobs=(one_way, bidir)))

            one_way_result = JobSyncResult(copied_files=1, deferred_files=(), deleted_files=0, targets=1)
            bidir_result = BidirectionalSyncResult(
                copied_files=1,
                deleted_files=0,
                deferred_files=(),
                conflict_files=0,
                conflicts=0,
            )

            with mock.patch.object(scheduler.one_way_engine, "sync_job", return_value=one_way_result) as sync_one_way:
                with mock.patch.object(scheduler.bidirectional_engine, "sync_job", return_value=bidir_result) as sync_bidir:
                    scheduler.run_once()

            sync_one_way.assert_called_once_with(one_way)
            sync_bidir.assert_called_once_with(bidir)

    def test_run_once_collects_failure_from_bidirectional_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            one_way = JobConfig(
                name="one-way",
                source=root / "source",
                targets=(root / "target",),
            )
            bidir = BidirectionalJobConfig(
                name="bidir",
                left=root / "left",
                right=root / "right",
                state_file=root / "state.json",
            )
            scheduler = Scheduler(AppConfig(max_workers=2, log_level="INFO", jobs=(one_way, bidir)))

            with mock.patch.object(scheduler.one_way_engine, "sync_job", return_value=object()):
                with mock.patch.object(scheduler.bidirectional_engine, "sync_job", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError):
                        scheduler.run_once()

    def test_submit_if_due_routes_mixed_jobs_for_run_forever_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            one_way = JobConfig(name="one-way", source=root / "source", targets=(root / "target",))
            bidir = BidirectionalJobConfig(name="bidir", left=root / "left", right=root / "right", state_file=root / "state.json")
            scheduler = Scheduler(AppConfig(max_workers=2, log_level="INFO", jobs=(one_way, bidir)))

            submitted: list[tuple[str, object]] = []

            def submit_side_effect(fn, state):
                submitted.append((state.config.name, scheduler._select_engine(state.config)))

                class DummyFuture:
                    def add_done_callback(self, _cb):
                        return None
                return DummyFuture()

            with mock.patch.object(scheduler.executor, "submit", side_effect=submit_side_effect):
                now = 0.0
                for state in scheduler.states:
                    state.next_run_at = 0.0
                    scheduler._submit_if_due(state, now)

            self.assertEqual([name for name, _ in submitted], ["one-way", "bidir"])
            self.assertIs(submitted[0][1], scheduler.one_way_engine)
            self.assertIs(submitted[1][1], scheduler.bidirectional_engine)

    def test_complete_allows_retry_after_failure_on_run_forever_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bidir = BidirectionalJobConfig(name="bidir", left=root / "left", right=root / "right", state_file=root / "state.json")
            scheduler = Scheduler(AppConfig(max_workers=1, log_level="INFO", jobs=(bidir,)))
            state = scheduler.states[0]
            state.running = True

            class FailingFuture:
                def result(self):
                    raise RuntimeError("boom")

            scheduler._complete(state, FailingFuture())

            self.assertFalse(state.running)
            self.assertGreater(state.next_run_at, 0.0)


if __name__ == "__main__":
    unittest.main()
