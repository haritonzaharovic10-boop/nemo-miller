import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navigation_state import NavigationError, NavigationState, SelectionEventGate


def path_chain(path: Path):
    resolved = path.resolve(strict=True)
    root = Path(resolved.anchor)
    chain = [root]
    current = root
    for part in resolved.parts[1:]:
        current = current / part
        chain.append(current)
    return tuple(chain)


class NavigationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory(
            prefix="nemo-miller-navigation-", dir="/tmp"
        )
        self.root = Path(self.temp_directory.name).resolve()

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_initial_path_chain_construction(self):
        target = self.root / "one" / "two"
        target.mkdir(parents=True)

        state = NavigationState(target)

        self.assertEqual(state.visible_directories, path_chain(target))
        self.assertEqual(
            state.cursor_paths,
            state.visible_directories[1:] + (None,),
        )
        self.assertEqual(
            state.active_column_index, len(state.visible_directories) - 1
        )
        self.assertEqual(state.current_directory, target)
        self.assertIsNone(state.current_item)

    def test_navigating_to_a_deeper_directory(self):
        child = self.root / "child"
        child.mkdir()
        state = NavigationState(self.root)
        source_index = state.active_column_index

        self.assertTrue(state.select_item(source_index, child, is_directory=True))

        self.assertEqual(state.current_directory, child)
        self.assertEqual(state.visible_directories[-1], child)
        self.assertEqual(state.active_column_index, source_index)
        self.assertEqual(state.current_item, child)
        self.assertIsNone(state.cursor_paths[-1])

    def test_selecting_sibling_directory_truncates_obsolete_chain(self):
        old_leaf = self.root / "old" / "leaf"
        sibling = self.root / "sibling"
        old_leaf.mkdir(parents=True)
        sibling.mkdir()
        state = NavigationState(old_leaf)
        source_index = state.visible_directories.index(self.root)

        self.assertTrue(
            state.select_item(source_index, sibling, is_directory=True)
        )

        self.assertEqual(state.visible_directories, path_chain(sibling))
        self.assertNotIn(self.root / "old", state.visible_directories)
        self.assertEqual(state.active_column_index, source_index)
        self.assertEqual(state.current_item, sibling)

    def test_selecting_file_keeps_containing_directory_current(self):
        file_path = self.root / "item.txt"
        file_path.write_text("test", encoding="utf-8")
        state = NavigationState(self.root)
        source_index = state.active_column_index

        self.assertTrue(
            state.select_item(source_index, file_path, is_directory=False)
        )

        self.assertEqual(state.current_directory, self.root)
        self.assertEqual(state.visible_directories[-1], self.root)
        self.assertEqual(state.current_item, file_path)
        self.assertEqual(state.active_column_index, source_index)

    def test_active_column_remains_valid_after_truncation(self):
        leaf = self.root / "old" / "leaf"
        leaf.mkdir(parents=True)
        file_path = self.root / "replacement.txt"
        file_path.write_text("test", encoding="utf-8")
        state = NavigationState(leaf)
        source_index = state.visible_directories.index(self.root)

        self.assertTrue(
            state.select_item(source_index, file_path, is_directory=False)
        )

        self.assertEqual(state.visible_directories[-1], self.root)
        self.assertEqual(state.active_column_index, source_index)
        self.assertLess(
            state.active_column_index, len(state.visible_directories)
        )

    def test_current_cursor_belongs_to_active_column(self):
        child = self.root / "child"
        child.mkdir()
        state = NavigationState(self.root)

        self.assertTrue(
            state.select_item(
                state.active_column_index, child, is_directory=True
            )
        )

        active_directory = state.visible_directories[state.active_column_index]
        self.assertEqual(state.current_item.parent, active_directory)
        self.assertEqual(
            state.cursor_paths[state.active_column_index], state.current_item
        )

    def test_whole_path_reconstruction_is_idempotent(self):
        target = self.root / "one" / "two"
        target.mkdir(parents=True)
        state = NavigationState(target)
        initial = state.snapshot

        self.assertTrue(state.navigate_to(target))

        self.assertEqual(state.snapshot, initial)

    def test_repeated_navigation_has_no_duplicate_semantic_columns(self):
        target = self.root / "one" / "two"
        target.mkdir(parents=True)
        state = NavigationState(self.root)

        for _ in range(3):
            self.assertTrue(state.navigate_to(target))

        self.assertEqual(state.visible_directories, path_chain(target))
        self.assertEqual(
            len(state.visible_directories), len(set(state.visible_directories))
        )

    def test_nonexistent_target_does_not_partially_mutate_state(self):
        state = NavigationState(self.root)
        initial = state.snapshot

        self.assertFalse(state.navigate_to(self.root / "missing"))

        self.assertEqual(state.snapshot, initial)
        self.assertIsNotNone(state.last_error)

    def test_regular_file_is_rejected_as_navigation_target(self):
        file_path = self.root / "item.txt"
        file_path.write_text("test", encoding="utf-8")
        state = NavigationState(self.root)
        initial = state.snapshot

        self.assertFalse(state.navigate_to(file_path))

        self.assertEqual(state.snapshot, initial)
        self.assertIn("not a directory", state.last_error)

    def test_resolution_error_does_not_partially_mutate_state(self):
        state = NavigationState(self.root)
        initial = state.snapshot

        with mock.patch.object(Path, "resolve", side_effect=OSError("unavailable")):
            self.assertFalse(state.navigate_to(self.root / "other"))

        self.assertEqual(state.snapshot, initial)
        self.assertIn("Cannot resolve directory", state.last_error)

    def test_item_outside_source_column_is_rejected_atomically(self):
        other = self.root / "other"
        other.mkdir()
        file_path = other / "item.txt"
        file_path.write_text("test", encoding="utf-8")
        state = NavigationState(self.root)
        initial = state.snapshot

        self.assertFalse(
            state.select_item(
                state.active_column_index, file_path, is_directory=False
            )
        )

        self.assertEqual(state.snapshot, initial)
        self.assertIn("does not belong", state.last_error)

    def test_clearing_deepest_cursor_keeps_directory_chain(self):
        deepest = self.root / "deepest"
        deepest.mkdir()
        file_path = deepest / "item.txt"
        file_path.write_text("test", encoding="utf-8")
        state = NavigationState(deepest)
        deepest_index = state.active_column_index
        self.assertTrue(
            state.select_item(deepest_index, file_path, is_directory=False)
        )
        chain_before_clear = state.visible_directories

        self.assertTrue(state.clear_cursor(deepest_index))

        self.assertEqual(state.visible_directories, chain_before_clear)
        self.assertIsNone(state.cursor_paths[deepest_index])
        self.assertEqual(state.active_column_index, deepest_index)
        self.assertEqual(state.current_directory, deepest)
        self.assertIsNone(state.current_item)

    def test_clearing_ancestor_cursor_truncates_right_side(self):
        deepest = self.root / "middle" / "deepest"
        deepest.mkdir(parents=True)
        state = NavigationState(deepest)
        cleared_index = state.visible_directories.index(self.root)

        self.assertTrue(state.clear_cursor(cleared_index))

        self.assertEqual(state.visible_directories, path_chain(self.root))
        self.assertEqual(state.active_column_index, cleared_index)
        self.assertEqual(state.current_directory, self.root)
        self.assertIsNone(state.current_item)
        self.assertIsNone(state.cursor_paths[cleared_index])

    def test_invalid_clear_index_is_atomic(self):
        state = NavigationState(self.root)
        initial = state.snapshot

        for invalid_index in (-1, len(state.visible_directories)):
            with self.subTest(column_index=invalid_index):
                self.assertFalse(state.clear_cursor(invalid_index))
                self.assertEqual(state.snapshot, initial)
                self.assertIn("Invalid column index", state.last_error)

    def test_invalid_initial_target_is_rejected(self):
        with self.assertRaises(NavigationError):
            NavigationState(self.root / "missing")

    def test_programmatic_selection_is_distinct_from_user_selection(self):
        gate = SelectionEventGate()
        self.assertTrue(gate.user_notifications_enabled)

        with gate.programmatic_change():
            self.assertFalse(gate.user_notifications_enabled)

        self.assertTrue(gate.user_notifications_enabled)

    def test_gate_suppresses_programmatic_deselection(self):
        gate = SelectionEventGate()
        forwarded_rows = []

        def forward_if_user(row):
            if gate.user_notifications_enabled:
                forwarded_rows.append(row)

        with gate.programmatic_change():
            forward_if_user(None)

        self.assertEqual(forwarded_rows, [])
        forward_if_user(None)
        self.assertEqual(forwarded_rows, [None])

    def test_programmatic_selection_gate_recovers_after_error(self):
        gate = SelectionEventGate()

        with self.assertRaises(RuntimeError):
            with gate.programmatic_change():
                raise RuntimeError("simulated reconciliation error")

        self.assertTrue(gate.user_notifications_enabled)

    def test_root_path_behavior(self):
        state = NavigationState(Path("/"))

        self.assertEqual(state.visible_directories, (Path("/"),))
        self.assertEqual(state.cursor_paths, (None,))
        self.assertEqual(state.current_directory, Path("/"))
        self.assertEqual(state.active_column_index, 0)
        self.assertIsNone(state.current_item)
        self.assertTrue(state.navigate_to(Path("/")))
        self.assertEqual(state.visible_directories, (Path("/"),))


if __name__ == "__main__":
    unittest.main()
