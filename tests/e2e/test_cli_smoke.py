from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fsync.cli import main
from fsync.state import load_state
from tests.e2e.helpers import list_files, read_file, write_file


class CliSmokeTests(unittest.TestCase):
    def test_one_way_once_copies_and_deletes_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            config = root / "config.toml"

            write_file(source / "docs" / "report.txt", "v1")
            config.write_text(
                f"""
[app]
max_workers = 2
log_level = "INFO"

[[jobs]]
name = "backup"
mode = "one_way"
source = "{source}"
targets = ["{target}"]
copy_deleted = true
""".strip(),
                encoding="utf-8",
            )

            self.assertEqual(main(["--config", str(config), "once"]), 0)
            self.assertEqual(read_file(target / "docs" / "report.txt"), "v1")

            (source / "docs" / "report.txt").unlink()
            self.assertEqual(main(["--config", str(config), "once"]), 0)
            self.assertFalse((target / "docs" / "report.txt").exists())

    def test_bidirectional_once_copies_new_file_and_reruns_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            left = root / "left"
            right = root / "right"
            state_dir = root / "state"
            config = root / "config.toml"

            write_file(left / "docs" / "report.txt", "left-v1")
            config.write_text(
                f"""
[app]
max_workers = 2
log_level = "INFO"
state_dir = "{state_dir}"

[[jobs]]
name = "sync"
mode = "bidirectional"
left = "{left}"
right = "{right}"
conflict_policy = "keep_both"
initial_sync = "manual"
""".strip(),
                encoding="utf-8",
            )

            self.assertEqual(main(["--config", str(config), "once"]), 0)
            self.assertEqual(read_file(right / "docs" / "report.txt"), "left-v1")
            first_state = load_state(state_dir / "sync.json")
            self.assertIsNotNone(first_state)

            self.assertEqual(main(["--config", str(config), "once"]), 0)
            second_state = load_state(state_dir / "sync.json")
            self.assertEqual(first_state.entries, second_state.entries)
            self.assertEqual(list_files(right), ["docs/report.txt"])

    def test_bidirectional_manual_conflict_aborts_without_state_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            left = root / "left"
            right = root / "right"
            state_dir = root / "state"
            config = root / "config.toml"

            write_file(left / "report.txt", "base")
            config.write_text(
                f"""
[app]
max_workers = 2
log_level = "INFO"
state_dir = "{state_dir}"

[[jobs]]
name = "sync"
mode = "bidirectional"
left = "{left}"
right = "{right}"
conflict_policy = "manual"
initial_sync = "left_wins"
""".strip(),
                encoding="utf-8",
            )

            self.assertEqual(main(["--config", str(config), "once"]), 0)
            baseline = load_state(state_dir / "sync.json")
            write_file(left / "report.txt", "left-change")
            write_file(right / "report.txt", "right-change")

            with self.assertRaises(RuntimeError):
                main(["--config", str(config), "once"])

            self.assertEqual(read_file(left / "report.txt"), "left-change")
            self.assertEqual(read_file(right / "report.txt"), "right-change")
            self.assertEqual(load_state(state_dir / "sync.json"), baseline)

    def test_bidirectional_keep_both_writes_conflict_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            left = root / "left"
            right = root / "right"
            state_dir = root / "state"
            config = root / "config.toml"

            write_file(left / "docs" / "report.txt", "base")
            config.write_text(
                f"""
[app]
max_workers = 2
log_level = "INFO"
state_dir = "{state_dir}"

[[jobs]]
name = "sync"
mode = "bidirectional"
left = "{left}"
right = "{right}"
conflict_policy = "keep_both"
initial_sync = "left_wins"
""".strip(),
                encoding="utf-8",
            )

            self.assertEqual(main(["--config", str(config), "once"]), 0)
            write_file(left / "docs" / "report.txt", "left-change")
            write_file(right / "docs" / "report.txt", "right-change")

            self.assertEqual(main(["--config", str(config), "once"]), 0)

            left_files = list_files(left)
            right_files = list_files(right)
            self.assertIn("docs/report.txt", left_files)
            self.assertIn("docs/report.txt", right_files)
            self.assertTrue(any(name.startswith("docs/report.conflict-right") for name in left_files))
            self.assertTrue(any(name.startswith("docs/report.conflict-left") for name in right_files))

    def test_bidirectional_state_mismatch_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            left = root / "left"
            right = root / "right"
            other_left = root / "other-left"
            state_dir = root / "state"
            config = root / "config.toml"

            write_file(other_left / "report.txt", "other")
            mismatch_config = root / "mismatch.toml"
            mismatch_config.write_text(
                f"""
[app]
max_workers = 2
log_level = "INFO"
state_dir = "{state_dir}"

[[jobs]]
name = "sync"
mode = "bidirectional"
left = "{other_left}"
right = "{right}"
conflict_policy = "keep_both"
initial_sync = "manual"
""".strip(),
                encoding="utf-8",
            )
            self.assertEqual(main(["--config", str(mismatch_config), "once"]), 0)

            write_file(left / "report.txt", "current")
            config.write_text(
                f"""
[app]
max_workers = 2
log_level = "INFO"
state_dir = "{state_dir}"

[[jobs]]
name = "sync"
mode = "bidirectional"
left = "{left}"
right = "{right}"
conflict_policy = "keep_both"
initial_sync = "manual"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeError):
                main(["--config", str(config), "once"])
            self.assertEqual(read_file(right / "report.txt"), "other")
            self.assertFalse((right / "report.conflict-left.txt").exists())
            self.assertFalse((right / "current.txt").exists())


if __name__ == "__main__":
    unittest.main()
