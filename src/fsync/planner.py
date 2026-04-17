from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Literal

from fsync.config import BidirectionalJobConfig
from fsync.state import BaselineEntry, BidirectionalState, FileFingerprint


ActionKind = Literal[
    "copy_left_to_right",
    "copy_right_to_left",
    "delete_left",
    "delete_right",
    "write_conflict_left",
    "write_conflict_right",
    "noop",
]
StatusKind = Literal["missing", "created", "modified", "deleted", "unchanged"]
ResolutionKind = Literal["keep_both", "manual", "newer_wins"]

MISSING_FINGERPRINT = FileFingerprint(False, None, None, None)


@dataclass(slots=True, frozen=True)
class PlannedAction:
    kind: ActionKind
    relative_path: str
    reason: str


@dataclass(slots=True, frozen=True)
class ConflictEntry:
    relative_path: str
    left_status: StatusKind
    right_status: StatusKind
    resolution: ResolutionKind


@dataclass(slots=True, frozen=True)
class BidirectionalPlan:
    actions: tuple[PlannedAction, ...]
    conflicts: tuple[ConflictEntry, ...]
    deferred_paths: tuple[str, ...] = ()


class BidirectionalPlanner:
    def plan_job(
        self,
        job: BidirectionalJobConfig,
        left_snapshot: dict[str, FileFingerprint],
        right_snapshot: dict[str, FileFingerprint],
        baseline: BidirectionalState | None,
    ) -> BidirectionalPlan:
        actions: list[PlannedAction] = []
        conflicts: list[ConflictEntry] = []

        tracked_paths = set(left_snapshot) | set(right_snapshot)
        if baseline is not None:
            tracked_paths.update(baseline.entries)

        for relative_path in sorted(tracked_paths):
            left_current = left_snapshot.get(relative_path, MISSING_FINGERPRINT)
            right_current = right_snapshot.get(relative_path, MISSING_FINGERPRINT)
            baseline_entry = baseline.entries.get(relative_path) if baseline is not None else None

            if baseline_entry is None:
                path_actions, path_conflict = self._plan_initial_path(job, relative_path, left_current, right_current)
            else:
                path_actions, path_conflict = self._plan_tracked_path(
                    job,
                    relative_path,
                    left_current,
                    right_current,
                    baseline_entry,
                )

            actions.extend(path_actions)
            if path_conflict is not None:
                conflicts.append(path_conflict)

        return BidirectionalPlan(actions=tuple(actions), conflicts=tuple(conflicts))

    def snapshot_directory(self, root: Path) -> dict[str, FileFingerprint]:
        snapshot: dict[str, FileFingerprint] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _is_conflict_artifact(path):
                continue
            relative_path = path.relative_to(root).as_posix()
            snapshot[relative_path] = self.fingerprint_path(path)
        return snapshot

    def fingerprint_path(self, path: Path) -> FileFingerprint:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return FileFingerprint(
            exists=True,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha256=digest.hexdigest(),
        )

    def _plan_initial_path(
        self,
        job: BidirectionalJobConfig,
        relative_path: str,
        left_current: FileFingerprint,
        right_current: FileFingerprint,
    ) -> tuple[list[PlannedAction], ConflictEntry | None]:
        if left_current.exists and not right_current.exists:
            return [PlannedAction("copy_left_to_right", relative_path, "initial_left_only")], None
        if right_current.exists and not left_current.exists:
            return [PlannedAction("copy_right_to_left", relative_path, "initial_right_only")], None
        if not left_current.exists and not right_current.exists:
            return [PlannedAction("noop", relative_path, "initial_missing_on_both")], None
        if left_current == right_current:
            return [PlannedAction("noop", relative_path, "initial_same_content")], None
        if job.initial_sync == "left_wins":
            return [PlannedAction("copy_left_to_right", relative_path, "initial_left_wins")], None
        if job.initial_sync == "right_wins":
            return [PlannedAction("copy_right_to_left", relative_path, "initial_right_wins")], None
        return [], ConflictEntry(
            relative_path=relative_path,
            left_status="created",
            right_status="created",
            resolution="manual",
        )

    def _plan_tracked_path(
        self,
        job: BidirectionalJobConfig,
        relative_path: str,
        left_current: FileFingerprint,
        right_current: FileFingerprint,
        baseline_entry: BaselineEntry,
    ) -> tuple[list[PlannedAction], ConflictEntry | None]:
        left_status = _classify_change(left_current, baseline_entry.left)
        right_status = _classify_change(right_current, baseline_entry.right)

        if left_status == "unchanged" and right_status == "unchanged":
            return [PlannedAction("noop", relative_path, "unchanged")], None

        if left_status != "unchanged" and right_status == "unchanged":
            return self._plan_single_sided_change(
                relative_path,
                changed_side="left",
                changed_status=left_status,
            )

        if right_status != "unchanged" and left_status == "unchanged":
            return self._plan_single_sided_change(
                relative_path,
                changed_side="right",
                changed_status=right_status,
            )

        if left_current == right_current:
            if not left_current.exists:
                return [PlannedAction("noop", relative_path, "both_deleted")], None
            return [PlannedAction("noop", relative_path, "both_converged")], None

        if job.conflict_policy == "newer_wins" and left_current.exists and right_current.exists:
            if left_current.mtime_ns is not None and right_current.mtime_ns is not None:
                if left_current.mtime_ns > right_current.mtime_ns:
                    return [PlannedAction("copy_left_to_right", relative_path, "conflict_newer_left")], None
                if right_current.mtime_ns > left_current.mtime_ns:
                    return [PlannedAction("copy_right_to_left", relative_path, "conflict_newer_right")], None

        return [], ConflictEntry(
            relative_path=relative_path,
            left_status=left_status,
            right_status=right_status,
            resolution=job.conflict_policy,
        )

    def _plan_single_sided_change(
        self,
        relative_path: str,
        changed_side: Literal["left", "right"],
        changed_status: StatusKind,
    ) -> tuple[list[PlannedAction], ConflictEntry | None]:
        if changed_status in {"created", "modified"}:
            if changed_side == "left":
                return [PlannedAction("copy_left_to_right", relative_path, f"left_{changed_status}")], None
            return [PlannedAction("copy_right_to_left", relative_path, f"right_{changed_status}")], None
        if changed_status == "deleted":
            if changed_side == "left":
                return [PlannedAction("delete_right", relative_path, "tracked_delete_from_left")], None
            return [PlannedAction("delete_left", relative_path, "tracked_delete_from_right")], None
        return [PlannedAction("noop", relative_path, f"{changed_side}_{changed_status}")], None


def _classify_change(current: FileFingerprint, baseline: FileFingerprint) -> StatusKind:
    if current == baseline:
        return "unchanged"
    if not baseline.exists and current.exists:
        return "created"
    if baseline.exists and not current.exists:
        return "deleted"
    if not baseline.exists and not current.exists:
        return "missing"
    return "modified"


def _is_conflict_artifact(path: Path) -> bool:
    return ".conflict-left" in path.name or ".conflict-right" in path.name
