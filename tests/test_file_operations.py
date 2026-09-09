import tempfile
import unittest
from pathlib import Path

from nemo_miller_columns import (
    FileOperationError,
    MAX_PREVIEW_BYTES,
    calculate_auto_column_width,
    calculate_width_slot_count,
    copy_path,
    create_folder,
    load_file_preview,
    move_path,
    rename_path,
    resolve_operation_destination,
    resolve_operation_paths,
    trash_path,
)


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
        asset = Path(__file__).resolve().parents[1] / "assets" / "unsupported-preview.jpeg"

        preview = load_file_preview(asset)

        self.assertEqual(preview.kind, "image")


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
