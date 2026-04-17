from __future__ import annotations

import os
import tempfile
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fsync.config import AppConfig, JobConfig
from fsync.scheduler import Scheduler
from fsync.syncer import SyncEngine, TargetSyncError


class SyncEngineTests(unittest.TestCase):
    def test_copies_new_and_modified_files_to_multiple_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target_a = root / "target-a"
            target_b = root / "target-b"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("value-1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target_a, target_b),
                interval_seconds=60,
                copy_deleted=False,
            )
            engine = SyncEngine()

            result = engine.sync_job(job)
            self.assertEqual((target_a / "example.txt").read_text(encoding="utf-8"), "value-1")
            self.assertEqual((target_b / "example.txt").read_text(encoding="utf-8"), "value-1")
            self.assertEqual(result.copied_files, 1)
            self.assertEqual(result.deferred_files, ())

    def test_does_not_copy_again_when_targets_already_match_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("value-1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target,),
                interval_seconds=60,
                copy_deleted=False,
            )

            first_engine = SyncEngine()
            first_result = first_engine.sync_job(job)
            self.assertEqual(first_result.copied_files, 1)

            second_engine = SyncEngine()
            second_result = second_engine.sync_job(job)
            self.assertEqual(second_result.copied_files, 0)
            self.assertEqual(second_result.deferred_files, ())

    def test_does_not_copy_when_only_timestamp_precision_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()

            source_file = source / "example.txt"
            target_file = target / "example.txt"
            source_file.write_text("same-content", encoding="utf-8")
            target_file.write_text("same-content", encoding="utf-8")

            source_stat = source_file.stat()
            os.utime(target_file, ns=(source_stat.st_mtime_ns // 1_000_000_000 * 1_000_000_000,) * 2)

            job = JobConfig(
                name="job",
                source=source,
                targets=(target,),
                interval_seconds=60,
                copy_deleted=False,
            )

            engine = SyncEngine()
            result = engine.sync_job(job)

            self.assertEqual(result.copied_files, 0)
            self.assertEqual(result.deferred_files, ())

    def test_copies_only_missing_target_when_other_target_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target_a = root / "target-a"
            target_b = root / "target-b"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("value-1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target_a, target_b),
                interval_seconds=60,
                copy_deleted=False,
            )

            engine = SyncEngine()
            engine.sync_job(job)

            (target_b / "example.txt").unlink()

            retry_engine = SyncEngine()
            result = retry_engine.sync_job(job)

            self.assertEqual(result.copied_files, 1)
            self.assertEqual((target_a / "example.txt").read_text(encoding="utf-8"), "value-1")
            self.assertEqual((target_b / "example.txt").read_text(encoding="utf-8"), "value-1")

            file_path.write_text("value-2-updated", encoding="utf-8")
            result = engine.sync_job(job)

            self.assertEqual((target_a / "example.txt").read_text(encoding="utf-8"), "value-2-updated")
            self.assertEqual((target_b / "example.txt").read_text(encoding="utf-8"), "value-2-updated")
            self.assertEqual(result.copied_files, 1)
            self.assertEqual(result.deferred_files, ())

    def test_deletes_target_file_when_copy_deleted_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("v1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target,),
                interval_seconds=60,
                copy_deleted=True,
            )
            engine = SyncEngine()

            engine.sync_job(job)
            file_path.unlink()
            result = engine.sync_job(job)

            self.assertFalse((target / "example.txt").exists())
            self.assertEqual(result.deleted_files, 1)

    def test_defers_busy_source_file_until_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("v1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target,),
                interval_seconds=60,
                copy_deleted=False,
            )
            engine = SyncEngine()

            with mock.patch.object(engine, "_source_is_ready", side_effect=[False, True]):
                first_result = engine.sync_job(job)
                self.assertEqual(first_result.copied_files, 0)
                self.assertEqual(first_result.deferred_files, ("example.txt",))
                self.assertFalse((target / "example.txt").exists())

                second_result = engine.sync_job(job)

            self.assertEqual(second_result.copied_files, 1)
            self.assertEqual(second_result.deferred_files, ())
            self.assertEqual((target / "example.txt").read_text(encoding="utf-8"), "v1")

    def test_copy_access_error_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("v1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target,),
                interval_seconds=60,
                copy_deleted=False,
            )
            engine = SyncEngine()

            with mock.patch("fsync.syncer.shutil.copyfileobj", side_effect=PermissionError("source busy")):
                result = engine.sync_job(job)

            self.assertEqual(result.copied_files, 0)
            self.assertEqual(result.deferred_files, ("example.txt",))

    def test_target_failure_aborts_job_and_retries_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target_a = root / "target-a"
            target_b = root / "target-b"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("v1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target_a, target_b),
                interval_seconds=60,
                copy_deleted=False,
            )
            engine = SyncEngine()

            original_copy = engine._copy_file

            def fail_second_target(source_path: Path, target_path: Path, snapshot: object) -> None:
                if target_path.parts[-2] == "target-b":
                    raise PermissionError("target is not writable")
                original_copy(source_path, target_path, snapshot)

            with mock.patch.object(engine, "_copy_file", side_effect=fail_second_target):
                with self.assertRaises(TargetSyncError):
                    engine.sync_job(job)

            self.assertTrue((target_a / "example.txt").exists())
            self.assertFalse((target_b / "example.txt").exists())

            retry_result = engine.sync_job(job)

            self.assertEqual(retry_result.copied_files, 1)
            self.assertEqual(retry_result.deferred_files, ())
            self.assertEqual((target_b / "example.txt").read_text(encoding="utf-8"), "v1")

    def test_target_metadata_failure_is_not_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()

            file_path = source / "example.txt"
            file_path.write_text("v1", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target,),
                interval_seconds=60,
                copy_deleted=False,
            )
            engine = SyncEngine()

            with mock.patch.object(engine, "_apply_target_metadata", side_effect=PermissionError("target busy")):
                with self.assertRaises(TargetSyncError):
                    engine.sync_job(job)

    def test_successful_files_are_committed_while_busy_files_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()

            ready_path = source / "ready.txt"
            busy_path = source / "busy.txt"
            ready_path.write_text("ready", encoding="utf-8")
            busy_path.write_text("busy", encoding="utf-8")

            job = JobConfig(
                name="job",
                source=source,
                targets=(target,),
                interval_seconds=60,
                copy_deleted=False,
            )
            engine = SyncEngine()

            def first_run_readiness(source_path: Path, _snapshot: object) -> bool:
                return source_path.name != "busy.txt"

            with mock.patch.object(engine, "_source_is_ready", side_effect=first_run_readiness):
                first_result = engine.sync_job(job)

            self.assertEqual(first_result.copied_files, 1)
            self.assertEqual(first_result.deferred_files, ("busy.txt",))
            self.assertEqual((target / "ready.txt").read_text(encoding="utf-8"), "ready")
            self.assertFalse((target / "busy.txt").exists())

            second_result = engine.sync_job(job)

            self.assertEqual(second_result.copied_files, 1)
            self.assertEqual(second_result.deferred_files, ())
            self.assertEqual((target / "ready.txt").read_text(encoding="utf-8"), "ready")
            self.assertEqual((target / "busy.txt").read_text(encoding="utf-8"), "busy")

    def test_run_once_waits_for_all_jobs_before_failing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_a = root / "source-a"
            source_b = root / "source-b"
            target_a = root / "target-a"
            target_b = root / "target-b"
            source_a.mkdir()
            source_b.mkdir()

            job_a = JobConfig(
                name="job-a",
                source=source_a,
                targets=(target_a,),
                interval_seconds=60,
                copy_deleted=False,
            )
            job_b = JobConfig(
                name="job-b",
                source=source_b,
                targets=(target_b,),
                interval_seconds=60,
                copy_deleted=False,
            )
            scheduler = Scheduler(AppConfig(max_workers=2, log_level="INFO", jobs=(job_a, job_b)))
            called: list[str] = []

            def sync_side_effect(job: JobConfig) -> None:
                called.append(job.name)
                if job.name == "job-a":
                    raise RuntimeError("boom")

            with mock.patch.object(scheduler.engine, "sync_job", side_effect=sync_side_effect):
                with self.assertRaises(RuntimeError):
                    scheduler.run_once()

            self.assertCountEqual(called, ["job-a", "job-b"])


if __name__ == "__main__":
    unittest.main()
