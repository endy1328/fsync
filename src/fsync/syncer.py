from __future__ import annotations

import errno
import hashlib
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil

from fsync.config import JobConfig


logger = logging.getLogger("fsync.syncer")


@dataclass(slots=True, frozen=True)
class FileSnapshot:
    relative_path: str
    size: int
    mtime_ns: int
    mode: int


@dataclass(slots=True, frozen=True)
class JobSyncResult:
    copied_files: int
    deferred_files: tuple[str, ...]
    deleted_files: int
    targets: int


class TargetSyncError(RuntimeError):
    """target을 안전하게 동기화할 수 없을 때 발생합니다."""


class SourceFileBusyError(RuntimeError):
    """source 파일을 다음 동기화 주기에 재시도해야 할 때 발생합니다."""


class SyncEngine:
    def __init__(self) -> None:
        pass

    def sync_job(self, job: JobConfig) -> JobSyncResult:
        if not job.source.exists():
            raise FileNotFoundError(f"source 디렉토리가 존재하지 않습니다: {job.source}")
        if not job.source.is_dir():
            raise NotADirectoryError(f"source 경로가 디렉토리가 아닙니다: {job.source}")

        current = self._scan_source(job.source)
        deferred_paths: set[str] = set()
        copied_paths: set[str] = set()
        deleted_files = 0
        target_sync_plans = {
            target: self._build_target_sync_plan(job, target, current)
            for target in job.targets
        }
        changed_paths = {
            relative_path
            for relative_paths in target_sync_plans.values()
            for relative_path in relative_paths
        }

        logger.info(
            "job '%s': 변경 파일 %d개, 삭제 파일 %d개, target %d개",
            job.name,
            len(changed_paths),
            self._count_delete_candidates(job, current),
            len(job.targets),
        )

        for target in job.targets:
            copied_now = self._sync_target(
                job,
                target,
                target_sync_plans[target],
                current,
                deferred_paths,
            )
            copied_paths.update(copied_now)
            if job.copy_deleted:
                deleted_files += self._delete_target_paths(job, target, current)

        result = JobSyncResult(
            copied_files=len(copied_paths),
            deferred_files=tuple(sorted(deferred_paths)),
            deleted_files=deleted_files,
            targets=len(job.targets),
        )
        if deferred_paths:
            logger.warning(
                "job '%s': source 파일 사용 중으로 %d개 파일을 다음 주기로 보류합니다: %s",
                job.name,
                len(deferred_paths),
                ", ".join(sorted(deferred_paths)),
            )
        logger.info(
            "job '%s': 복사 %d개, 보류 %d개로 완료되었습니다",
            job.name,
            result.copied_files,
            len(result.deferred_files),
            )
        return result

    def _scan_source(self, source: Path) -> dict[str, FileSnapshot]:
        snapshot: dict[str, FileSnapshot] = {}
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            relative = path.relative_to(source).as_posix()
            snapshot[relative] = FileSnapshot(
                relative_path=relative,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                mode=stat.st_mode,
            )
        return snapshot

    def _sync_target(
        self,
        job: JobConfig,
        target: Path,
        changed_paths: list[str],
        current: dict[str, FileSnapshot],
        deferred_paths: set[str],
    ) -> set[str]:
        target.mkdir(parents=True, exist_ok=True)
        copied_paths: set[str] = set()

        for relative_path in changed_paths:
            if relative_path in deferred_paths:
                continue

            source_path = job.source / relative_path
            target_path = target / relative_path
            file_snapshot = current[relative_path]

            if not self._source_is_ready(source_path, file_snapshot):
                deferred_paths.add(relative_path)
                logger.warning("job '%s': 사용 중인 source 파일을 보류합니다: %s", job.name, source_path)
                continue

            try:
                self._copy_file(source_path, target_path, file_snapshot)
            except SourceFileBusyError:
                deferred_paths.add(relative_path)
                logger.warning("job '%s': 사용 중인 source 파일을 보류합니다: %s", job.name, source_path)
                continue
            except OSError as exc:
                raise TargetSyncError(
                    f"job '{job.name}'에서 '{source_path}'를 '{target_path}'로 복사하지 못했습니다: {exc}"
                ) from exc

            logger.info("job '%s': 복사 완료 %s -> %s", job.name, source_path, target_path)
            copied_paths.add(relative_path)

        return copied_paths

    def _delete_target_paths(self, job: JobConfig, target: Path, current: dict[str, FileSnapshot]) -> int:
        deleted_files = 0
        for target_path in (path for path in target.rglob("*") if path.is_file()):
            relative_path = target_path.relative_to(target).as_posix()
            if relative_path in current:
                continue

            try:
                target_path.unlink()
            except OSError as exc:
                raise TargetSyncError(
                    f"job '{job.name}'에서 '{target_path}'를 삭제하지 못했습니다: {exc}"
                ) from exc
            logger.info("job '%s': 삭제 완료 %s", job.name, target_path)
            deleted_files += 1

        self._prune_empty_directories(target)
        return deleted_files

    def _prune_empty_directories(self, root: Path) -> None:
        for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
            try:
                path.rmdir()
            except OSError:
                continue

    def _copy_file(self, source_path: Path, target_path: Path, file_snapshot: FileSnapshot) -> None:
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

            # Metadata is applied from the scanned source snapshot, so failures here
            # are target-side write issues rather than a fresh read from the source.
            self._apply_target_metadata(target_path, file_snapshot)

    def _source_is_ready(self, source_path: Path, expected: FileSnapshot) -> bool:
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

    def _apply_target_metadata(self, target_path: Path, file_snapshot: FileSnapshot) -> None:
        os.chmod(target_path, file_snapshot.mode)
        os.utime(target_path, ns=(file_snapshot.mtime_ns, file_snapshot.mtime_ns))

    def _is_access_related_error(self, exc: OSError) -> bool:
        access_errnos = {
            errno.EACCES,
            errno.EPERM,
            errno.EBUSY,
            getattr(errno, "ETXTBSY", errno.EBUSY),
        }
        if isinstance(exc, PermissionError):
            return True
        if exc.errno in access_errnos:
            return True
        winerror = getattr(exc, "winerror", None)
        return winerror in {5, 32, 33}

    def _build_target_sync_plan(
        self,
        job: JobConfig,
        target: Path,
        current: dict[str, FileSnapshot],
    ) -> list[str]:
        changed_paths: list[str] = []
        for relative_path, source_snapshot in current.items():
            source_path = job.source / relative_path
            target_path = target / relative_path
            if self._target_needs_copy(source_path, target_path, source_snapshot):
                changed_paths.append(relative_path)
        return changed_paths

    def _target_needs_copy(
        self,
        source_path: Path,
        target_path: Path,
        source_snapshot: FileSnapshot,
    ) -> bool:
        try:
            stat = target_path.stat()
        except FileNotFoundError:
            return True
        except OSError:
            return True
        if stat.st_size != source_snapshot.size:
            return True
        if stat.st_mtime_ns == source_snapshot.mtime_ns:
            return False
        return self._hash_file(source_path) != self._hash_file(target_path)

    def _count_delete_candidates(self, job: JobConfig, current: dict[str, FileSnapshot]) -> int:
        if not job.copy_deleted:
            return 0

        delete_count = 0
        current_paths = set(current)
        for target in job.targets:
            if not target.exists():
                continue
            for target_path in (path for path in target.rglob("*") if path.is_file()):
                if target_path.relative_to(target).as_posix() not in current_paths:
                    delete_count += 1
        return delete_count

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
