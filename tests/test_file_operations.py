import os
import tempfile
import threading
import unittest
from pathlib import Path

from nemo_miller_columns import (
    FileOperationError,
    MAX_PREVIEW_BYTES,
    calculate_auto_column_width,
    calculate_directory_size,
    calculate_width_slot_count,
    copy_path,
    create_folder,
    load_file_preview,
    move_path,
    parse_file_uri_list,
    parse_gnome_file_clipboard,
    rename_path,
    resolve_drag_paths,
    resolve_operation_destination,
    resolve_operation_paths,
    resolve_refreshed_active_path,
    resolve_startup_target,
    serialize_gnome_file_clipboard,
    sort_file_items,
    trash_path,
    FileItem,
    SORT_MODIFIED_NEWEST,
    SORT_NAME,
    next_sort_mode,
)


class SystemClipboardFormatTests(unittest.TestCase):
    def test_gnome_file_clipboard_round_trip_preserves_mode_and_order(self):
        paths = (
            Path("/tmp/one file.txt"),
            Path("/tmp/тест.json"),
        )

        payload = serialize_gnome_file_clipboard("cut", paths)
        mode, parsed_paths = parse_gnome_file_clipboard(payload)

        self.assertEqual(mode, "cut")
        self.assertEqual(parsed_paths, paths)

    def test_gnome_payload_has_no_empty_uri_for_nemo_parser(self):
        payload = serialize_gnome_file_clipboard(
            "copy", (Path("/tmp/one.txt"), Path("/tmp/two.txt"))
        )
        lines = payload.decode("utf-8").split("\n")

        self.assertEqual(lines[0], "copy")
        self.assertEqual(len(lines[1:]), 2)
        self.assertNotIn("", lines[1:])
        self.assertFalse(payload.endswith(b"\n"))

    def test_uri_list_ignores_comments_and_blank_lines(self):
        paths = parse_file_uri_list(
            "# copied files\nfile:///tmp/one.txt\n\nfile:///tmp/two.txt\r\n"
        )

        self.assertEqual(paths, (Path("/tmp/one.txt"), Path("/tmp/two.txt")))

    def test_remote_clipboard_uri_is_rejected(self):
        with self.assertRaises(FileOperationError):
            parse_file_uri_list("https://example.com/file.txt")

    def test_invalid_gnome_clipboard_mode_is_rejected(self):
        with self.assertRaises(FileOperationError):
            parse_gnome_file_clipboard(b"link\nfile:///tmp/one.txt\n")

    def test_drag_from_marked_item_exports_all_marks_in_visible_order(self):
        marked = (Path("/tmp/first.txt"), Path("/tmp/second.txt"))

        paths = resolve_drag_paths(marked, marked[1])

        self.assertEqual(paths, marked)

    def test_drag_from_unmarked_item_exports_only_pressed_item(self):
        marked = (Path("/tmp/first.txt"), Path("/tmp/second.txt"))
        pressed = Path("/tmp/third.txt")

        paths = resolve_drag_paths(marked, pressed)

        self.assertEqual(paths, (pressed,))

    def test_drag_without_pressed_item_exports_nothing(self):
        self.assertEqual(resolve_drag_paths((Path("/tmp/first.txt"),), None), ())


class ColumnWidthTests(unittest.TestCase):
    def test_reserved_child_fill_does_not_change_width(self):
        before = calculate_auto_column_width(900, (-1, -1), 3)
        after = calculate_auto_column_width(900, (-1, -1, -1), 3)

        self.assertEqual(before, after)

    def test_child_removal_leaves_reserved_width_unchanged(self):
        with_child = calculate_auto_column_width(900, (-1, -1, -1), 3)
        without_child = calculate_auto_column_width(900, (-1, -1), 3)

        self.assertEqual(with_child, without_child)

    def test_reserved_slots_fit_without_horizontal_overflow(self):
        width = calculate_auto_column_width(300, (-1, -1, -1, -1), 5)

        self.assertLessEqual(width * 5 + 6 * 4, 300)
        self.assertGreater(width, 0)

    def test_old_deep_reservation_shrinks_to_current_columns(self):
        slots = calculate_width_slot_count(5, 3)

        self.assertEqual(slots, 5)

    def test_deepest_working_column_reserves_one_child(self):
        slots = calculate_width_slot_count(5, 4)

        self.assertEqual(slots, 6)


class RefreshActivePathTests(unittest.TestCase):
    def setUp(self):
        self.paths = tuple(
            Path("/tmp") / name
            for name in ("alpha", "bravo", "charlie")
        )

    def test_surviving_active_path_is_preserved(self):
        selected = resolve_refreshed_active_path(
            self.paths, self.paths, self.paths[1]
        )

        self.assertEqual(selected, self.paths[1])

    def test_removed_middle_path_selects_next_at_same_index(self):
        selected = resolve_refreshed_active_path(
            self.paths,
            (self.paths[0], self.paths[2]),
            self.paths[1],
        )

        self.assertEqual(selected, self.paths[2])

    def test_removed_last_path_selects_previous(self):
        selected = resolve_refreshed_active_path(
            self.paths,
            self.paths[:2],
            self.paths[2],
        )

        self.assertEqual(selected, self.paths[1])

    def test_empty_refreshed_column_has_no_active_path(self):
        selected = resolve_refreshed_active_path(
            self.paths, (), self.paths[1]
        )

        self.assertIsNone(selected)

    def test_absent_previous_active_does_not_select_first_item(self):
        selected = resolve_refreshed_active_path(
            self.paths, self.paths, None
        )

        self.assertIsNone(selected)

    def test_renamed_active_path_uses_preferred_replacement(self):
        renamed = Path("/tmp/renamed")
        selected = resolve_refreshed_active_path(
            self.paths,
            (self.paths[0], renamed, self.paths[2]),
            self.paths[1],
            renamed,
        )

        self.assertEqual(selected, renamed)


class StartupTargetTests(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.TemporaryDirectory(
            prefix="nemo-miller-startup-target-", dir="/tmp"
        )
        self.root = Path(self.lab.name).resolve()

    def tearDown(self):
        self.lab.cleanup()

    def test_directory_argument_starts_in_directory(self):
        directory = self.root / "directory"
        directory.mkdir()

        location, selected = resolve_startup_target(directory, self.root)

        self.assertEqual(location, directory)
        self.assertIsNone(selected)

    def test_file_argument_starts_in_parent_and_selects_file(self):
        directory = self.root / "directory"
        directory.mkdir()
        path = directory / "document.txt"
        path.write_text("document", encoding="utf-8")

        location, selected = resolve_startup_target(path, self.root)

        self.assertEqual(location, directory)
        self.assertEqual(selected, path)

    def test_file_uri_is_decoded(self):
        directory = self.root / "folder with spaces"
        directory.mkdir()
        path = directory / "file name.txt"
        path.write_text("document", encoding="utf-8")

        location, selected = resolve_startup_target(path.as_uri(), self.root)

        self.assertEqual(location, directory)
        self.assertEqual(selected, path)

    def test_invalid_argument_falls_back_to_home(self):
        home = self.root / "home"
        home.mkdir()

        location, selected = resolve_startup_target(
            self.root / "missing", home
        )

        self.assertEqual(location, home)
        self.assertIsNone(selected)


class SortingTests(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.TemporaryDirectory(
            prefix="nemo-miller-sort-", dir="/tmp"
        )
        self.root = Path(self.lab.name)
        self.old = self.root / "a-old.txt"
        self.new = self.root / "z-new.txt"
        self.old.write_text("old", encoding="utf-8")
        self.new.write_text("new", encoding="utf-8")
        os.utime(self.old, (1000, 1000))
        os.utime(self.new, (2000, 2000))

    def tearDown(self):
        self.lab.cleanup()

    def test_alphabetical_mode_sorts_names(self):
        items = [FileItem(self.old), FileItem(self.new)]

        ordered = sort_file_items(items, SORT_NAME)

        self.assertEqual([item.name for item in ordered], [
            "a-old.txt", "z-new.txt"
        ])

    def test_recently_modified_mode_places_latest_first(self):
        items = [FileItem(self.old), FileItem(self.new)]

        ordered = sort_file_items(items, SORT_MODIFIED_NEWEST)

        self.assertEqual([item.name for item in ordered], [
            "z-new.txt", "a-old.txt"
        ])

    def test_directories_remain_grouped_before_files(self):
        directory = self.root / "directory"
        directory.mkdir()
        items = [FileItem(self.old), FileItem(directory)]

        ordered = sort_file_items(items, SORT_MODIFIED_NEWEST)

        self.assertTrue(ordered[0].is_dir)

    def test_tab_cycles_alphabetical_then_recently_modified(self):
        self.assertEqual(next_sort_mode(SORT_NAME), SORT_MODIFIED_NEWEST)
        self.assertEqual(next_sort_mode(SORT_MODIFIED_NEWEST), SORT_NAME)


class FilePreviewTests(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.TemporaryDirectory(
            prefix="nemo-miller-preview-", dir="/tmp"
        )
        self.root = Path(self.lab.name)

    def tearDown(self):
        self.lab.cleanup()

    def test_plain_text_preview(self):
        path = self.root / "notes.txt"
        path.write_text("first\nsecond\nthird\n", encoding="utf-8")

        preview = load_file_preview(path)

        self.assertEqual(preview.kind, "text")
        self.assertEqual(preview.text, "first\nsecond\nthird")
        self.assertFalse(preview.truncated)

    def test_text_preview_is_line_bounded(self):
        path = self.root / "many.txt"
        path.write_text(
            "\n".join(f"line-{index}" for index in range(250)),
            encoding="utf-8",
        )

        preview = load_file_preview(path)

        self.assertEqual(preview.kind, "text")
        self.assertEqual(len(preview.text.splitlines()), 200)
        self.assertTrue(preview.truncated)

    def test_text_preview_is_byte_bounded(self):
        path = self.root / "large.txt"
        path.write_bytes(b"a" * (MAX_PREVIEW_BYTES + 32))

        preview = load_file_preview(path)

        self.assertEqual(preview.kind, "text")
        self.assertLessEqual(len(preview.text.encode("utf-8")), MAX_PREVIEW_BYTES)
        self.assertTrue(preview.truncated)

    def test_binary_file_is_unsupported(self):
        path = self.root / "payload.bin"
        path.write_bytes(b"\x00\x01\x02")

        preview = load_file_preview(path)

        self.assertEqual(preview.kind, "unsupported")

    def test_image_is_classified_for_scaled_rendering(self):
        asset = (
            Path(__file__).resolve().parents[1] /
            "assets" / "unsupported-preview-original.png"
        )

        preview = load_file_preview(asset)

        self.assertEqual(preview.kind, "image")


class DirectorySizeTests(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.TemporaryDirectory(
            prefix="nemo-miller-directory-size-", dir="/tmp"
        )
        self.root = Path(self.lab.name)

    def tearDown(self):
        self.lab.cleanup()

    def test_recursive_size_and_item_counts(self):
        (self.root / "first.txt").write_bytes(b"first")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "second.txt").write_bytes(b"second!")

        result = calculate_directory_size(self.root)

        self.assertEqual(result.size, 12)
        self.assertEqual(result.file_count, 2)
        self.assertEqual(result.directory_count, 1)
        self.assertEqual(result.error_count, 0)
        self.assertFalse(result.cancelled)

    def test_directory_symlink_is_counted_without_following_target(self):
        scanned = self.root / "scanned"
        scanned.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "large.bin").write_bytes(b"x" * 4096)
        link = scanned / "outside-link"
        link.symlink_to(outside, target_is_directory=True)

        result = calculate_directory_size(scanned)

        self.assertEqual(result.size, link.lstat().st_size)
        self.assertEqual(result.file_count, 1)
        self.assertEqual(result.directory_count, 0)
        self.assertEqual(result.error_count, 0)

    def test_pre_cancelled_scan_stops_without_work(self):
        cancel_event = threading.Event()
        cancel_event.set()

        result = calculate_directory_size(self.root, cancel_event)

        self.assertTrue(result.cancelled)
        self.assertEqual(result.size, 0)
        self.assertEqual(result.file_count, 0)
        self.assertEqual(result.directory_count, 0)

    def test_missing_directory_is_reported_as_partial(self):
        result = calculate_directory_size(self.root / "missing")

        self.assertEqual(result.size, 0)
        self.assertEqual(result.error_count, 1)
        self.assertFalse(result.cancelled)


class FileOperationLabTests(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.TemporaryDirectory(
            prefix="nemo-miller-fileops-", dir="/tmp"
        )
        self.root = Path(self.lab.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        self.destination.mkdir()

    def tearDown(self):
        self.lab.cleanup()

    def test_active_item_fallback_and_marked_order(self):
        active = self.source / "active.txt"
        first = self.source / "first.txt"
        second = self.source / "second.txt"

        self.assertEqual(resolve_operation_paths((), active), (active,))
        self.assertEqual(
            resolve_operation_paths((second, first), active),
            (second, first),
        )
        self.assertEqual(resolve_operation_paths((), None), ())

    def test_active_directory_is_operation_destination(self):
        active_directory = self.source / "active-directory"

        destination = resolve_operation_destination(
            self.source, active_directory, True
        )

        self.assertEqual(destination, active_directory)

    def test_active_file_keeps_column_as_operation_destination(self):
        active_file = self.source / "active.txt"

        destination = resolve_operation_destination(
            self.source, active_file, False
        )

        self.assertEqual(destination, self.source)

    def test_no_active_item_keeps_column_as_operation_destination(self):
        destination = resolve_operation_destination(
            self.source, None, False
        )

        self.assertEqual(destination, self.source)

    def test_copy_one_file(self):
        source = self.source / "one.txt"
        source.write_text("one", encoding="utf-8")

        copied = copy_path(source, self.destination)

        self.assertEqual(copied.read_text(encoding="utf-8"), "one")
        self.assertTrue(source.exists())

    def test_copy_multiple_marked_files(self):
        sources = []
        for name in ("one.txt", "two.txt"):
            source = self.source / name
            source.write_text(name, encoding="utf-8")
            sources.append(source)

        for source in resolve_operation_paths(sources, None):
            copy_path(source, self.destination)

        self.assertEqual(
            sorted(path.name for path in self.destination.iterdir()),
            ["one.txt", "two.txt"],
        )

    def test_copy_directory_recursively_and_preserve_symlink(self):
        directory = self.source / "tree"
        directory.mkdir()
        (directory / "nested.txt").write_text("nested", encoding="utf-8")
        (directory / "link.txt").symlink_to("nested.txt")

        copied = copy_path(directory, self.destination)

        self.assertEqual(
            (copied / "nested.txt").read_text(encoding="utf-8"), "nested"
        )
        self.assertTrue((copied / "link.txt").is_symlink())
        self.assertEqual((copied / "link.txt").readlink(), Path("nested.txt"))

    def test_copy_into_descendant_is_rejected(self):
        directory = self.source / "tree"
        descendant = directory / "inside"
        descendant.mkdir(parents=True)

        with self.assertRaises(FileOperationError):
            copy_path(directory, descendant)

    def test_destination_collision_is_rejected(self):
        source = self.source / "same.txt"
        source.write_text("source", encoding="utf-8")
        existing = self.destination / source.name
        existing.write_text("existing", encoding="utf-8")

        with self.assertRaises(FileOperationError):
            copy_path(source, self.destination)

        self.assertEqual(existing.read_text(encoding="utf-8"), "existing")

    def test_move_file(self):
        source = self.source / "move.txt"
        source.write_text("move", encoding="utf-8")

        moved = move_path(source, self.destination)

        self.assertFalse(source.exists())
        self.assertEqual(moved.read_text(encoding="utf-8"), "move")

    def test_move_collision_is_rejected_without_source_loss(self):
        source = self.source / "same.txt"
        source.write_text("source", encoding="utf-8")
        existing = self.destination / source.name
        existing.write_text("existing", encoding="utf-8")

        with self.assertRaises(FileOperationError):
            move_path(source, self.destination)

        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assertEqual(existing.read_text(encoding="utf-8"), "existing")

    def test_move_directory(self):
        source = self.source / "move-tree"
        source.mkdir()
        (source / "nested.txt").write_text("nested", encoding="utf-8")

        moved = move_path(source, self.destination)

        self.assertFalse(source.exists())
        self.assertEqual(
            (moved / "nested.txt").read_text(encoding="utf-8"), "nested"
        )

    def test_rename_file(self):
        source = self.source / "old.txt"
        source.write_text("rename", encoding="utf-8")

        renamed = rename_path(source, "new.txt")

        self.assertFalse(source.exists())
        self.assertEqual(renamed.read_text(encoding="utf-8"), "rename")

    def test_rename_collision_is_rejected(self):
        source = self.source / "old.txt"
        source.write_text("old", encoding="utf-8")
        existing = self.source / "existing.txt"
        existing.write_text("existing", encoding="utf-8")

        with self.assertRaises(FileOperationError):
            rename_path(source, existing.name)

        self.assertTrue(source.exists())
        self.assertEqual(existing.read_text(encoding="utf-8"), "existing")

    def test_invalid_rename_names_are_rejected(self):
        source = self.source / "old.txt"
        source.write_text("old", encoding="utf-8")

        for name in ("", ".", "..", "nested/name"):
            with self.subTest(name=name):
                with self.assertRaises(FileOperationError):
                    rename_path(source, name)
                self.assertTrue(source.exists())

    def test_create_folder(self):
        created = create_folder(self.destination, "New Folder")

        self.assertTrue(created.is_dir())
        with self.assertRaises(FileOperationError):
            create_folder(self.destination, "New Folder")

        for name in ("", ".", "..", "nested/name"):
            with self.subTest(name=name):
                with self.assertRaises(FileOperationError):
                    create_folder(self.destination, name)

    def test_trash_file_without_permanent_delete_fallback(self):
        source = self.source / "trash-me.txt"
        source.write_text("trash", encoding="utf-8")

        try:
            trash_path(source)
        except FileOperationError:
            # Some /tmp mounts do not support freedesktop trash metadata. The
            # required safe behavior is to leave the source untouched.
            self.assertTrue(source.exists())
        else:
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
