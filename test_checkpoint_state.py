import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checkpoint_state import CHECKPOINTS, CheckpointStore


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "checkpoint_state.json"

    def test_exact_checkpoints_and_manual_progress(self):
        self.assertEqual(CHECKPOINTS, (
            (2, 300, 15), (3, 400, 34), (4, 500, 48), (5, 600, 472),
            (6, 700, 472), (7, 900, 4719), (8, 1000, 14157),
            (9, 2000, 94380),
        ))
        store = CheckpointStore(self.path)
        self.assertEqual(store.completed_count, 0)
        self.assertEqual(store.next_checkpoint, CHECKPOINTS[0])
        store.set_reached(3, True)
        self.assertTrue(store.is_reached(3))
        self.assertEqual(store.next_checkpoint, CHECKPOINTS[0])
        store.set_reached(2, True)
        self.assertEqual(store.next_checkpoint, CHECKPOINTS[2])
        self.assertEqual(store.completed_count, 2)
        store.set_reached(3, False)
        self.assertEqual(store.next_checkpoint, CHECKPOINTS[1])
        self.assertEqual(store.reached_numbers, (2,))
        for number, _, _ in CHECKPOINTS:
            store.set_reached(number, True)
        self.assertEqual(store.completed_count, len(CHECKPOINTS))
        self.assertIsNone(store.next_checkpoint)

    def test_marks_reload_and_do_not_use_bot_progress(self):
        other_path = Path(self.directory.name) / "progress_state.json"
        other_path.write_text('{"runs_completed":99}', encoding="utf-8")
        first = CheckpointStore(self.path)
        first.set_reached(2, True)
        first.set_reached(9, True)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"reached": [2, 9]})
        self.assertEqual(CheckpointStore(self.path).reached_numbers, (2, 9))
        self.assertEqual(other_path.read_text(encoding="utf-8"),
                         '{"runs_completed":99}')

    def test_corrupt_or_unknown_state_is_safe(self):
        for content in ('{broken', '[]', '{"reached":"2"}',
                        '{"reached":[true,1,2,2,99,"3"]}'):
            self.path.write_text(content, encoding="utf-8")
            store = CheckpointStore(self.path)
            expected = (2,) if content.startswith('{"reached":[') else ()
            self.assertEqual(store.reached_numbers, expected)
        with self.assertRaises(ValueError):
            store.set_reached(99, True)
        with self.assertRaises(TypeError):
            store.set_reached(2, 1)

    def test_failed_atomic_replace_keeps_old_file_and_memory(self):
        store = CheckpointStore(self.path)
        store.set_reached(2, True)
        original = self.path.read_bytes()
        with patch("checkpoint_state.os.replace", side_effect=OSError("busy")):
            with self.assertRaises(OSError):
                store.set_reached(3, True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(store.reached_numbers, (2,))
        self.assertEqual(list(self.path.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
