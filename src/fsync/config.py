from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(slots=True, frozen=True)
class JobConfig:
    name: str
    source: Path
    targets: tuple[Path, ...]
    interval_seconds: int = 60
    copy_deleted: bool = False


@dataclass(slots=True, frozen=True)
class AppConfig:
    max_workers: int
    log_level: str
    jobs: tuple[JobConfig, ...]


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as file_obj:
        raw = tomllib.load(file_obj)

    app = raw.get("app", {})
    jobs_raw = raw.get("jobs", [])
    jobs: list[JobConfig] = []

    for index, item in enumerate(jobs_raw, start=1):
        name = item.get("name") or f"job-{index}"
        source = Path(item["source"]).expanduser().resolve()
        targets = tuple(Path(target).expanduser().resolve() for target in item["targets"])
        interval_seconds = int(item.get("interval_seconds", 60))
        copy_deleted = bool(item.get("copy_deleted", False))
        jobs.append(
            JobConfig(
                name=name,
                source=source,
                targets=targets,
                interval_seconds=interval_seconds,
                copy_deleted=copy_deleted,
            )
        )

    if not jobs:
        raise ValueError("설정 파일에는 최소 하나 이상의 [[jobs]] 항목이 있어야 합니다.")

    max_workers = int(app.get("max_workers", max(4, len(jobs) * 2)))
    log_level = str(app.get("log_level", "INFO")).upper()
    return AppConfig(max_workers=max_workers, log_level=log_level, jobs=tuple(jobs))
