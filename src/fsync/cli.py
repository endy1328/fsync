from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from fsync.config import load_config
from fsync.scheduler import Scheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsync",
        description="단방향 파일 동기화 스케줄러",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="도움말을 표시하고 종료합니다")
    parser.add_argument(
        "--config",
        default="config.toml",
        help="TOML 설정 파일 경로입니다. 기본값: config.toml",
    )
    parser.add_argument(
        "command",
        choices=("run", "once"),
        nargs="?",
        default="run",
        help="계속 실행하거나 1회만 동기화를 실행합니다",
    )
    parser._positionals.title = "위치 인자"
    parser._optionals.title = "옵션"
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(threadName)s %(name)s - %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(Path(args.config))
    configure_logging(config.log_level)

    scheduler = Scheduler(config)
    if args.command == "once":
        scheduler.run_once()
        return 0

    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        logging.getLogger("fsync").info("사용자 요청으로 종료합니다.")
        return 0
    except Exception:
        logging.getLogger("fsync").exception("처리되지 않은 오류가 발생했습니다")
        return 1
    finally:
        scheduler.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
