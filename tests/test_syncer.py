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

from fsync.bidir import BidirectionalSyncEngine, ConflictAbortError
from fsync.config import AppConfig, BidirectionalJobConfig, JobConfig, load_config
from fsync.planner import BidirectionalPlanner
from fsync.scheduler import Scheduler
from fsync.state import (
    BaselineEntry,
    BidirectionalState,
    FileFingerprint,
    default_state_file_path,
    load_state,
    save_state,
)
from fsync.syncer import SyncEngine, TargetSyncError
from fsync.bidir import BidirectionalSyncEngine, ConflictAbortError, StateMismatchError


class ConfigLoadingTests(unittest.TestCase):
    def test_load_config_defaults_to_one_way_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(
                """
[app]
max_workers = 2
log_level = "debug"

[[jobs]]
name = "job-a"
source = "/tmp/source-a"
targets = ["/tmp/target-a"]
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.max_workers, 2)
            self.assertEqual(config.log_level, "DEBUG")
            self.assertEqual(config.state_dir, Path(".fsync-state").resolve())
            self.assertEqual(len(config.jobs), 1)
            job = config.jobs[0]
            self.assertIsInstance(job, JobConfig)
            self.assertEqual(job.mode, "one_way")
            self.assertEqual(job.source, Path("/tmp/source-a").resolve())
            self.assertEqual(job.targets, (Path("/tmp/target-a").resolve(),))

    def test_load_config_supports_bidirectional_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(
                """
[app]
state_dir = "/tmp/fsync-state"

[[jobs]]
name = "job-a"
mode = "bidirectional"
left = "/tmp/left"
right = "/tmp/right"
conflict_policy = "manual"
initial_sync = "left_wins"
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.state_dir, Path("/tmp/fsync-state").resolve())
            self.assertEqual(len(config.jobs), 1)
            job = config.jobs[0]
            self.assertIsInstance(job, BidirectionalJobConfig)
            self.assertEqual(job.mode, "bidirectional")
            self.assertEqual(job.left, Path("/tmp/left").resolve())
            self.assertEqual(job.right, Path("/tmp/right").resolve())
            self.assertEqual(job.conflict_policy, "manual")
            self.assertEqual(job.initial_sync, "left_wins")
            self.assertEqual(job.delete_policy, "tracked")
            self.assertEqual(job.state_file, Path("/tmp/fsync-state/job-a.json").resolve())

    def test_load_config_rejects_bidirectional_job_with_same_peers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(
                """
[[jobs]]
name = "job-a"
mode = "bidirectional"
left = "/tmp/shared"
right = "/tmp/shared"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "left와 right는 같을 수 없습니다"):
                load_config(config_path)

    def test_load_config_rejects_duplicate_bidirectional_peer_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(
                """
[[jobs]]
name = "job-a"
mode = "bidirectional"
left = "/tmp/left-a"
right = "/tmp/right-a"

[[jobs]]
name = "job-b"
mode = "bidirectional"
left = "/tmp/right-a"
right = "/tmp/right-b"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "중복 사용되었습니다"):
                load_config(config_path)


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


class BidirectionalPlannerTests(unittest.TestCase):
    def test_initial_left_only_file_is_copied_to_right(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={"docs/report.txt": FileFingerprint(True, 10, 100, "left")},
            right_snapshot={},
            baseline=None,
        )

        self.assertEqual(plan.conflicts, ())
        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].kind, "copy_left_to_right")
        self.assertEqual(plan.actions[0].relative_path, "docs/report.txt")

    def test_initial_manual_sync_marks_mismatched_files_as_conflict(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
            initial_sync="manual",
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={"docs/report.txt": FileFingerprint(True, 10, 100, "left")},
            right_snapshot={"docs/report.txt": FileFingerprint(True, 12, 101, "right")},
            baseline=None,
        )

        self.assertEqual(plan.actions, ())
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0].relative_path, "docs/report.txt")
        self.assertEqual(plan.conflicts[0].resolution, "manual")

    def test_initial_left_wins_sync_copies_left_version(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
            initial_sync="left_wins",
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={"docs/report.txt": FileFingerprint(True, 10, 100, "left")},
            right_snapshot={"docs/report.txt": FileFingerprint(True, 12, 101, "right")},
            baseline=None,
        )

        self.assertEqual(plan.conflicts, ())
        self.assertEqual(plan.actions[0].kind, "copy_left_to_right")
        self.assertEqual(plan.actions[0].reason, "initial_left_wins")

    def test_tracked_right_modification_is_copied_to_left(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
        )
        baseline = BidirectionalState(
            job_name="job",
            left_root="/tmp/left",
            right_root="/tmp/right",
            entries={
                "docs/report.txt": BaselineEntry(
                    left=FileFingerprint(True, 8, 90, "same"),
                    right=FileFingerprint(True, 8, 90, "same"),
                )
            },
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={"docs/report.txt": FileFingerprint(True, 8, 90, "same")},
            right_snapshot={"docs/report.txt": FileFingerprint(True, 11, 120, "right-new")},
            baseline=baseline,
        )

        self.assertEqual(plan.conflicts, ())
        self.assertEqual(plan.actions[0].kind, "copy_right_to_left")
        self.assertEqual(plan.actions[0].reason, "right_modified")

    def test_tracked_left_deletion_propagates_to_right(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
        )
        baseline = BidirectionalState(
            job_name="job",
            left_root="/tmp/left",
            right_root="/tmp/right",
            entries={
                "docs/report.txt": BaselineEntry(
                    left=FileFingerprint(True, 8, 90, "same"),
                    right=FileFingerprint(True, 8, 90, "same"),
                )
            },
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={},
            right_snapshot={"docs/report.txt": FileFingerprint(True, 8, 90, "same")},
            baseline=baseline,
        )

        self.assertEqual(plan.conflicts, ())
        self.assertEqual(plan.actions[0].kind, "delete_right")

    def test_both_modified_to_same_content_is_noop(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
        )
        baseline = BidirectionalState(
            job_name="job",
            left_root="/tmp/left",
            right_root="/tmp/right",
            entries={
                "docs/report.txt": BaselineEntry(
                    left=FileFingerprint(True, 8, 90, "same"),
                    right=FileFingerprint(True, 8, 90, "same"),
                )
            },
        )
        same_new = FileFingerprint(True, 11, 130, "same-new")
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={"docs/report.txt": same_new},
            right_snapshot={"docs/report.txt": same_new},
            baseline=baseline,
        )

        self.assertEqual(plan.conflicts, ())
        self.assertEqual(plan.actions[0].kind, "noop")
        self.assertEqual(plan.actions[0].reason, "both_converged")

    def test_both_modified_differently_creates_keep_both_conflict(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
            conflict_policy="keep_both",
        )
        baseline = BidirectionalState(
            job_name="job",
            left_root="/tmp/left",
            right_root="/tmp/right",
            entries={
                "docs/report.txt": BaselineEntry(
                    left=FileFingerprint(True, 8, 90, "same"),
                    right=FileFingerprint(True, 8, 90, "same"),
                )
            },
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={"docs/report.txt": FileFingerprint(True, 9, 120, "left-new")},
            right_snapshot={"docs/report.txt": FileFingerprint(True, 10, 125, "right-new")},
            baseline=baseline,
        )

        self.assertEqual(plan.actions, ())
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0].left_status, "modified")
        self.assertEqual(plan.conflicts[0].right_status, "modified")
        self.assertEqual(plan.conflicts[0].resolution, "keep_both")

    def test_delete_vs_modify_creates_conflict(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
        )
        baseline = BidirectionalState(
            job_name="job",
            left_root="/tmp/left",
            right_root="/tmp/right",
            entries={
                "docs/report.txt": BaselineEntry(
                    left=FileFingerprint(True, 8, 90, "same"),
                    right=FileFingerprint(True, 8, 90, "same"),
                )
            },
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={},
            right_snapshot={"docs/report.txt": FileFingerprint(True, 10, 125, "right-new")},
            baseline=baseline,
        )

        self.assertEqual(plan.actions, ())
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0].left_status, "deleted")
        self.assertEqual(plan.conflicts[0].right_status, "modified")

    def test_newer_wins_conflict_copies_newer_side(self) -> None:
        job = BidirectionalJobConfig(
            name="job",
            left=Path("/tmp/left"),
            right=Path("/tmp/right"),
            state_file=Path("/tmp/state.json"),
            conflict_policy="newer_wins",
        )
        baseline = BidirectionalState(
            job_name="job",
            left_root="/tmp/left",
            right_root="/tmp/right",
            entries={
                "docs/report.txt": BaselineEntry(
                    left=FileFingerprint(True, 8, 90, "same"),
                    right=FileFingerprint(True, 8, 90, "same"),
                )
            },
        )
        planner = BidirectionalPlanner()

        plan = planner.plan_job(
            job,
            left_snapshot={"docs/report.txt": FileFingerprint(True, 9, 120, "left-new")},
            right_snapshot={"docs/report.txt": FileFingerprint(True, 10, 125, "right-new")},
            baseline=baseline,
        )

        self.assertEqual(plan.conflicts, ())
        self.assertEqual(plan.actions[0].kind, "copy_right_to_left")
        self.assertEqual(plan.actions[0].reason, "conflict_newer_right")


class BidirectionalSyncEngineTests(unittest.TestCase):
    def _make_job(
        self,
        root: Path,
        *,
        name: str = "job",
        conflict_policy: str = "keep_both",
        initial_sync: str = "manual",
    ) -> BidirectionalJobConfig:
        left = root / "left"
        right = root / "right"
        left.mkdir()
        right.mkdir()
        return BidirectionalJobConfig(
            name=name,
            left=left,
            right=right,
            state_file=root / "state" / f"{name}.json",
            conflict_policy=conflict_policy,
            initial_sync=initial_sync,
        )

    def test_copies_new_left_file_to_right(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root)
            engine = BidirectionalSyncEngine()

            (job.left / "docs").mkdir()
            (job.left / "docs" / "report.txt").write_text("left-v1", encoding="utf-8")

            result = engine.sync_job(job)
            state = load_state(job.state_file)

            self.assertEqual(result.copied_files, 1)
            self.assertEqual(result.deleted_files, 0)
            self.assertEqual(result.deferred_files, ())
            self.assertEqual((job.right / "docs" / "report.txt").read_text(encoding="utf-8"), "left-v1")
            self.assertIsNotNone(state)
            assert state is not None
            self.assertIn("docs/report.txt", state.entries)

    def test_copies_new_right_file_to_left(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root)
            engine = BidirectionalSyncEngine()

            (job.right / "docs").mkdir()
            (job.right / "docs" / "report.txt").write_text("right-v1", encoding="utf-8")

            result = engine.sync_job(job)

            self.assertEqual(result.copied_files, 1)
            self.assertEqual(result.deleted_files, 0)
            self.assertEqual(result.deferred_files, ())
            self.assertEqual((job.left / "docs" / "report.txt").read_text(encoding="utf-8"), "right-v1")

    def test_tracked_deletion_is_propagated_to_peer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root)
            engine = BidirectionalSyncEngine()

            (job.left / "docs").mkdir()
            (job.left / "docs" / "report.txt").write_text("shared", encoding="utf-8")
            engine.sync_job(job)

            (job.left / "docs" / "report.txt").unlink()

            result = engine.sync_job(job)
            state = load_state(job.state_file)

            self.assertEqual(result.deleted_files, 1)
            self.assertFalse((job.right / "docs" / "report.txt").exists())
            self.assertIsNotNone(state)
            assert state is not None
            self.assertNotIn("docs/report.txt", state.entries)

    def test_manual_conflict_leaves_files_and_state_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root, conflict_policy="manual", initial_sync="left_wins")
            engine = BidirectionalSyncEngine()

            (job.left / "docs").mkdir()
            (job.left / "docs" / "report.txt").write_text("base", encoding="utf-8")
            engine.sync_job(job)
            baseline = load_state(job.state_file)

            (job.left / "docs" / "report.txt").write_text("left-change", encoding="utf-8")
            (job.right / "docs" / "report.txt").write_text("right-change", encoding="utf-8")

            with self.assertRaises(ConflictAbortError):
                engine.sync_job(job)

            self.assertEqual((job.left / "docs" / "report.txt").read_text(encoding="utf-8"), "left-change")
            self.assertEqual((job.right / "docs" / "report.txt").read_text(encoding="utf-8"), "right-change")
            self.assertEqual(load_state(job.state_file), baseline)
            self.assertEqual(list(job.left.glob("docs/*.conflict-*")), [])
            self.assertEqual(list(job.right.glob("docs/*.conflict-*")), [])

    def test_keep_both_conflict_writes_artifacts_without_polluting_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root, conflict_policy="keep_both", initial_sync="left_wins")
            engine = BidirectionalSyncEngine()

            (job.left / "docs").mkdir()
            (job.left / "docs" / "report.txt").write_text("base", encoding="utf-8")
            engine.sync_job(job)

            (job.left / "docs" / "report.txt").write_text("left-change", encoding="utf-8")
            (job.right / "docs" / "report.txt").write_text("right-change", encoding="utf-8")

            with mock.patch.object(
                engine,
                "_build_conflict_path",
                side_effect=[
                    job.right / "docs" / "report.conflict-left.txt",
                    job.left / "docs" / "report.conflict-right.txt",
                ],
            ):
                result = engine.sync_job(job)

            state = load_state(job.state_file)

            self.assertEqual(result.copied_files, 0)
            self.assertEqual(result.conflicts, 1)
            self.assertEqual(result.conflict_files, 2)
            self.assertEqual((job.right / "docs" / "report.conflict-left.txt").read_text(encoding="utf-8"), "left-change")
            self.assertEqual((job.left / "docs" / "report.conflict-right.txt").read_text(encoding="utf-8"), "right-change")
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(set(state.entries), {"docs/report.txt"})

    def test_busy_file_is_deferred_and_keeps_previous_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root)
            engine = BidirectionalSyncEngine()

            (job.left / "ready.txt").write_text("ready-v1", encoding="utf-8")
            (job.left / "busy.txt").write_text("busy-v1", encoding="utf-8")
            engine.sync_job(job)
            baseline = load_state(job.state_file)
            assert baseline is not None
            busy_before = baseline.entries["busy.txt"]

            (job.left / "ready.txt").write_text("ready-v2", encoding="utf-8")
            (job.left / "busy.txt").write_text("busy-v2", encoding="utf-8")

            def source_ready(source_path: Path, _expected: FileFingerprint) -> bool:
                return source_path.name != "busy.txt"

            with mock.patch.object(engine, "_source_is_ready", side_effect=source_ready):
                result = engine.sync_job(job)

            state = load_state(job.state_file)

            self.assertEqual(result.copied_files, 1)
            self.assertEqual(result.deferred_files, ("busy.txt",))
            self.assertEqual((job.right / "ready.txt").read_text(encoding="utf-8"), "ready-v2")
            self.assertEqual((job.right / "busy.txt").read_text(encoding="utf-8"), "busy-v1")
            self.assertIsNotNone(state)
            assert state is not None
            self.assertNotEqual(state.entries["ready.txt"], baseline.entries["ready.txt"])
            self.assertEqual(state.entries["busy.txt"], busy_before)

    def test_failure_does_not_commit_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root)
            engine = BidirectionalSyncEngine()

            (job.left / "report.txt").write_text("v1", encoding="utf-8")
            engine.sync_job(job)
            baseline = load_state(job.state_file)

            (job.left / "report.txt").write_text("v2", encoding="utf-8")

            with mock.patch.object(engine, "_copy_file", side_effect=OSError("target busy")):
                with self.assertRaises(TargetSyncError):
                    engine.sync_job(job)

            self.assertEqual(load_state(job.state_file), baseline)
            self.assertEqual((job.right / "report.txt").read_text(encoding="utf-8"), "v1")

    def test_rejects_state_with_different_job_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root, name="job-a")
            engine = BidirectionalSyncEngine()
            save_state(
                job.state_file,
                BidirectionalState(
                    job_name="job-b",
                    left_root=str(job.left),
                    right_root=str(job.right),
                    entries={},
                ),
            )

            with self.assertRaises(StateMismatchError):
                engine.sync_job(job)

    def test_rejects_state_with_swapped_roots_without_committing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root, name="job-a")
            engine = BidirectionalSyncEngine()
            save_state(
                job.state_file,
                BidirectionalState(
                    job_name=job.name,
                    left_root=str(job.right),
                    right_root=str(job.left),
                    entries={},
                ),
            )
            (job.left / "report.txt").write_text("v1", encoding="utf-8")
            baseline = load_state(job.state_file)

            with self.assertRaises(StateMismatchError):
                engine.sync_job(job)

            self.assertEqual(load_state(job.state_file), baseline)
            self.assertFalse((job.right / "report.txt").exists())

    def test_successful_rerun_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = self._make_job(root)
            engine = BidirectionalSyncEngine()

            (job.left / "report.txt").write_text("stable", encoding="utf-8")

            first_result = engine.sync_job(job)
            second_result = engine.sync_job(job)

            self.assertEqual(first_result.copied_files, 1)
            self.assertEqual(second_result.copied_files, 0)
            self.assertEqual(second_result.deleted_files, 0)
            self.assertEqual(second_result.deferred_files, ())
            self.assertEqual(second_result.conflict_files, 0)
            self.assertEqual(second_result.conflicts, 0)


class StateStoreTests(unittest.TestCase):
    def test_default_state_file_path_uses_job_name_under_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = default_state_file_path("documents", Path(temp_dir) / "state")

            self.assertEqual(state_path, (Path(temp_dir) / "state" / "documents.json").resolve())

    def test_save_and_load_state_round_trips_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state" / "job.json"
            state = BidirectionalState(
                job_name="job-a",
                left_root="/tmp/left",
                right_root="/tmp/right",
                last_synced_at="2026-04-17T15:00:00+09:00",
                entries={
                    "docs/report.txt": BaselineEntry(
                        left=FileFingerprint(
                            exists=True,
                            size=12,
                            mtime_ns=101,
                            sha256="left-hash",
                        ),
                        right=FileFingerprint(
                            exists=False,
                            size=None,
                            mtime_ns=None,
                            sha256=None,
                        ),
                    )
                },
            )

            save_state(state_path, state)
            loaded = load_state(state_path)

            self.assertEqual(loaded, state)

    def test_load_state_returns_none_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "missing.json"

            self.assertIsNone(load_state(state_path))

    def test_save_state_does_not_overwrite_existing_file_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state" / "job.json"
            original_state = BidirectionalState(
                job_name="job-a",
                left_root="/tmp/left",
                right_root="/tmp/right",
                entries={},
            )
            next_state = BidirectionalState(
                job_name="job-a",
                left_root="/tmp/left",
                right_root="/tmp/right",
                last_synced_at="2026-04-17T16:00:00+09:00",
                entries={
                    "docs/report.txt": BaselineEntry(
                        left=FileFingerprint(True, 7, 202, "left-next"),
                        right=FileFingerprint(True, 7, 202, "right-next"),
                    )
                },
            )

            save_state(state_path, original_state)

            with mock.patch("fsync.state.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_state(state_path, next_state)

            self.assertEqual(load_state(state_path), original_state)
            temp_files = list(state_path.parent.glob(".job.*.tmp"))
            self.assertEqual(temp_files, [])


if __name__ == "__main__":
    unittest.main()
