from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import tomllib


@dataclass(slots=True, frozen=True)
class JobConfig:
    name: str
    source: Path
    targets: tuple[Path, ...]
    interval_seconds: int = 60
    copy_deleted: bool = False
    mode: Literal["one_way"] = "one_way"


@dataclass(slots=True, frozen=True)
class BidirectionalJobConfig:
    name: str
    left: Path
    right: Path
    state_file: Path
    interval_seconds: int = 60
    delete_policy: Literal["tracked"] = "tracked"
    conflict_policy: Literal["keep_both", "manual", "newer_wins"] = "keep_both"
    initial_sync: Literal["manual", "left_wins", "right_wins"] = "manual"
    mode: Literal["bidirectional"] = "bidirectional"


@dataclass(slots=True, frozen=True)
class AppConfig:
    max_workers: int
    log_level: str
    jobs: tuple[JobConfig | BidirectionalJobConfig, ...]
    state_dir: Path = field(default_factory=lambda: Path(".fsync-state"))


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as file_obj:
        raw = tomllib.load(file_obj)

    app = raw.get("app", {})
    jobs_raw = raw.get("jobs", [])
    jobs: list[JobConfig | BidirectionalJobConfig] = []
    state_dir = _resolve_path(app.get("state_dir", ".fsync-state"))

    for index, item in enumerate(jobs_raw, start=1):
        jobs.append(_load_job(index, item, state_dir))

    if not jobs:
        raise ValueError("설정 파일에는 최소 하나 이상의 [[jobs]] 항목이 있어야 합니다.")

    _validate_bidirectional_job_paths(jobs)

    max_workers = int(app.get("max_workers", max(4, len(jobs) * 2)))
    log_level = str(app.get("log_level", "INFO")).upper()
    return AppConfig(max_workers=max_workers, log_level=log_level, jobs=tuple(jobs), state_dir=state_dir)


def _load_job(
    index: int,
    item: dict[str, object],
    state_dir: Path,
) -> JobConfig | BidirectionalJobConfig:
    mode = str(item.get("mode", "one_way")).lower()
    if mode == "one_way":
        return _load_one_way_job(index, item)
    if mode == "bidirectional":
        return _load_bidirectional_job(index, item, state_dir)
    raise ValueError(f"지원하지 않는 job mode입니다: {mode}")


def _load_one_way_job(index: int, item: dict[str, object]) -> JobConfig:
    name = str(item.get("name") or f"job-{index}")
    if "source" not in item:
        raise ValueError(f"one_way job '{name}'에는 source가 필요합니다.")
    targets_raw = item.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ValueError(f"one_way job '{name}'에는 최소 하나 이상의 target이 필요합니다.")

    source = _resolve_path(item["source"])
    targets = tuple(_resolve_path(target) for target in targets_raw)
    if len(set(targets)) != len(targets):
        raise ValueError(f"one_way job '{name}'에 중복 target 경로가 있습니다.")
    if source in targets:
        raise ValueError(f"one_way job '{name}'에서 source와 target은 같을 수 없습니다.")

    return JobConfig(
        name=name,
        source=source,
        targets=targets,
        interval_seconds=int(item.get("interval_seconds", 60)),
        copy_deleted=bool(item.get("copy_deleted", False)),
    )


def _load_bidirectional_job(
    index: int,
    item: dict[str, object],
    state_dir: Path,
) -> BidirectionalJobConfig:
    name = str(item.get("name") or f"job-{index}")
    if "left" not in item or "right" not in item:
        raise ValueError(f"bidirectional job '{name}'에는 left와 right가 필요합니다.")

    left = _resolve_path(item["left"])
    right = _resolve_path(item["right"])
    if left == right:
        raise ValueError(f"bidirectional job '{name}'에서 left와 right는 같을 수 없습니다.")

    delete_policy = str(item.get("delete_policy", "tracked")).lower()
    if delete_policy != "tracked":
        raise ValueError(f"bidirectional job '{name}'의 delete_policy는 tracked만 지원합니다.")

    conflict_policy = str(item.get("conflict_policy", "keep_both")).lower()
    if conflict_policy not in {"keep_both", "manual", "newer_wins"}:
        raise ValueError(f"bidirectional job '{name}'의 conflict_policy가 올바르지 않습니다.")

    initial_sync = str(item.get("initial_sync", "manual")).lower()
    if initial_sync not in {"manual", "left_wins", "right_wins"}:
        raise ValueError(f"bidirectional job '{name}'의 initial_sync가 올바르지 않습니다.")

    state_file_raw = item.get("state_file")
    state_file = _resolve_path(state_file_raw) if state_file_raw else (state_dir / f"{name}.json").resolve()

    return BidirectionalJobConfig(
        name=name,
        left=left,
        right=right,
        state_file=state_file,
        interval_seconds=int(item.get("interval_seconds", 60)),
        delete_policy=delete_policy,
        conflict_policy=conflict_policy,
        initial_sync=initial_sync,
    )


def _validate_bidirectional_job_paths(jobs: list[JobConfig | BidirectionalJobConfig]) -> None:
    used_paths: dict[Path, str] = {}
    for job in jobs:
        if not isinstance(job, BidirectionalJobConfig):
            continue
        for peer_path in (job.left, job.right):
            previous = used_paths.get(peer_path)
            if previous is not None:
                raise ValueError(
                    f"bidirectional job 경로 '{peer_path}'가 '{previous}'와 '{job.name}'에서 중복 사용되었습니다."
                )
            used_paths[peer_path] = job.name


def _resolve_path(raw_path: object) -> Path:
    if not isinstance(raw_path, str):
        raise ValueError(f"경로 값이 올바르지 않습니다: {raw_path!r}")
    return Path(raw_path).expanduser().resolve()
