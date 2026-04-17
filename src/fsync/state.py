from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile
from typing import Any


STATE_VERSION = 1


@dataclass(slots=True, frozen=True)
class FileFingerprint:
    exists: bool
    size: int | None
    mtime_ns: int | None
    sha256: str | None

    def __post_init__(self) -> None:
        if self.exists:
            if self.size is None or self.mtime_ns is None or self.sha256 is None:
                raise ValueError("exists=True인 fingerprint에는 size, mtime_ns, sha256이 모두 필요합니다.")
            return
        if any(value is not None for value in (self.size, self.mtime_ns, self.sha256)):
            raise ValueError("exists=False인 fingerprint에는 size, mtime_ns, sha256을 저장할 수 없습니다.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FileFingerprint:
        return cls(
            exists=bool(raw["exists"]),
            size=_optional_int(raw.get("size")),
            mtime_ns=_optional_int(raw.get("mtime_ns")),
            sha256=_optional_str(raw.get("sha256")),
        )


@dataclass(slots=True, frozen=True)
class BaselineEntry:
    left: FileFingerprint
    right: FileFingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BaselineEntry:
        return cls(
            left=FileFingerprint.from_dict(_require_dict(raw, "left")),
            right=FileFingerprint.from_dict(_require_dict(raw, "right")),
        )


@dataclass(slots=True, frozen=True)
class BidirectionalState:
    job_name: str
    left_root: str
    right_root: str
    last_synced_at: str | None = None
    entries: dict[str, BaselineEntry] = field(default_factory=dict)
    version: int = STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "job_name": self.job_name,
            "left_root": self.left_root,
            "right_root": self.right_root,
            "last_synced_at": self.last_synced_at,
            "entries": {
                relative_path: entry.to_dict()
                for relative_path, entry in sorted(self.entries.items())
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BidirectionalState:
        entries_raw = _require_dict(raw, "entries")
        entries = {
            relative_path: BaselineEntry.from_dict(_require_dict(entries_raw, relative_path))
            for relative_path in sorted(entries_raw)
        }
        version = int(raw["version"])
        if version != STATE_VERSION:
            raise ValueError(f"지원하지 않는 state version입니다: {version}")
        return cls(
            version=version,
            job_name=str(raw["job_name"]),
            left_root=str(raw["left_root"]),
            right_root=str(raw["right_root"]),
            last_synced_at=_optional_str(raw.get("last_synced_at")),
            entries=entries,
        )


def default_state_file_path(job_name: str, state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser().resolve() / f"{job_name}.json"


def load_state(path: str | Path) -> BidirectionalState | None:
    state_path = Path(path).expanduser().resolve()
    if not state_path.exists():
        return None
    with state_path.open("r", encoding="utf-8") as file_obj:
        raw = json.load(file_obj)
    if not isinstance(raw, dict):
        raise ValueError("state 파일 최상위 구조는 object여야 합니다.")
    return BidirectionalState.from_dict(raw)


def save_state(path: str | Path, state: BidirectionalState) -> None:
    state_path = Path(path).expanduser().resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(state.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as file_obj:
            file_obj.write(payload)
            file_obj.flush()
            os.fsync(file_obj.fileno())
            temp_path = Path(file_obj.name)

        os.replace(temp_path, state_path)
        _fsync_directory(state_path.parent)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _require_dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw[key]
    if not isinstance(value, dict):
        raise ValueError(f"state 필드 '{key}'는 object여야 합니다.")
    return value
