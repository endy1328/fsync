from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil

from fsync.config import BidirectionalJobConfig
from fsync.planner import BidirectionalPlanner, ConflictEntry
from fsync.state import BaselineEntry, BidirectionalState, FileFingerprint, load_state, save_state
from fsync.syncer import SourceFileBusyError, TargetSyncError


logger = logging.getLogger("fsync.bidir")
MISSING_FINGERPRINT = FileFingerprint(False, None, None, None)


@dataclass(slots=True, frozen=True)
class BidirectionalSyncResult:
    copied_files: int
    deleted_files: int
    deferred_files: tuple[str, ...]
    conflict_files: int
    conflicts: int


class ConflictAbortError(RuntimeError):
    """manual conflict policy에서 충돌이 있으면 발생합니다."""


class StateMismatchError(RuntimeError):
    """state 파일이 현재 bidirectional job과 일치하지 않을 때 발생합니다."""


class BidirectionalSyncEngine:
    def __init__(self, planner: BidirectionalPlanner | None = None) -> None:
        self.planner = planner or BidirectionalPlanner()

    def sync_job(self, job: BidirectionalJobConfig) -> BidirectionalSyncResult:
        self._ensure_peer_directory(job.left, "left")
        self._ensure_peer_directory(job.right, "right")

        baseline = load_state(job.state_file)
        self._validate_state_for_job(job, baseline)
        left_snapshot = self.planner.snapshot_directory(job.left)
        right_snapshot = self.planner.snapshot_directory(job.right)
        plan = self.planner.plan_job(job, left_snapshot, right_snapshot, baseline)
        if plan.conflicts and job.conflict_policy == "manual":
            raise ConflictAbortError(
                f"job '{job.name}'에서 수동 충돌이 {len(plan.conflicts)}개 발견되어 실행을 중단합니다."
            )

        deferred_paths: set[str] = set(plan.deferred_paths)
        copied_files = 0
        deleted_files = 0
        conflict_files = 0

        for action in plan.actions:
            if action.kind == "noop":
                continue
            if action.kind == "copy_left_to_right":
                copied = self._copy_peer_file(job.left, job.right, action.relative_path, left_snapshot, deferred_paths)
                copied_files += int(copied)
                continue
            if action.kind == "copy_right_to_left":
                copied = self._copy_peer_file(job.right, job.left, action.relative_path, right_snapshot, deferred_paths)
                copied_files += int(copied)
                continue
            if action.kind == "delete_left":
                deleted_files += int(self._delete_peer_file(job.left, action.relative_path))
                continue
            if action.kind == "delete_right":
                deleted_files += int(self._delete_peer_file(job.right, action.relative_path))
                continue
            raise RuntimeError(f"지원하지 않는 planner action입니다: {action.kind}")

        if plan.conflicts and job.conflict_policy != "keep_both":
            raise ConflictAbortError(
                f"job '{job.name}'에서 자동 해소할 수 없는 충돌이 {len(plan.conflicts)}개 발견되었습니다."
            )

        for conflict in plan.conflicts:
            if conflict.relative_path in deferred_paths:
                continue
            written = self._resolve_keep_both_conflict(job, conflict, left_snapshot, right_snapshot, deferred_paths)
            conflict_files += written

        final_left_snapshot = self.planner.snapshot_directory(job.left)
        final_right_snapshot = self.planner.snapshot_directory(job.right)
        next_state = self._build_next_state(
            job,
            final_left_snapshot,
            final_right_snapshot,
            baseline,
            deferred_paths,
        )
        save_state(job.state_file, next_state)

        return BidirectionalSyncResult(
            copied_files=copied_files,
            deleted_files=deleted_files,
            deferred_files=tuple(sorted(deferred_paths)),
            conflict_files=conflict_files,
            conflicts=len(plan.conflicts),
        )

    def _validate_state_for_job(
        self,
        job: BidirectionalJobConfig,
        baseline: BidirectionalState | None,
    ) -> None:
        if baseline is None:
            return

        mismatches: list[str] = []
        if baseline.job_name != job.name:
            mismatches.append(f"job_name(state={baseline.job_name}, current={job.name})")
        if Path(baseline.left_root).resolve() != job.left.resolve():
            mismatches.append(f"left_root(state={baseline.left_root}, current={job.left})")
        if Path(baseline.right_root).resolve() != job.right.resolve():
            mismatches.append(f"right_root(state={baseline.right_root}, current={job.right})")

        if mismatches:
            raise StateMismatchError(
                f"job '{job.name}'의 state 파일이 현재 설정과 일치하지 않습니다: {', '.join(mismatches)}"
            )

    def _ensure_peer_directory(self, path: Path, label: str) -> None:
        if path.exists() and not path.is_dir():
            raise NotADirectoryError(f"{label} 경로가 디렉토리가 아닙니다: {path}")
        path.mkdir(parents=True, exist_ok=True)

    def _copy_peer_file(
        self,
        source_root: Path,
        target_root: Path,
        relative_path: str,
        source_snapshot: dict[str, FileFingerprint],
        deferred_paths: set[str],
    ) -> bool:
        if relative_path in deferred_paths:
            return False
        fingerprint = source_snapshot.get(relative_path)
        if fingerprint is None or not fingerprint.exists:
            return False

        source_path = source_root / relative_path
        target_path = target_root / relative_path
        if not self._source_is_ready(source_path, fingerprint):
            deferred_paths.add(relative_path)
            return False

        try:
            self._copy_file(source_path, target_path)
        except SourceFileBusyError:
            deferred_paths.add(relative_path)
            return False
        except OSError as exc:
            raise TargetSyncError(
                f"'{source_path}'를 '{target_path}'로 복사하지 못했습니다: {exc}"
            ) from exc
        logger.info("bidirectional copy %s -> %s", source_path, target_path)
        return True

    def _delete_peer_file(self, root: Path, relative_path: str) -> bool:
        target_path = root / relative_path
        if not target_path.exists():
            return False
        try:
            target_path.unlink()
        except OSError as exc:
            raise TargetSyncError(f"'{target_path}'를 삭제하지 못했습니다: {exc}") from exc
        self._prune_empty_directories(root)
        logger.info("bidirectional delete %s", target_path)
        return True

    def _resolve_keep_both_conflict(
        self,
        job: BidirectionalJobConfig,
        conflict: ConflictEntry,
        left_snapshot: dict[str, FileFingerprint],
        right_snapshot: dict[str, FileFingerprint],
        deferred_paths: set[str],
    ) -> int:
        copies: list[tuple[Path, Path]] = []
        relative_path = conflict.relative_path
        left_path = job.left / relative_path
        right_path = job.right / relative_path

        if relative_path in deferred_paths:
            return 0

        if relative_path in left_snapshot:
            if not self._source_is_ready(left_path, left_snapshot[relative_path]):
                deferred_paths.add(relative_path)
                return 0
            copies.append((left_path, self._build_conflict_path(job.right, relative_path, "left")))

        if relative_path in right_snapshot:
            if not self._source_is_ready(right_path, right_snapshot[relative_path]):
                deferred_paths.add(relative_path)
                return 0
            copies.append((right_path, self._build_conflict_path(job.left, relative_path, "right")))

        written = 0
        for source_path, target_path in copies:
            try:
                self._copy_file(source_path, target_path)
            except SourceFileBusyError:
                deferred_paths.add(relative_path)
                return written
            except OSError as exc:
                raise TargetSyncError(
                    f"충돌 파일 '{source_path}'를 '{target_path}'로 기록하지 못했습니다: {exc}"
                ) from exc
            written += 1
            logger.info("bidirectional conflict copy %s -> %s", source_path, target_path)

        return written

    def _build_conflict_path(self, root: Path, relative_path: str, side: str) -> Path:
        relative = Path(relative_path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = "".join(relative.suffixes)
        stem = relative.name[:-len(suffix)] if suffix else relative.name
        conflict_name = f"{stem}.conflict-{side}-{timestamp}{suffix}"
        return root / relative.parent / conflict_name

    def _copy_file(self, source_path: Path, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            source_file = source_path.open("rb")
        except OSError as exc:
            if self._is_access_related_error(exc):
                raise SourceFileBusyError(source_path) from exc
            raise

        with source_file:
            try:
                target_file = target_path.open("wb")
            except OSError:
                raise

            with target_file:
                try:
                    shutil.copyfileobj(source_file, target_file)
                except OSError as exc:
                    if self._is_access_related_error(exc):
                        raise SourceFileBusyError(source_path) from exc
                    raise

        shutil.copystat(source_path, target_path)

    def _source_is_ready(self, source_path: Path, expected: FileFingerprint) -> bool:
        try:
            stat = source_path.stat()
            if stat.st_size != expected.size or stat.st_mtime_ns != expected.mtime_ns:
                return False
            with source_path.open("rb") as source_file:
                source_file.read(1)
        except OSError as exc:
            if self._is_access_related_error(exc):
                return False
            raise
        return True

    def _is_access_related_error(self, exc: OSError) -> bool:
        if isinstance(exc, PermissionError):
            return True
        return getattr(exc, "errno", None) in {13, 16}

    def _build_next_state(
        self,
        job: BidirectionalJobConfig,
        left_snapshot: dict[str, FileFingerprint],
        right_snapshot: dict[str, FileFingerprint],
        baseline: BidirectionalState | None,
        deferred_paths: set[str],
    ) -> BidirectionalState:
        entries: dict[str, BaselineEntry] = {}
        all_paths = set(left_snapshot) | set(right_snapshot)
        if baseline is not None:
            all_paths.update(baseline.entries)

        for relative_path in sorted(all_paths):
            if relative_path in deferred_paths:
                if baseline is not None and relative_path in baseline.entries:
                    entries[relative_path] = baseline.entries[relative_path]
                continue

            left_fingerprint = left_snapshot.get(relative_path, MISSING_FINGERPRINT)
            right_fingerprint = right_snapshot.get(relative_path, MISSING_FINGERPRINT)
            if not left_fingerprint.exists and not right_fingerprint.exists:
                continue
            entries[relative_path] = BaselineEntry(left=left_fingerprint, right=right_fingerprint)

        return BidirectionalState(
            job_name=job.name,
            left_root=str(job.left),
            right_root=str(job.right),
            last_synced_at=datetime.now(timezone.utc).isoformat(),
            entries=entries,
        )

    def _prune_empty_directories(self, root: Path) -> None:
        for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
            try:
                path.rmdir()
            except OSError:
                continue
