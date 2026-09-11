#!/usr/bin/env python3
"""
Nemo Miller Columns - A Miller Columns file viewer
Inspired by macOS Finder
"""

import sys
import os
import gi
import subprocess
import mimetypes
import shutil
import urllib.parse
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Generator, Optional

gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, Pango


CLIPBOARD_COPY = "copy"
CLIPBOARD_CUT = "cut"
RESIZE_HANDLE_WIDTH = 6
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_LINES = 200
UNSUPPORTED_PREVIEW_IMAGE = (
    Path(__file__).resolve().parent /
    "assets" / "unsupported-preview-original.png"
)
GNOME_COPIED_FILES_NAME = "x-special/gnome-copied-files"
URI_LIST_NAME = "text/uri-list"
GNOME_COPIED_FILES_INFO = 1
URI_LIST_INFO = 2


class FileOperationError(Exception):
    """A user-facing file operation failure."""


def path_exists(path):
    path = Path(path)
    return path.exists() or path.is_symlink()


def resolve_operation_paths(marked_paths, active_path):
    """Use ordered marked paths, otherwise one active path if available."""
    marked = tuple(Path(path) for path in marked_paths)
    if marked:
        return marked
    if active_path is None:
        return ()
    return (Path(active_path),)


def resolve_drag_paths(ordered_marked_paths, pressed_path):
    """Drag the complete marked set only when the gesture starts on it."""
    marked = tuple(Path(path) for path in ordered_marked_paths)
    if pressed_path is None:
        return ()
    pressed_path = Path(pressed_path)
    if pressed_path in marked:
        return marked
    return (pressed_path,)


def resolve_refreshed_active_path(previous_paths, refreshed_paths,
                                  previous_active_path,
                                  preferred_active_path=None):
    """Preserve ACTIVE or choose its next/previous neighbor after refresh."""
    previous_paths = tuple(Path(path) for path in previous_paths)
    refreshed_paths = tuple(Path(path) for path in refreshed_paths)
    previous_active_path = (
        Path(previous_active_path)
        if previous_active_path is not None else None
    )
    preferred_active_path = (
        Path(preferred_active_path)
        if preferred_active_path is not None else previous_active_path
    )

    if preferred_active_path in refreshed_paths:
        return preferred_active_path
    if previous_active_path is None or not refreshed_paths:
        return None
    try:
        previous_index = previous_paths.index(previous_active_path)
    except ValueError:
        return None
    return refreshed_paths[min(previous_index, len(refreshed_paths) - 1)]


def serialize_gnome_file_clipboard(mode, paths):
    """Serialize paths using the GNOME/Nemo copied-files convention."""
    if mode not in (CLIPBOARD_COPY, CLIPBOARD_CUT):
        raise ValueError(f"Unsupported clipboard mode: {mode}")
    lines = [mode]
    lines.extend(Path(path).resolve().as_uri() for path in paths)
    # Nemo splits every line after mode into a URI, including a trailing empty
    # line. Never append a final newline: it would become an invalid "" path.
    return "\n".join(lines).encode("utf-8")


def parse_file_uri_list(text):
    """Parse local file URIs from clipboard text in visible order."""
    paths = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        uri = raw_line.strip()
        if not uri or uri.startswith("#"):
            continue
        parsed = urllib.parse.urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise FileOperationError(f"Unsupported clipboard URI: {uri}")
        paths.append(Path(urllib.parse.unquote(parsed.path)))
    return tuple(paths)


def parse_gnome_file_clipboard(data):
    """Return mode and ordered local paths from GNOME copied-files data."""
    if isinstance(data, bytes):
        text = data.rstrip(b"\x00").decode("utf-8", errors="strict")
    else:
        text = str(data)
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() not in (CLIPBOARD_COPY, CLIPBOARD_CUT):
        raise FileOperationError("Clipboard does not contain a valid file operation")
    mode = lines[0].strip()
    paths = parse_file_uri_list("\n".join(lines[1:]))
    if not paths:
        raise FileOperationError("Clipboard file list is empty")
    return mode, paths


def resolve_operation_destination(column_path, active_path, active_is_directory):
    """Use an active directory as destination, otherwise the column path."""
    if active_path is not None and active_is_directory:
        return Path(active_path)
    return Path(column_path)


def calculate_auto_column_width(total_width, column_widths, slot_count):
    """Calculate stable auto width while reserving virtual child slots."""
    slot_count = max(slot_count, len(column_widths), 1)
    virtual_handle_width = RESIZE_HANDLE_WIDTH * (slot_count - 1)
    available_width = max(1, total_width - virtual_handle_width)
    fixed_width = sum(width for width in column_widths if width != -1)
    empty_slot_count = slot_count - len(column_widths)
    auto_slot_count = (
        sum(1 for width in column_widths if width == -1) +
        empty_slot_count
    )
    if auto_slot_count == 0:
        return 0
    return max(1, (available_width - fixed_width) // auto_slot_count)


def calculate_width_slot_count(column_count, reserved_child_column_index):
    """Keep one child slot for the working column without retaining old depth."""
    if column_count <= 0:
        return 1
    working_index = min(
        max(reserved_child_column_index, 0), column_count - 1
    )
    return max(column_count, working_index + 2)


def validate_name(name):
    """Validate one basename used by rename/new-folder operations."""
    if not name or name in ('.', '..') or '/' in name:
        raise FileOperationError(
            "Name must be non-empty, not '.' or '..', and contain no '/'"
        )
    return name


def _require_source(source):
    source = Path(source)
    if not path_exists(source):
        raise FileOperationError(f"Source does not exist: {source}")
    return source


def _require_destination_directory(destination_directory):
    destination_directory = Path(destination_directory)
    if not destination_directory.is_dir():
        raise FileOperationError(
            f"Destination is not a directory: {destination_directory}"
        )
    return destination_directory


def _available_destination(source, destination_directory):
    destination = destination_directory / source.name
    if path_exists(destination):
        raise FileOperationError(f"Destination already exists: {destination}")
    return destination


def _reject_recursive_destination(source, destination):
    if source.is_symlink() or not source.is_dir():
        return
    try:
        source_resolved = source.resolve(strict=True)
        destination_resolved = destination.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise FileOperationError(f"Cannot validate destination: {error}") from error
    if (destination_resolved == source_resolved or
            source_resolved in destination_resolved.parents):
        raise FileOperationError(
            f"Cannot copy or move a directory into itself: {source}"
        )


def copy_path(source, destination_directory):
    """Copy one path without overwriting and return its destination."""
    source = _require_source(source)
    destination_directory = _require_destination_directory(destination_directory)
    destination = _available_destination(source, destination_directory)
    _reject_recursive_destination(source, destination)

    try:
        if source.is_symlink():
            os.symlink(os.readlink(source), destination)
        elif source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination, follow_symlinks=False)
    except (OSError, shutil.Error) as error:
        raise FileOperationError(str(error)) from error
    return destination


def move_path(source, destination_directory):
    """Move one path without overwriting and return its destination."""
    source = _require_source(source)
    destination_directory = _require_destination_directory(destination_directory)
    destination = _available_destination(source, destination_directory)
    _reject_recursive_destination(source, destination)

    try:
        shutil.move(str(source), str(destination))
    except (OSError, shutil.Error) as error:
        raise FileOperationError(str(error)) from error
    return destination


def rename_path(source, new_name):
    """Rename one path within its parent without overwriting."""
    source = _require_source(source)
    validate_name(new_name)
    destination = source.parent / new_name
    if destination == source:
        return source
    if path_exists(destination):
        raise FileOperationError(f"Destination already exists: {destination}")
    try:
        source.rename(destination)
    except OSError as error:
        raise FileOperationError(str(error)) from error
    return destination


def create_folder(destination_directory, name):
    """Create one folder without overwriting and return its path."""
    destination_directory = _require_destination_directory(destination_directory)
    validate_name(name)
    destination = destination_directory / name
    if path_exists(destination):
        raise FileOperationError(f"Destination already exists: {destination}")
    try:
        destination.mkdir()
    except OSError as error:
        raise FileOperationError(str(error)) from error
    return destination


def trash_path(path):
    """Move one path to the desktop trash; never permanently delete it."""
    path = _require_source(path)
    try:
        trashed = Gio.File.new_for_path(str(path)).trash(None)
    except Exception as error:
        raise FileOperationError(str(error)) from error
    if not trashed:
        raise FileOperationError(f"Trash operation was not supported for: {path}")


@dataclass(frozen=True)
class FilePreviewData:
    kind: str
    text: str = ""
    truncated: bool = False
    message: str = ""


@dataclass(frozen=True)
class DirectorySizeData:
    """Result of one cancellable recursive directory scan."""

    size: int
    file_count: int
    directory_count: int
    error_count: int
    cancelled: bool = False


def calculate_directory_size(path, cancel_event=None):
    """Calculate apparent content size without following directory symlinks."""
    path = Path(path)
    size = 0
    file_count = 0
    directory_count = 0
    error_count = 0

    if cancel_event is not None and cancel_event.is_set():
        return DirectorySizeData(0, 0, 0, 0, cancelled=True)

    if path.is_symlink():
        try:
            size = path.lstat().st_size
        except OSError:
            error_count = 1
        return DirectorySizeData(size, 1, 0, error_count)

    pending_directories = [path]
    while pending_directories:
        if cancel_event is not None and cancel_event.is_set():
            return DirectorySizeData(
                size, file_count, directory_count, error_count,
                cancelled=True,
            )

        directory = pending_directories.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            error_count += 1
            continue

        with entries:
            for entry in entries:
                if cancel_event is not None and cancel_event.is_set():
                    return DirectorySizeData(
                        size, file_count, directory_count, error_count,
                        cancelled=True,
                    )
                try:
                    if entry.is_symlink():
                        size += entry.stat(follow_symlinks=False).st_size
                        file_count += 1
                    elif entry.is_dir(follow_symlinks=False):
                        directory_count += 1
                        pending_directories.append(Path(entry.path))
                    else:
                        size += entry.stat(follow_symlinks=False).st_size
                        file_count += 1
                except OSError:
                    error_count += 1

    return DirectorySizeData(
        size, file_count, directory_count, error_count
    )


def load_file_preview(path):
    """Load bounded plain text metadata or classify an image/unsupported file."""
    path = Path(path)
    mime_type, _encoding = mimetypes.guess_type(str(path))
    if mime_type and mime_type.startswith("image/"):
        return FilePreviewData(kind="image")

    text_mime = (
        mime_type is None or
        mime_type.startswith("text/") or
        mime_type in {
            "application/json",
            "application/xml",
            "application/javascript",
            "application/x-python",
            "application/x-sh",
            "application/x-perl",
        }
    )
    if not text_mime:
        return FilePreviewData(
            kind="unsupported", message=mime_type or "Unsupported file"
        )

    try:
        with open(path, "rb") as file_handle:
            content = file_handle.read(MAX_PREVIEW_BYTES + 1)
    except OSError as error:
        return FilePreviewData(kind="unsupported", message=str(error))

    if b"\x00" in content:
        return FilePreviewData(kind="unsupported", message="Binary file")

    byte_truncated = len(content) > MAX_PREVIEW_BYTES
    content = content[:MAX_PREVIEW_BYTES]
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    line_truncated = len(lines) > MAX_PREVIEW_LINES
    visible_text = "\n".join(lines[:MAX_PREVIEW_LINES])
    return FilePreviewData(
        kind="text",
        text=visible_text,
        truncated=byte_truncated or line_truncated,
    )


class FileItem:
    """Represents a file or directory"""

    def __init__(self, path):
        self.path = Path(path)
        self.name = self.path.name or str(self.path)
        self.is_dir = self.path.is_dir()
        self.is_symlink = self.path.is_symlink()

    def get_icon(self, icon_theme, size=24):
        """Gets the appropriate icon for the file"""
        try:
            if self.is_dir:
                icon_name = "folder"
            else:
                mime_type, _ = mimetypes.guess_type(str(self.path))
                if mime_type:
                    # Convert MIME type to icon name
                    icon_name = mime_type.replace('/', '-')
                    if not icon_theme.has_icon(icon_name):
                        # Try with generic category
                        icon_name = mime_type.split('/')[0] + "-x-generic"
                        if not icon_theme.has_icon(icon_name):
                            icon_name = "text-x-generic"
                else:
                    icon_name = "text-x-generic"

            if icon_theme.has_icon(icon_name):
                return icon_theme.load_icon(icon_name, size, Gtk.IconLookupFlags.FORCE_SIZE)
            else:
                return icon_theme.load_icon("text-x-generic", size, Gtk.IconLookupFlags.FORCE_SIZE)
        except Exception:
            # Fallback to generic icon
            try:
                return icon_theme.load_icon("text-x-generic", size, Gtk.IconLookupFlags.FORCE_SIZE)
            except Exception:
                return None


class ColumnView(Gtk.Box):
    """A single column in the Miller view"""

    MIN_WIDTH = 100

    def __init__(self, path, on_item_selected, on_item_activated,
                 on_files_dropped, on_drag_finished):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.path = Path(path)
        self.on_item_selected = on_item_selected
        self.on_item_activated = on_item_activated
        self.on_files_dropped = on_files_dropped
        self.on_drag_finished = on_drag_finished
        self.icon_theme = Gtk.IconTheme.get_default()
        self.active_item_path = None
        self.marked_paths = set()
        self.mark_anchor_path = None
        self._extend_marks_on_next_selection = False
        self._suppress_navigation_callback = False
        self._pressed_path = None
        self._pending_plain_click_path = None
        self._drag_paths = ()

        # Set minimum width
        self.set_hexpand(False)
        self.set_size_request(self.MIN_WIDTH, -1)

        # ScrolledWindow for the list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)

        # ListBox for items
        self.listbox = Gtk.ListBox()
        self.listbox.set_can_focus(True)
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(False)
        self.listbox.connect("row-selected", self._on_row_selected)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("button-press-event", self._on_button_press)
        self.listbox.connect("button-release-event", self._on_button_release)
        self.listbox.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [],
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE,
        )
        self.listbox.drag_source_add_uri_targets()
        self.listbox.connect("drag-begin", self._on_drag_begin)
        self.listbox.connect("drag-data-get", self._on_drag_data_get)
        self.listbox.connect("drag-end", self._on_drag_end)
        self.listbox.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [],
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE,
        )
        self.listbox.drag_dest_add_uri_targets()
        self.listbox.connect("drag-data-received", self._on_drag_data_received)
        self.listbox.get_style_context().add_class("miller-column")

        scroll.add(self.listbox)
        self.pack_start(scroll, True, True, 0)

        self.populate()

    def populate(self):
        """Populates the column with directory contents"""
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        try:
            items = []
            for entry in self.path.iterdir():
                try:
                    # Skip hidden files
                    if not entry.name.startswith('.'):
                        items.append(FileItem(entry))
                except PermissionError:
                    continue

            # Sort: directories first, then files, alphabetically
            items.sort(key=lambda x: (not x.is_dir, x.name.lower()))

            for item in items:
                row = self._create_row(item)
                self.listbox.add(row)

        except PermissionError:
            label = Gtk.Label(label="Permission denied")
            label.set_margin_top(20)
            label.set_margin_bottom(20)
            self.listbox.add(label)
        except Exception as e:
            label = Gtk.Label(label=f"Error: {str(e)}")
            label.set_margin_top(20)
            self.listbox.add(label)

        self.listbox.show_all()

    def _create_row(self, item):
        """Creates a row for an item"""
        row = Gtk.ListBoxRow()
        row.item = item

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_start(8)
        hbox.set_margin_end(8)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)

        # Icon
        icon = item.get_icon(self.icon_theme, 24)
        if icon:
            image = Gtk.Image.new_from_pixbuf(icon)
        else:
            image = Gtk.Image.new_from_icon_name("text-x-generic", Gtk.IconSize.LARGE_TOOLBAR)
        hbox.pack_start(image, False, False, 0)

        # File name
        label = Gtk.Label(label=item.name)
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_hexpand(True)
        hbox.pack_start(label, True, True, 0)

        # Arrow for directories
        if item.is_dir:
            arrow = Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.MENU)
            arrow.set_opacity(0.5)
            hbox.pack_end(arrow, False, False, 0)

        row.add(hbox)
        return row

    def _on_row_selected(self, listbox, row):
        """Handles row selection"""
        if row and hasattr(row, 'item'):
            self.active_item_path = row.item.path
            if self._extend_marks_on_next_selection:
                self._mark_range_to(row.item.path)
            if not self._suppress_navigation_callback:
                self.on_item_selected(self, row.item)
        elif row is None:
            self.active_item_path = None

    def _on_row_activated(self, listbox, row):
        """Handles row activation (double-click)"""
        if row and hasattr(row, 'item'):
            self.on_item_activated(row.item)

    def select_path(self, path, notify_navigation=True):
        """Select a path, optionally as visual-only path reconstruction."""
        path = Path(path)
        for row in self.listbox.get_children():
            if hasattr(row, 'item') and row.item.path == path:
                old_suppression = self._suppress_navigation_callback
                if not notify_navigation:
                    self._suppress_navigation_callback = True
                try:
                    self.listbox.select_row(row)
                finally:
                    self._suppress_navigation_callback = old_suppression
                return True
        return False

    def get_active_item(self):
        """Returns the single row that drives navigation and preview."""
        row = self.listbox.get_selected_row()
        if row is not None and hasattr(row, 'item'):
            return row.item
        return None

    def grab_navigation_focus(self, select_first=False):
        """Focus ACTIVE, optionally establishing the first row as ACTIVE."""
        row = self.listbox.get_selected_row()
        if row is None and select_first:
            row = self.listbox.get_row_at_index(0)
            if row is not None:
                # This changes ACTIVE only; MARKED paths remain independent.
                self.listbox.select_row(row)
        if row is not None:
            row.grab_focus()
        else:
            self.listbox.grab_focus()

    def get_operation_destination(self):
        """Resolve paste/new-folder destination from this column's ACTIVE."""
        active_item = self.get_active_item()
        return resolve_operation_destination(
            self.path,
            active_item.path if active_item is not None else None,
            active_item.is_dir if active_item is not None else False,
        )

    def _on_button_press(self, listbox, event):
        """Updates marks for a mouse gesture while GTK controls the active row."""
        self._pressed_path = None
        self._pending_plain_click_path = None
        if event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if event.state & (
                Gdk.ModifierType.MOD1_MASK |
                Gdk.ModifierType.MOD4_MASK |
                Gdk.ModifierType.SUPER_MASK):
            return False

        row = self.listbox.get_row_at_y(int(event.y))
        if row is None or not hasattr(row, 'item'):
            return False

        path = row.item.path
        self._pressed_path = path
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            if self.mark_anchor_path is None:
                self.mark_anchor_path = self.active_item_path or path
            self._mark_range_to(path)
        elif event.state & Gdk.ModifierType.CONTROL_MASK:
            self._toggle_mark(path)
        else:
            if path in self.marked_paths:
                # Keep the full marked set long enough for GTK's drag
                # threshold to be crossed. A click without a drag collapses
                # it normally in _on_button_release().
                self._pending_plain_click_path = path
            else:
                self._apply_plain_click_marks(row.item)

        # SINGLE mode may natively deselect a Ctrl-clicked active row. Restore
        # that clicked row after GTK processes the event so it remains active.
        if event.state & (
                Gdk.ModifierType.CONTROL_MASK |
                Gdk.ModifierType.SHIFT_MASK):
            GLib.idle_add(self._ensure_mouse_active, row)
        return False

    def _on_button_release(self, listbox, event):
        """Complete a plain click only if it did not become a drag."""
        if event.button != 1:
            return False
        pending_path = self._pending_plain_click_path
        self._pending_plain_click_path = None
        self._pressed_path = None
        if pending_path is None:
            return False
        row = self._row_for_path(pending_path)
        if row is not None:
            self._apply_plain_click_marks(row.item)
        return False

    def _apply_plain_click_marks(self, item):
        """Apply the existing plain-click operation-selection semantics."""
        self.marked_paths = set() if item.is_dir else {item.path}
        self.mark_anchor_path = item.path
        self._refresh_mark_styles()

    def _row_for_path(self, path):
        path = Path(path)
        for row in self.listbox.get_children():
            if hasattr(row, 'item') and row.item.path == path:
                return row
        return None

    def _on_drag_begin(self, listbox, context):
        """Snapshot drag targets before mouse release can alter marks."""
        self._drag_paths = resolve_drag_paths(
            (item.path for item in self.get_marked_items()),
            self._pressed_path,
        )
        self._pending_plain_click_path = None
        if self._drag_paths:
            Gtk.drag_set_icon_name(context, "text-x-generic", 0, 0)

    def _on_drag_data_get(self, listbox, context, selection_data, info, time_):
        """Publish every dragged local path using the standard URI target."""
        if self._drag_paths:
            selection_data.set_uris([
                path.resolve().as_uri() for path in self._drag_paths
            ])

    def _on_drag_end(self, listbox, context):
        drag_paths = self._drag_paths
        action = context.get_selected_action()
        self._pressed_path = None
        self._pending_plain_click_path = None
        self._drag_paths = ()
        if drag_paths and action & Gdk.DragAction.MOVE:
            self.on_drag_finished(drag_paths)

    def _on_drag_data_received(self, listbox, context, x, y,
                               selection_data, info, time_):
        """Resolve the drop row and delegate filesystem semantics upward."""
        row = self.listbox.get_row_at_y(int(y))
        item = row.item if row is not None and hasattr(row, 'item') else None
        destination_directory = resolve_operation_destination(
            self.path,
            item.path if item is not None else None,
            item.is_dir if item is not None else False,
        )
        self.on_files_dropped(
            selection_data.get_uris() or (),
            destination_directory,
            context.get_selected_action(),
            context,
            time_,
        )

    def _ensure_mouse_active(self, row):
        """Keeps a modifier-clicked row active without opening it."""
        if row.get_parent() is self.listbox:
            self.listbox.select_row(row)
        return False

    def _ordered_item_paths(self):
        return [
            row.item.path for row in self.listbox.get_children()
            if hasattr(row, 'item')
        ]

    def _toggle_mark(self, path):
        if path in self.marked_paths:
            self.marked_paths.remove(path)
        else:
            self.marked_paths.add(path)
        if self.mark_anchor_path is None:
            self.mark_anchor_path = path
        self._refresh_mark_styles()

    def _mark_range_to(self, path):
        ordered_paths = self._ordered_item_paths()
        try:
            anchor_index = ordered_paths.index(self.mark_anchor_path)
            path_index = ordered_paths.index(path)
        except ValueError:
            self.mark_anchor_path = path
            self.marked_paths = {path}
        else:
            start, end = sorted((anchor_index, path_index))
            self.marked_paths = set(ordered_paths[start:end + 1])
        self._refresh_mark_styles()

    def _refresh_mark_styles(self):
        for row in self.listbox.get_children():
            if not hasattr(row, 'item'):
                continue
            style = row.get_style_context()
            if row.item.path in self.marked_paths:
                style.add_class("marked-item")
            else:
                style.remove_class("marked-item")

    def prepare_keyboard_range_extension(self):
        """Lets native Shift+Up/Down move active, then marks its range."""
        if self.mark_anchor_path is None:
            self.mark_anchor_path = self.active_item_path
        self._extend_marks_on_next_selection = True
        GLib.idle_add(self._finish_keyboard_range_extension)

    def _finish_keyboard_range_extension(self):
        self._extend_marks_on_next_selection = False
        return False

    def toggle_active_mark(self):
        """Toggles the active item without changing active selection."""
        if self.active_item_path is None:
            return False
        self._toggle_mark(self.active_item_path)
        return True

    def mark_all(self):
        """Marks every selectable item without navigation side effects."""
        self.marked_paths = set(self._ordered_item_paths())
        self._refresh_mark_styles()

    def clear_marks(self):
        """Clears operation marks and anchor without changing active item."""
        had_marks = bool(self.marked_paths)
        self.marked_paths.clear()
        self.mark_anchor_path = None
        self._extend_marks_on_next_selection = False
        self._refresh_mark_styles()
        return had_marks

    def get_marked_items(self):
        """Returns marked items in visible row order for file operations."""
        return [
            row.item for row in self.listbox.get_children()
            if hasattr(row, 'item') and row.item.path in self.marked_paths
        ]

    def get_operation_paths(self):
        """Returns marked paths, or the active path as an implicit target."""
        return resolve_operation_paths(
            (item.path for item in self.get_marked_items()),
            self.active_item_path,
        )

    def refresh(self, replacements=None):
        """Repopulate while preserving surviving active/marked paths."""
        previous_paths = self._ordered_item_paths()
        previous_active_path = self.active_item_path
        replacements = {
            Path(source): Path(destination)
            for source, destination in (replacements or {}).items()
        }
        active_path = replacements.get(
            self.active_item_path, self.active_item_path
        )
        marked_paths = {
            replacements.get(path, path) for path in self.marked_paths
        }
        anchor_path = replacements.get(
            self.mark_anchor_path, self.mark_anchor_path
        )

        self._extend_marks_on_next_selection = False
        self._suppress_navigation_callback = True
        try:
            self.populate()
            refreshed_paths = self._ordered_item_paths()
            visible_paths = set(refreshed_paths)
            selected_active_path = resolve_refreshed_active_path(
                previous_paths,
                refreshed_paths,
                previous_active_path,
                active_path,
            )
            self.marked_paths = marked_paths & visible_paths
            self.mark_anchor_path = (
                anchor_path if anchor_path in visible_paths else None
            )
            self.active_item_path = None
            if selected_active_path is not None:
                self.select_path(selected_active_path)
            else:
                self.listbox.unselect_all()
        finally:
            self._suppress_navigation_callback = False

        self._refresh_mark_styles()
        return selected_active_path != previous_active_path

    def contains_focus(self, focused_widget):
        """Returns whether GTK focus is within this column's listbox."""
        while focused_widget is not None:
            if focused_widget is self.listbox:
                return True
            focused_widget = focused_widget.get_parent()
        return False


class FilePreviewColumn(Gtk.Box):
    """Non-interactive text/image preview occupying the reserved child slot."""

    def __init__(self, path):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.path = Path(path)
        self._destroyed = False
        self._pixbuf = None
        self.preview_kind = "loading"
        self.set_hexpand(False)
        self.set_size_request(1, -1)
        self.get_style_context().add_class("file-preview-column")
        self.connect("destroy", self._on_destroy)

        title = Gtk.Label(label=self.path.name)
        title.set_xalign(0)
        title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        title.set_margin_start(8)
        title.set_margin_end(8)
        title.set_margin_top(6)
        self.pack_start(title, False, False, 0)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        loading = Gtk.Label(label="Loading preview…")
        loading.get_style_context().add_class("dim-label")
        self.content.pack_start(loading, True, True, 12)
        self.pack_start(self.content, True, True, 0)
        self.show_all()

        threading.Thread(
            target=self._load_preview,
            daemon=True,
        ).start()

    def _on_destroy(self, widget):
        self._destroyed = True

    def _load_preview(self):
        data = load_file_preview(self.path)
        pixbuf = None
        if data.kind == "image":
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(self.path), 1024, 1024, True
                )
            except Exception as error:
                data = FilePreviewData(
                    kind="unsupported", message=f"Image error: {error}"
                )

        if data.kind == "unsupported" and UNSUPPORTED_PREVIEW_IMAGE.exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(UNSUPPORTED_PREVIEW_IMAGE), 512, 512, True
                )
            except Exception:
                pixbuf = None

        GLib.idle_add(self._apply_preview, data, pixbuf)

    def _apply_preview(self, data, pixbuf):
        if self._destroyed:
            return False
        self.preview_kind = data.kind
        for child in self.content.get_children():
            self.content.remove(child)

        if data.kind == "text":
            self._show_text(data)
        else:
            self._show_image_or_fallback(data, pixbuf)
        self.content.show_all()
        return False

    def _show_text(self, data):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_monospace(True)
        text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        text_view.set_left_margin(8)
        text_view.set_right_margin(8)
        text_view.set_top_margin(8)
        text_view.set_bottom_margin(8)
        text_view.get_buffer().set_text(data.text)
        scroll.add(text_view)
        self.content.pack_start(scroll, True, True, 0)

        if data.truncated:
            status = Gtk.Label(label="Preview truncated")
            status.get_style_context().add_class("dim-label")
            self.content.pack_start(status, False, False, 6)

    def _show_image_or_fallback(self, data, pixbuf):
        self._pixbuf = pixbuf
        if pixbuf is not None:
            drawing = Gtk.DrawingArea()
            drawing.set_size_request(1, 1)
            drawing.set_vexpand(True)
            drawing.connect("draw", self._draw_pixbuf)
            self.content.pack_start(drawing, True, True, 0)
        else:
            icon = Gtk.Image.new_from_icon_name(
                "dialog-question-symbolic", Gtk.IconSize.DIALOG
            )
            self.content.pack_start(icon, True, True, 12)

        if data.kind == "unsupported":
            message = Gtk.Label(label=data.message or "Preview unavailable")
            message.set_ellipsize(Pango.EllipsizeMode.END)
            message.set_max_width_chars(1)
            message.set_tooltip_text(data.message or "Preview unavailable")
            message.set_margin_start(8)
            message.set_margin_end(8)
            message.set_margin_bottom(8)
            message.get_style_context().add_class("dim-label")
            self.content.pack_start(message, False, False, 0)

    def _draw_pixbuf(self, widget, context):
        if self._pixbuf is None:
            return False
        allocation = widget.get_allocation()
        pixbuf_width = self._pixbuf.get_width()
        pixbuf_height = self._pixbuf.get_height()
        scale = min(
            allocation.width / pixbuf_width,
            allocation.height / pixbuf_height,
        )
        x = (allocation.width - pixbuf_width * scale) / 2
        y = (allocation.height - pixbuf_height * scale) / 2
        context.save()
        context.translate(x, y)
        context.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
        context.paint()
        context.restore()
        return False


class ResizeHandle(Gtk.EventBox):
    """Handle for resizing columns"""

    def __init__(self, on_drag):
        super().__init__()
        self.on_drag = on_drag
        self.dragging = False
        self.start_x = 0

        # Visual separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.add(separator)

        # Style
        self.get_style_context().add_class("resize-handle")
        self.set_size_request(6, -1)

        # Events
        self.set_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                       Gdk.EventMask.BUTTON_RELEASE_MASK |
                       Gdk.EventMask.POINTER_MOTION_MASK)

        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    def _on_button_press(self, widget, event):
        """Handles mouse button press"""
        if event.button == 1:
            self.dragging = True
            self.start_x = event.x_root
            return True
        return False

    def _on_button_release(self, widget, event):
        """Handles mouse button release"""
        self.dragging = False
        return True

    def _on_motion(self, widget, event):
        """Handles mouse motion during drag"""
        if self.dragging:
            delta = event.x_root - self.start_x
            self.start_x = event.x_root
            self.on_drag(self, delta)
            return True
        return False

    def _on_enter(self, widget, event):
        """Handles mouse enter - changes cursor"""
        window = self.get_window()
        if window:
            window.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), "col-resize"))

    def _on_leave(self, widget, event):
        """Handles mouse leave - resets cursor"""
        if not self.dragging:
            window = self.get_window()
            if window:
                window.set_cursor(None)


class PreviewPanel(Gtk.Box):
    """Metadata inspector for the active file or directory."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)
        self.set_size_request(280, -1)

        self.icon_theme = Gtk.IconTheme.get_default()
        self._directory_size_request_id = 0
        self._directory_size_cancel_event = None
        self._current_item_path = None
        self.connect("destroy", self._on_destroy)

        # Large icon
        self.icon_image = Gtk.Image()
        self.pack_start(self.icon_image, False, False, 0)

        # File name
        self.name_label = Gtk.Label()
        self.name_label.set_line_wrap(True)
        self.name_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.name_label.set_max_width_chars(25)
        self.name_label.get_style_context().add_class("preview-title")
        self.pack_start(self.name_label, False, False, 0)

        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.pack_start(separator, False, False, 10)

        # Information grid
        self.info_grid = Gtk.Grid()
        self.info_grid.set_column_spacing(12)
        self.info_grid.set_row_spacing(6)
        self.pack_start(self.info_grid, False, False, 0)

        self.show_all()

    def update(self, item):
        """Updates the preview with item information"""
        request_id = self._cancel_directory_size_request()
        if item is None:
            self._clear_widgets()
            return
        self._current_item_path = item.path

        icon = item.get_icon(self.icon_theme, 64)
        if icon:
            self.icon_image.set_from_pixbuf(icon)

        self.name_label.set_markup(f"<b>{GLib.markup_escape_text(item.name)}</b>")

        # Clear previous info
        for child in self.info_grid.get_children():
            self.info_grid.remove(child)

        row = 0

        # Type
        if item.is_dir:
            file_type = "Folder"
        else:
            mime_type, _ = mimetypes.guess_type(str(item.path))
            file_type = mime_type or "File"
        self._add_info_row("Type:", file_type, row)
        row += 1

        size_value = None
        contents_value = None
        if item.is_dir:
            size_value = self._add_info_row("Size:", "Calculating…", row)
            row += 1
            contents_value = self._add_info_row(
                "Contents:", "Calculating…", row
            )
            row += 1
        else:
            try:
                size = item.path.stat().st_size
                self._add_info_row("Size:", self._format_size(size), row)
                row += 1
            except (PermissionError, OSError):
                pass

        # Modified date
        try:
            mtime = item.path.stat().st_mtime
            from datetime import datetime
            mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            self._add_info_row("Modified:", mtime_str, row)
            row += 1
        except (PermissionError, OSError):
            pass

        # Path
        self._add_info_row("Path:", str(item.path.parent), row)

        self.info_grid.show_all()
        if item.is_dir:
            self._start_directory_size_request(
                request_id,
                item.path,
                size_value,
                contents_value,
            )

    def _add_info_row(self, label_text, value_text, row):
        """Adds an information row"""
        label = Gtk.Label(label=label_text)
        label.set_xalign(1)
        label.get_style_context().add_class("dim-label")
        self.info_grid.attach(label, 0, row, 1, 1)

        value = Gtk.Label(label=value_text)
        value.set_xalign(0)
        value.set_line_wrap(True)
        value.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        value.set_max_width_chars(20)
        value.set_selectable(True)
        self.info_grid.attach(value, 1, row, 1, 1)
        return value

    def _cancel_directory_size_request(self):
        """Cancel an obsolete scan and return a fresh request identity."""
        if self._directory_size_cancel_event is not None:
            self._directory_size_cancel_event.set()
        self._directory_size_cancel_event = None
        self._directory_size_request_id += 1
        return self._directory_size_request_id

    def _start_directory_size_request(self, request_id, path,
                                      size_value, contents_value):
        cancel_event = threading.Event()
        self._directory_size_cancel_event = cancel_event

        def calculate():
            result = calculate_directory_size(path, cancel_event)
            GLib.idle_add(
                self._apply_directory_size,
                request_id,
                Path(path),
                size_value,
                contents_value,
                result,
            )

        threading.Thread(target=calculate, daemon=True).start()

    def _apply_directory_size(self, request_id, path, size_value,
                              contents_value, result):
        """Apply a scan result only while its directory remains active."""
        if (result.cancelled or
                request_id != self._directory_size_request_id or
                path != self._current_item_path):
            return False

        size_text = self._format_size(result.size)
        if result.error_count:
            size_text += " (partial)"
        size_value.set_text(size_text)

        item_count = result.file_count + result.directory_count
        contents_text = "Empty" if item_count == 0 else f"{item_count} items"
        if result.error_count:
            contents_text += f" · {result.error_count} unreadable"
        contents_value.set_text(contents_text)
        self._directory_size_cancel_event = None
        return False

    def _format_size(self, size):
        """Formats size in human-readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def clear(self):
        """Clears the metadata inspector."""
        self._cancel_directory_size_request()
        self._clear_widgets()

    def _clear_widgets(self):
        self._current_item_path = None
        self.icon_image.clear()
        self.name_label.set_text("")
        for child in self.info_grid.get_children():
            self.info_grid.remove(child)

    def _on_destroy(self, widget):
        self._cancel_directory_size_request()


@dataclass
class SearchResult:
    """Represents a search result"""
    path: Path
    name: str
    is_dir: bool
    match_type: str  # "name" or "content"


class SearchEngine:
    """Handles file searching with name and content matching"""

    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB limit for content search

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        """Cancels the current search"""
        self.cancelled = True

    def search(self, root_path: Path, query: str) -> Generator[SearchResult, None, None]:
        """
        Searches for files matching the query.
        Yields SearchResult objects as they are found.
        """
        self.cancelled = False
        query_lower = query.lower()

        for dirpath, dirnames, filenames in os.walk(root_path):
            if self.cancelled:
                return

            # Skip hidden directories
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]

            current_dir = Path(dirpath)

            # Search in directory names
            for dirname in dirnames:
                if self.cancelled:
                    return
                if query_lower in dirname.lower():
                    yield SearchResult(
                        path=current_dir / dirname,
                        name=dirname,
                        is_dir=True,
                        match_type="name"
                    )

            # Search in file names and contents
            for filename in filenames:
                if self.cancelled:
                    return

                # Skip hidden files
                if filename.startswith('.'):
                    continue

                file_path = current_dir / filename

                # Check if name matches
                name_match = query_lower in filename.lower()
                if name_match:
                    yield SearchResult(
                        path=file_path,
                        name=filename,
                        is_dir=False,
                        match_type="name"
                    )
                    continue  # Don't search content if name already matches

                # Search in file content
                if self._search_in_content(file_path, query_lower):
                    yield SearchResult(
                        path=file_path,
                        name=filename,
                        is_dir=False,
                        match_type="content"
                    )

    def _search_in_content(self, file_path: Path, query: str) -> bool:
        """Searches for query in file content. Returns True if found."""
        try:
            # Check file size
            if file_path.stat().st_size > self.MAX_FILE_SIZE:
                return False

            # Check if it's a text file
            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                return False

            # Only search text-like files
            text_types = ('text/', 'application/json', 'application/xml',
                         'application/javascript', 'application/x-python',
                         'application/x-sh', 'application/x-perl')
            if not any(mime_type.startswith(t) for t in text_types):
                return False

            # Read and search
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                return query in content.lower()

        except (PermissionError, OSError, UnicodeDecodeError):
            return False


class SearchResultsView(Gtk.Box):
    """View for displaying search results"""

    def __init__(self, on_result_activated):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_result_activated = on_result_activated
        self.icon_theme = Gtk.IconTheme.get_default()

        # Header with result count and close button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(12)
        header.set_margin_end(12)
        header.set_margin_top(8)
        header.set_margin_bottom(8)

        self.status_label = Gtk.Label(label="Searching...")
        self.status_label.set_xalign(0)
        header.pack_start(self.status_label, True, True, 0)

        # Spinner for loading
        self.spinner = Gtk.Spinner()
        header.pack_start(self.spinner, False, False, 0)

        self.pack_start(header, False, False, 0)

        # Scrolled window for results
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        # ListBox for results
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.get_style_context().add_class("search-results")

        scroll.add(self.listbox)
        self.pack_start(scroll, True, True, 0)

        self.result_count = 0

    def clear(self):
        """Clears all results"""
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.result_count = 0
        self.status_label.set_text("Searching...")

    def start_search(self):
        """Called when search starts"""
        self.clear()
        self.spinner.start()

    def stop_search(self):
        """Called when search completes"""
        self.spinner.stop()
        if self.result_count == 0:
            self.status_label.set_text("No results found")
        else:
            self.status_label.set_text(f"{self.result_count} results")

    def add_result(self, result: SearchResult):
        """Adds a search result to the list"""
        self.result_count += 1

        row = Gtk.ListBoxRow()
        row.result = result

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox.set_margin_start(12)
        hbox.set_margin_end(12)
        hbox.set_margin_top(6)
        hbox.set_margin_bottom(6)

        # Icon
        if result.is_dir:
            icon_name = "folder"
        else:
            mime_type, _ = mimetypes.guess_type(str(result.path))
            if mime_type:
                icon_name = mime_type.replace('/', '-')
                if not self.icon_theme.has_icon(icon_name):
                    icon_name = "text-x-generic"
            else:
                icon_name = "text-x-generic"

        try:
            icon = self.icon_theme.load_icon(icon_name, 24, Gtk.IconLookupFlags.FORCE_SIZE)
            image = Gtk.Image.new_from_pixbuf(icon)
        except Exception:
            image = Gtk.Image.new_from_icon_name("text-x-generic", Gtk.IconSize.LARGE_TOOLBAR)
        hbox.pack_start(image, False, False, 0)

        # Text container
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # File name with match type indicator
        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_label = Gtk.Label(label=result.name)
        name_label.set_xalign(0)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_box.pack_start(name_label, False, False, 0)

        # Match type badge
        if result.match_type == "content":
            badge = Gtk.Label(label="content")
            badge.get_style_context().add_class("dim-label")
            badge.set_markup("<small><i>in content</i></small>")
            name_box.pack_start(badge, False, False, 0)

        text_box.pack_start(name_box, False, False, 0)

        # Path
        path_label = Gtk.Label(label=str(result.path.parent))
        path_label.set_xalign(0)
        path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path_label.get_style_context().add_class("dim-label")
        text_box.pack_start(path_label, False, False, 0)

        hbox.pack_start(text_box, True, True, 0)

        # Arrow
        arrow = Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.MENU)
        arrow.set_opacity(0.5)
        hbox.pack_end(arrow, False, False, 0)

        row.add(hbox)
        self.listbox.add(row)
        row.show_all()

        # Update status
        self.status_label.set_text(f"{self.result_count} results...")

    def _on_row_activated(self, listbox, row):
        """Handles result activation"""
        if row and hasattr(row, 'result'):
            self.on_result_activated(row.result)


class MillerColumnsContainer(Gtk.Box):
    """Container for Miller columns with resizing support"""

    def __init__(self, on_item_selected, on_item_activated,
                 on_files_dropped, on_drag_finished):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.on_item_selected_callback = on_item_selected
        self.on_item_activated_callback = on_item_activated
        self.on_files_dropped_callback = on_files_dropped
        self.on_drag_finished_callback = on_drag_finished

        self.columns = []  # List of ColumnView
        self.handles = []  # List of ResizeHandle
        self.column_widths = []  # Column widths (-1 = auto)
        self.reserved_child_column_index = 0
        self.width_slot_count = 1
        self.file_preview = None
        self.file_preview_separator = None
        self.file_preview_source = None

        self.get_style_context().add_class("miller-columns-container")

    def add_column(self, path):
        """Adds a new column"""
        self.clear_file_preview()
        column = ColumnView(
            path,
            self._on_item_selected,
            self.on_item_activated_callback,
            self.on_files_dropped_callback,
            self.on_drag_finished_callback,
        )

        # If there are existing columns, add a resize handle
        if self.columns:
            handle = ResizeHandle(self._on_handle_drag)
            handle.column_index = len(self.columns) - 1
            self.handles.append(handle)
            self.pack_start(handle, False, False, 0)
            handle.show_all()

        self.columns.append(column)
        self.column_widths.append(-1)  # -1 means "auto"

        # Width distribution reserves an empty child slot. Do not let GtkBox
        # expand real columns into that reserved space.
        self.pack_start(column, False, True, 0)
        column.show_all()

        # Recalculate widths
        GLib.idle_add(self._distribute_widths)

        return column

    def remove_columns_after(self, column):
        """Removes all columns after the specified one"""
        if column not in self.columns:
            return

        self.clear_file_preview()

        idx = self.columns.index(column)

        # Remove columns and handles
        while len(self.columns) > idx + 1:
            col = self.columns.pop()
            self.remove(col)
            col.destroy()
            self.column_widths.pop()

            if self.handles:
                handle = self.handles.pop()
                self.remove(handle)
                handle.destroy()

        # Recalculate widths
        GLib.idle_add(self._distribute_widths)

    def clear(self):
        """Removes all columns"""
        self.clear_file_preview()
        for col in self.columns:
            self.remove(col)
            col.destroy()
        for handle in self.handles:
            self.remove(handle)
            handle.destroy()
        self.columns.clear()
        self.handles.clear()
        self.column_widths.clear()
        self.reserved_child_column_index = 0
        self.width_slot_count = 1

    def show_file_preview(self, column, path):
        """Fill the reserved child slot with a non-navigation file preview."""
        if column not in self.columns:
            return
        self.clear_file_preview()

        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.set_size_request(RESIZE_HANDLE_WIDTH, -1)
        separator.get_style_context().add_class("resize-handle")
        preview = FilePreviewColumn(path)

        self.file_preview_source = column
        self.file_preview_separator = separator
        self.file_preview = preview
        self.pack_start(separator, False, False, 0)
        self.pack_start(preview, False, True, 0)
        separator.show_all()
        preview.show_all()
        GLib.idle_add(self._distribute_widths)

    def clear_file_preview(self):
        """Remove the transient preview slot without changing directories."""
        if self.file_preview is not None:
            self.remove(self.file_preview)
            self.file_preview.destroy()
        if self.file_preview_separator is not None:
            self.remove(self.file_preview_separator)
            self.file_preview_separator.destroy()
        self.file_preview = None
        self.file_preview_separator = None
        self.file_preview_source = None

    def _on_item_selected(self, column, item):
        """Handles selection and recalculates widths"""
        self.reserve_child_slot(column)
        self.on_item_selected_callback(column, item)
        GLib.idle_add(self._distribute_widths)

    def reserve_child_slot(self, column):
        """Reserve stable width for one potential child of ``column``."""
        if column not in self.columns:
            return
        self.reserved_child_column_index = self.columns.index(column)
        self.width_slot_count = calculate_width_slot_count(
            len(self.columns), self.reserved_child_column_index
        )
        GLib.idle_add(self._distribute_widths)

    def _distribute_widths(self):
        """Distributes widths equally among columns"""
        if not self.columns:
            return False

        allocation = self.get_allocation()
        total_width = allocation.width

        if total_width <= 1:
            return False

        self.width_slot_count = calculate_width_slot_count(
            len(self.columns), self.reserved_child_column_index
        )
        auto_width = calculate_auto_column_width(
            total_width,
            self.column_widths,
            self.width_slot_count,
        )

        # Apply widths
        for i, col in enumerate(self.columns):
            if self.column_widths[i] == -1:
                col.set_size_request(auto_width, -1)
            else:
                col.set_size_request(self.column_widths[i], -1)

        if self.file_preview is not None:
            self.file_preview.set_size_request(auto_width, -1)

        return False

    def _on_handle_drag(self, handle, delta):
        """Handles dragging of a resize handle"""
        idx = handle.column_index

        if idx >= len(self.columns) - 1:
            return

        # Get current widths
        left_col = self.columns[idx]
        right_col = self.columns[idx + 1]

        left_width = left_col.get_allocation().width
        right_width = right_col.get_allocation().width

        # Calculate new widths
        new_left = left_width + delta
        new_right = right_width - delta

        # Respect minimum widths
        if new_left < ColumnView.MIN_WIDTH:
            delta = ColumnView.MIN_WIDTH - left_width
            new_left = ColumnView.MIN_WIDTH
            new_right = right_width - delta

        if new_right < ColumnView.MIN_WIDTH:
            delta = right_width - ColumnView.MIN_WIDTH
            new_right = ColumnView.MIN_WIDTH
            new_left = left_width + delta

        # Update stored widths
        self.column_widths[idx] = max(ColumnView.MIN_WIDTH, int(new_left))
        self.column_widths[idx + 1] = max(ColumnView.MIN_WIDTH, int(new_right))

        # Apply
        left_col.set_size_request(self.column_widths[idx], -1)
        right_col.set_size_request(self.column_widths[idx + 1], -1)


class MillerColumnsWindow(Gtk.ApplicationWindow):
    """Main window with Miller Columns view"""

    def __init__(self, app, start_path=None):
        super().__init__(application=app, title="Nemo Miller Columns")

        self.set_default_size(1200, 700)
        self.current_path = Path(start_path or Path.home())

        # Search state
        self.search_mode = False
        self.search_engine = SearchEngine()
        self.search_thread = None
        self.search_timeout_id = None

        # Application-owned file clipboard with GNOME/Nemo interoperability.
        self.clipboard_mode = None
        self.clipboard_paths = ()
        self.clipboard_selection = Gdk.SELECTION_CLIPBOARD
        self.gnome_copied_files_target = Gdk.Atom.intern(
            GNOME_COPIED_FILES_NAME, False
        )
        self.uri_list_target = Gdk.Atom.intern(URI_LIST_NAME, False)
        self.clipboard_owner = Gtk.Invisible()
        self.clipboard_owner.connect(
            "selection-get", self._on_clipboard_selection_get
        )
        self.clipboard_owner.connect(
            "selection-clear-event", self._on_clipboard_selection_clear
        )
        Gtk.selection_add_target(
            self.clipboard_owner,
            self.clipboard_selection,
            self.gnome_copied_files_target,
            GNOME_COPIED_FILES_INFO,
        )
        Gtk.selection_add_target(
            self.clipboard_owner,
            self.clipboard_selection,
            self.uri_list_target,
            URI_LIST_INFO,
        )

        # GTK may undo focus changes made during key-press CAPTURE. Hold one
        # replaceable target until key release, with one idle fallback for
        # keyboard/driver paths where that release is not delivered here.
        self.pending_focus_column = None
        self.pending_focus_keyval = None
        self.pending_focus_source_id = None
        self.pending_focus_select_first = False

        self._setup_css()

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(main_box)

        # Toolbar
        self._create_toolbar(main_box)

        # Main area
        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        main_box.pack_start(self.main_paned, True, True, 0)

        # Stack for switching between columns and search results
        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(150)

        # Columns container
        self.columns_container = MillerColumnsContainer(
            self._on_item_selected,
            self._on_item_activated,
            self._on_files_dropped,
            self._on_drag_finished,
        )
        columns_frame = Gtk.Frame()
        columns_frame.add(self.columns_container)
        self.content_stack.add_named(columns_frame, "columns")

        # Search results view
        self.search_results_view = SearchResultsView(self._on_search_result_activated)
        search_frame = Gtk.Frame()
        search_frame.add(self.search_results_view)
        self.content_stack.add_named(search_frame, "search")

        self.main_paned.pack1(self.content_stack, True, False)

        # Preview panel
        self.preview_panel = PreviewPanel()
        preview_frame = Gtk.Frame()
        preview_frame.add(self.preview_panel)
        preview_frame.get_style_context().add_class("preview-frame")
        self.main_paned.pack2(preview_frame, False, False)

        self.main_paned.set_position(900)

        # Navigate to initial path
        self._navigate_to(self.current_path)

        self.connect("key-press-event", self._on_key_press)
        self.miller_key_controller = Gtk.EventControllerKey.new(self)
        self.miller_key_controller.set_propagation_phase(
            Gtk.PropagationPhase.CAPTURE
        )
        self.miller_key_controller.connect(
            "key-pressed", self._on_miller_navigation_key_pressed
        )
        self.miller_key_controller.connect(
            "key-released", self._on_miller_navigation_key_released
        )
        self.show_all()

    def _setup_css(self):
        """Sets up custom CSS styles"""
        css = b"""
        .miller-columns-container {
            background-color: @theme_base_color;
        }

        .miller-column {
            background-color: @theme_base_color;
        }

        .file-preview-column {
            background-color: @theme_base_color;
            border-left: 1px solid @borders;
        }

        .miller-column row {
            padding: 2px;
        }

        .miller-column row:selected {
            background-color: alpha(@theme_selected_bg_color, 0.20);
            color: @theme_fg_color;
            box-shadow: inset 0 0 0 1px @theme_selected_bg_color;
        }

        .miller-column row.marked-item {
            background-color: @theme_selected_bg_color;
            color: @theme_selected_fg_color;
        }

        .miller-column row.marked-item:selected {
            background-color: @theme_selected_bg_color;
            color: @theme_selected_fg_color;
            box-shadow: inset 0 0 0 2px @theme_selected_fg_color;
        }

        .preview-frame {
            background-color: @theme_base_color;
        }

        .preview-title {
            font-size: 14px;
        }

        .path-bar {
            padding: 4px 8px;
            background-color: @theme_bg_color;
        }

        .path-button {
            padding: 2px 6px;
            min-height: 24px;
        }

        .resize-handle {
            background-color: @borders;
            min-width: 6px;
        }

        .resize-handle:hover {
            background-color: @theme_selected_bg_color;
        }

        .search-results {
            background-color: @theme_base_color;
        }

        .search-results row {
            padding: 4px;
        }

        .search-results row:selected {
            background-color: @theme_selected_bg_color;
            color: @theme_selected_fg_color;
        }
        """

        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _create_toolbar(self, container):
        """Creates the navigation toolbar"""
        toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar_box.set_margin_start(6)
        toolbar_box.set_margin_end(6)
        toolbar_box.set_margin_top(6)
        toolbar_box.set_margin_bottom(6)

        # Back button
        back_btn = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        back_btn.set_tooltip_text("Back")
        back_btn.connect("clicked", self._on_go_back)
        toolbar_box.pack_start(back_btn, False, False, 0)

        # Home button
        home_btn = Gtk.Button.new_from_icon_name("go-home-symbolic", Gtk.IconSize.BUTTON)
        home_btn.set_tooltip_text("Home")
        home_btn.connect("clicked", self._on_go_home)
        toolbar_box.pack_start(home_btn, False, False, 0)

        # Path bar
        self.path_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.path_bar.get_style_context().add_class("path-bar")

        path_scroll = Gtk.ScrolledWindow()
        path_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        path_scroll.add(self.path_bar)
        toolbar_box.pack_start(path_scroll, True, True, 0)

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search files and contents...")
        self.search_entry.set_width_chars(25)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", self._on_search_stopped)
        toolbar_box.pack_start(self.search_entry, False, False, 0)

        # Open in Nemo button
        nemo_btn = Gtk.Button.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.BUTTON)
        nemo_btn.set_tooltip_text("Open in Nemo")
        nemo_btn.connect("clicked", self._on_open_in_nemo)
        toolbar_box.pack_end(nemo_btn, False, False, 0)

        # Terminal button
        terminal_btn = Gtk.Button.new_from_icon_name("utilities-terminal-symbolic", Gtk.IconSize.BUTTON)
        terminal_btn.set_tooltip_text("Open Terminal here")
        terminal_btn.connect("clicked", self._on_open_terminal)
        toolbar_box.pack_end(terminal_btn, False, False, 0)

        container.pack_start(toolbar_box, False, False, 0)

    def _update_path_bar(self):
        """Updates the path bar"""
        for child in self.path_bar.get_children():
            self.path_bar.remove(child)

        parts = self.current_path.parts
        for i, part in enumerate(parts):
            if i > 0:
                sep = Gtk.Label(label="/")
                sep.set_opacity(0.5)
                self.path_bar.pack_start(sep, False, False, 0)

            btn = Gtk.Button(label=part or "/")
            btn.get_style_context().add_class("path-button")
            btn.get_style_context().add_class("flat")
            btn.path = Path(*parts[:i+1])
            btn.connect("clicked", self._on_path_button_clicked)
            self.path_bar.pack_start(btn, False, False, 0)

        self.path_bar.show_all()

    def _navigate_to(self, path):
        """Navigates to a specific path"""
        try:
            path = Path(path).resolve()
        except (OSError, RuntimeError):
            return False

        if not path.is_dir():
            return False

        self.columns_container.clear()
        self.preview_panel.clear()

        parts = path.parts
        current = Path(parts[0])

        self.columns_container.add_column(current)

        for part in parts[1:]:
            next_path = current / part
            if next_path.is_dir():
                if self.columns_container.columns:
                    # This selection only renders the ancestry cursor. Its
                    # normal callback would add the child here, and the next
                    # line would then add the same semantic column again.
                    self.columns_container.columns[-1].select_path(
                        next_path, notify_navigation=False
                    )
                self.columns_container.add_column(next_path)
                current = next_path

        if self.columns_container.columns:
            self.columns_container.reserve_child_slot(
                self.columns_container.columns[-1]
            )

        self.current_path = path
        self._update_path_bar()
        return True

    def _on_item_selected(self, column, item):
        """Handles item selection"""
        self.columns_container.remove_columns_after(column)

        if item.is_dir:
            self.columns_container.add_column(item.path)
            self.current_path = item.path
        else:
            self.current_path = item.path.parent
            self.columns_container.show_file_preview(column, item.path)

        self._update_path_bar()
        self.preview_panel.update(item)

    def _on_item_activated(self, item):
        """Handles item activation (double-click)"""
        if item.is_dir:
            focused_column = self._get_focused_column()
            if focused_column is not None:
                self._enter_active_item(focused_column)
            return

        try:
            subprocess.Popen(['xdg-open', str(item.path)])
        except Exception as e:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Cannot open file: {e}"
            )
            dialog.run()
            dialog.destroy()

    def _on_go_back(self, button):
        """Goes to parent directory"""
        if self.current_path.parent != self.current_path:
            self._navigate_to(self.current_path.parent)

    def _on_go_home(self, button):
        """Goes to home directory"""
        self._navigate_to(Path.home())

    def _on_path_button_clicked(self, button):
        """Handles path button click"""
        self._navigate_to(button.path)

    def _on_open_in_nemo(self, button):
        """Opens current folder in Nemo"""
        try:
            subprocess.Popen(['nemo', str(self.current_path)])
        except Exception as e:
            print(f"Error opening Nemo: {e}")

    def _on_open_terminal(self, button):
        """Opens a terminal in the current folder"""
        try:
            terminals = ['gnome-terminal', 'xfce4-terminal', 'konsole', 'xterm']
            for term in terminals:
                try:
                    subprocess.Popen([term, '--working-directory', str(self.current_path)])
                    return
                except FileNotFoundError:
                    continue
        except Exception as e:
            print(f"Error opening terminal: {e}")

    def _on_search_changed(self, search_entry):
        """Handles search text changes with debounce"""
        # Cancel any pending search timeout
        if self.search_timeout_id:
            GLib.source_remove(self.search_timeout_id)
            self.search_timeout_id = None

        query = search_entry.get_text().strip()

        if not query:
            # Clear search and return to columns view
            self._exit_search_mode()
            return

        # Debounce: wait 300ms before starting search
        self.search_timeout_id = GLib.timeout_add(300, self._start_search, query)

    def _on_search_stopped(self, search_entry):
        """Handles search stop (Escape in search entry)"""
        self._exit_search_mode()

    def _start_search(self, query):
        """Starts the actual search in a background thread"""
        self.search_timeout_id = None

        # Cancel any running search
        if self.search_thread and self.search_thread.is_alive():
            self.search_engine.cancel()
            self.search_thread.join(timeout=0.5)

        # Enter search mode
        self.search_mode = True
        self.content_stack.set_visible_child_name("search")
        self.search_results_view.start_search()

        # Start new search thread
        self.search_thread = threading.Thread(
            target=self._search_thread_func,
            args=(self.current_path, query),
            daemon=True
        )
        self.search_thread.start()

        return False  # Don't repeat timeout

    def _search_thread_func(self, root_path, query):
        """Search function running in background thread"""
        try:
            for result in self.search_engine.search(root_path, query):
                if self.search_engine.cancelled:
                    break
                # Add result to UI via main thread
                GLib.idle_add(self.search_results_view.add_result, result)
        finally:
            # Signal search complete
            GLib.idle_add(self.search_results_view.stop_search)

    def _exit_search_mode(self):
        """Exits search mode and returns to columns view"""
        # Cancel any running search
        if self.search_thread and self.search_thread.is_alive():
            self.search_engine.cancel()

        self.search_mode = False
        self.content_stack.set_visible_child_name("columns")
        self.search_entry.set_text("")
        self.search_results_view.clear()

    def _on_search_result_activated(self, result: SearchResult):
        """Handles activation of a search result"""
        # Navigate to the file's parent directory
        if result.is_dir:
            target_path = result.path
        else:
            target_path = result.path.parent

        # Exit search mode and navigate
        self._exit_search_mode()
        self._navigate_to(target_path)

        # If it's a file, open it
        if not result.is_dir:
            try:
                subprocess.Popen(['xdg-open', str(result.path)])
            except Exception as e:
                print(f"Error opening file: {e}")

    def _on_key_press(self, widget, event):
        """Handles keyboard shortcuts"""
        focused_column = self._get_focused_column()
        shortcut_keyval = self._shortcut_keyval(
            event.keyval, event.hardware_keycode
        )

        # Ctrl+A: mark all items in the focused Miller column.
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            if (shortcut_keyval == Gdk.KEY_a and
                    not event.state & (
                        Gdk.ModifierType.SHIFT_MASK |
                        Gdk.ModifierType.MOD1_MASK |
                        Gdk.ModifierType.MOD4_MASK |
                        Gdk.ModifierType.SUPER_MASK
                    ) and focused_column is not None):
                focused_column.mark_all()
                return True

            # Ctrl+F: Focus search entry
            if shortcut_keyval == Gdk.KEY_f:
                self.search_entry.grab_focus()
                return True

        # Space toggles only the active item's operation mark.
        if (event.keyval == Gdk.KEY_space and
                not event.state & (
                    Gdk.ModifierType.SHIFT_MASK |
                    Gdk.ModifierType.CONTROL_MASK |
                    Gdk.ModifierType.MOD1_MASK |
                    Gdk.ModifierType.MOD4_MASK |
                    Gdk.ModifierType.SUPER_MASK
                ) and focused_column is not None):
            focused_column.toggle_active_mark()
            return True

        # Native Gtk.ListBox still moves the active row. This flag only makes
        # Shift+Up/Down extend operation marks when that selection callback runs.
        if (event.state & Gdk.ModifierType.SHIFT_MASK and
                not event.state & (
                    Gdk.ModifierType.CONTROL_MASK |
                    Gdk.ModifierType.MOD1_MASK |
                    Gdk.ModifierType.MOD4_MASK |
                    Gdk.ModifierType.SUPER_MASK
                ) and event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and
                focused_column is not None):
            focused_column.prepare_keyboard_range_extension()
            return False

        # Escape: Exit search mode or close window
        if event.keyval == Gdk.KEY_Escape:
            if self.search_mode:
                self._exit_search_mode()
                return True
            elif self._clear_all_marks():
                return True
            else:
                self.close()
                return True

        # Backspace: Go back (only if not in search entry)
        if event.keyval == Gdk.KEY_BackSpace:
            # Don't intercept if focus is on search entry
            if not self.search_entry.has_focus():
                self._on_go_back(None)
                return True

        return False

    def _get_focused_column(self):
        """Returns the Miller column containing the current GTK focus."""
        focused_widget = self.get_focus()
        if isinstance(focused_widget, Gtk.Editable):
            return None
        for column in self.columns_container.columns:
            if column.contains_focus(focused_widget):
                return column
        return None

    def _on_miller_navigation_key_pressed(self, controller, keyval,
                                          keycode, state):
        """Captures file hotkeys and Miller keys in a focused column."""
        column = self.pending_focus_column or self._get_focused_column()
        if column is None:
            return False

        if state & (
                Gdk.ModifierType.MOD1_MASK |
                Gdk.ModifierType.MOD4_MASK |
                Gdk.ModifierType.SUPER_MASK):
            return False

        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        lower_keyval = self._shortcut_keyval(keyval, keycode)

        if control and not shift:
            if lower_keyval == Gdk.KEY_c:
                self._stage_clipboard(CLIPBOARD_COPY, column)
                return True
            if lower_keyval == Gdk.KEY_x:
                self._stage_clipboard(CLIPBOARD_CUT, column)
                return True
            if lower_keyval == Gdk.KEY_v:
                self._paste_clipboard(column)
                return True

        if control and shift and lower_keyval == Gdk.KEY_n:
            self._create_new_folder(column)
            return True

        if not control and not shift:
            if keyval == Gdk.KEY_F2:
                self._rename_operation_target(column)
                return True
            if keyval == Gdk.KEY_Delete:
                self._trash_operation_targets(column)
                return True
            if keyval == Gdk.KEY_Left:
                self._focus_previous_column(column, keyval)
                return True
            if keyval in (Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._enter_active_item(column, keyval)
                return True
        return False

    def _shortcut_keyval(self, keyval, hardware_keycode):
        """Resolve letter shortcuts from physical keys, independent of layout."""
        keymap = Gdk.Keymap.get_for_display(self.get_display())
        translated, base_keyval, _group, _level, _consumed = (
            keymap.translate_keyboard_state(
                hardware_keycode, Gdk.ModifierType(0), 0
            )
        )
        if translated:
            return Gdk.keyval_to_lower(base_keyval)
        return Gdk.keyval_to_lower(keyval)

    def _on_miller_navigation_key_released(self, controller, keyval,
                                           keycode, state):
        """Apply a captured column transition before the next key press."""
        if keyval != self.pending_focus_keyval:
            return
        self._apply_pending_column_focus()

    def _clear_all_marks(self):
        """Clears marks in every live column without changing navigation."""
        had_marks = False
        for column in self.columns_container.columns:
            had_marks = column.clear_marks() or had_marks
        return had_marks

    def _stage_clipboard(self, mode, column):
        """Snapshot focused-column targets without changing their marks."""
        paths = column.get_operation_paths()
        if paths:
            self._publish_file_clipboard(mode, paths)

    def _publish_file_clipboard(self, mode, paths):
        """Own the X11 clipboard and advertise GNOME/Nemo file targets."""
        paths = tuple(Path(path) for path in paths)
        owned = Gtk.selection_owner_set(
            self.clipboard_owner,
            self.clipboard_selection,
            Gdk.CURRENT_TIME,
        )
        self.clipboard_mode = mode
        self.clipboard_paths = paths
        if not owned:
            self._show_error(
                "Clipboard unavailable",
                "Could not publish files to the desktop clipboard."
            )

    def _on_clipboard_selection_get(self, widget, selection_data, info, time_):
        """Provide advertised file data when another application requests it."""
        if not self.clipboard_paths or self.clipboard_mode is None:
            return
        if info == GNOME_COPIED_FILES_INFO:
            payload = serialize_gnome_file_clipboard(
                self.clipboard_mode, self.clipboard_paths
            )
            selection_data.set(
                self.gnome_copied_files_target, 8, payload
            )
        elif info == URI_LIST_INFO:
            selection_data.set_uris([
                path.resolve().as_uri() for path in self.clipboard_paths
            ])

    def _on_clipboard_selection_clear(self, widget, event):
        """Drop stale internal state when another owner replaces clipboard."""
        self.clipboard_mode = None
        self.clipboard_paths = ()
        return False

    def _paste_clipboard(self, column):
        """Request external file targets, falling back to in-app state."""
        destination_directory = column.get_operation_destination()
        clipboard = Gtk.Clipboard.get(self.clipboard_selection)
        clipboard.request_targets(
            self._on_clipboard_targets_received,
            destination_directory,
        )

    def _on_clipboard_targets_received(self, clipboard, targets, n_targets,
                                       destination_directory):
        """Choose the richest supported external file clipboard format."""
        targets = tuple((targets or ())[:n_targets])
        if self.gnome_copied_files_target in targets:
            clipboard.request_contents(
                self.gnome_copied_files_target,
                self._on_clipboard_contents_received,
                (destination_directory, GNOME_COPIED_FILES_INFO),
            )
            return
        if self.uri_list_target in targets:
            clipboard.request_contents(
                self.uri_list_target,
                self._on_clipboard_contents_received,
                (destination_directory, URI_LIST_INFO),
            )
            return
        if (self.clipboard_mode in (CLIPBOARD_COPY, CLIPBOARD_CUT) and
                self.clipboard_paths):
            self._execute_paste(
                self.clipboard_mode,
                self.clipboard_paths,
                destination_directory,
            )

    def _on_clipboard_contents_received(self, clipboard, selection_data,
                                        context):
        """Parse external file targets and execute the existing paste path."""
        destination_directory, target_info = context
        try:
            if target_info == GNOME_COPIED_FILES_INFO:
                mode, paths = parse_gnome_file_clipboard(
                    selection_data.get_data()
                )
            else:
                uris = selection_data.get_uris() or ()
                mode = CLIPBOARD_COPY
                paths = parse_file_uri_list("\n".join(uris))
                if not paths:
                    raise FileOperationError("Clipboard file list is empty")
        except (FileOperationError, UnicodeError) as error:
            self._show_error("Paste failed", str(error))
            return
        self._execute_paste(mode, paths, destination_directory)

    def _execute_paste(self, mode, clipboard_paths, destination_directory,
                       update_cut_clipboard=True, failure_title="Paste failed"):
        """Copy or move parsed clipboard paths into a resolved destination."""
        clipboard_paths = tuple(Path(path) for path in clipboard_paths)
        destination_directory = Path(destination_directory)
        failures = []
        succeeded = []
        affected_directories = {destination_directory}
        operation = copy_path if mode == CLIPBOARD_COPY else move_path

        for source in clipboard_paths:
            if mode == CLIPBOARD_CUT:
                affected_directories.add(source.parent)
            try:
                operation(source, destination_directory)
            except FileOperationError as error:
                failures.append((source, str(error)))
            else:
                succeeded.append(source)

        # Refresh even after failure because a recursive/cross-filesystem
        # operation may have created a partial destination before reporting it.
        self._refresh_after_mutation(affected_directories)

        if mode == CLIPBOARD_CUT and update_cut_clipboard:
            failed_paths = {path for path, _message in failures}
            remaining_paths = tuple(
                path for path in clipboard_paths if path in failed_paths
            )
            if remaining_paths:
                self._publish_file_clipboard(CLIPBOARD_CUT, remaining_paths)
            else:
                self.clipboard_mode = None
                self.clipboard_paths = ()
                Gtk.selection_owner_set(
                    None,
                    self.clipboard_selection,
                    Gdk.CURRENT_TIME,
                )

        self._show_operation_failures(failure_title, failures)
        return tuple(succeeded), tuple(failures)

    def _on_files_dropped(self, uris, destination_directory, action,
                          drag_context, time_):
        """Execute one external/internal URI drop through safe file ops."""
        try:
            paths = parse_file_uri_list("\n".join(uris))
            if not paths:
                raise FileOperationError("Dropped file list is empty")
        except (FileOperationError, UnicodeError) as error:
            Gtk.drag_finish(drag_context, False, False, time_)
            self._show_error("Drop failed", str(error))
            return

        mode = (
            CLIPBOARD_CUT
            if action & Gdk.DragAction.MOVE
            else CLIPBOARD_COPY
        )
        succeeded, _failures = self._execute_paste(
            mode,
            paths,
            destination_directory,
            update_cut_clipboard=False,
            failure_title="Drop failed",
        )
        # The receiver has already performed MOVE itself, so the source must
        # never delete files in response to drag completion.
        Gtk.drag_finish(drag_context, bool(succeeded), False, time_)

    def _on_drag_finished(self, paths):
        """Refresh sources after another application accepted a MOVE drag."""
        self._refresh_after_mutation({Path(path).parent for path in paths})

    def _rename_operation_target(self, column):
        """Prompt for and rename exactly one focused-column target."""
        targets = column.get_operation_paths()
        if not targets:
            return
        if len(targets) != 1:
            self._show_error(
                "Rename unavailable",
                "Select or mark exactly one item to rename."
            )
            return

        source = targets[0]
        new_name = self._prompt_for_name("Rename", source.name)
        if new_name is None:
            return
        try:
            destination = rename_path(source, new_name)
        except FileOperationError as error:
            self._show_operation_failures(
                "Rename failed", [(source, str(error))]
            )
            return

        self.clipboard_paths = tuple(
            destination if path == source else path
            for path in self.clipboard_paths
        )
        self._refresh_after_mutation(
            {source.parent}, replacements={source: destination}
        )

    def _trash_operation_targets(self, column):
        """Move focused-column targets to Trash without delete fallback."""
        targets = column.get_operation_paths()
        if not targets:
            return

        failures = []
        succeeded = []
        affected_directories = set()
        for target in targets:
            try:
                trash_path(target)
            except FileOperationError as error:
                failures.append((target, str(error)))
            else:
                succeeded.append(target)
                affected_directories.add(target.parent)

        if succeeded:
            succeeded_set = set(succeeded)
            self.clipboard_paths = tuple(
                path for path in self.clipboard_paths
                if path not in succeeded_set
            )
            if not self.clipboard_paths:
                self.clipboard_mode = None
            self._refresh_after_mutation(affected_directories)

        self._show_operation_failures("Move to Trash failed", failures)

    def _create_new_folder(self, column):
        """Prompt for and create a folder in the operation destination."""
        destination_directory = column.get_operation_destination()
        name = self._prompt_for_name("New Folder", "New Folder")
        if name is None:
            return
        try:
            create_folder(destination_directory, name)
        except FileOperationError as error:
            self._show_operation_failures(
                "Create folder failed", [(destination_directory, str(error))]
            )
            return
        self._refresh_after_mutation({destination_directory})

    def _prompt_for_name(self, title, initial_name):
        """Show a small modal basename prompt and return text or None."""
        dialog = Gtk.Dialog(
            title=title,
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)

        entry = Gtk.Entry()
        entry.set_text(initial_name)
        entry.select_region(0, -1)
        entry.set_activates_default(True)
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        entry.set_margin_top(12)
        entry.set_margin_bottom(12)
        dialog.get_content_area().pack_start(entry, False, False, 0)
        dialog.show_all()

        response = dialog.run()
        name = entry.get_text() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return name

    def _show_operation_failures(self, title, failures):
        """Show at most one concise error dialog for a batch operation."""
        if not failures:
            return
        lines = [f"{path.name or path}: {message}" for path, message in failures[:8]]
        if len(failures) > 8:
            lines.append(f"...and {len(failures) - 8} more")
        self._show_error(title, "\n".join(lines))

    def _show_error(self, title, detail):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    def _refresh_after_mutation(self, directory_paths, replacements=None):
        """Refresh affected live columns and prune missing child branches."""
        directory_paths = {Path(path) for path in directory_paths}
        replacements = replacements or {}
        focused_column = self._get_focused_column()
        focused_active_changed = False

        index = 0
        while index < len(self.columns_container.columns):
            column = self.columns_container.columns[index]
            if not column.path.is_dir():
                if index > 0:
                    self.columns_container.remove_columns_after(
                        self.columns_container.columns[index - 1]
                    )
                break
            if column.path in directory_paths:
                active_changed = column.refresh(replacements)
                if column is focused_column:
                    focused_active_changed = active_changed
            index += 1

        if (focused_active_changed and
                focused_column in self.columns_container.columns):
            active_item = focused_column.get_active_item()
            self.columns_container.remove_columns_after(focused_column)
            self.columns_container.reserve_child_slot(focused_column)
            if active_item is not None and active_item.is_dir:
                self.columns_container.add_column(active_item.path)

        if self.columns_container.columns:
            self.current_path = self.columns_container.columns[-1].path
            self._update_path_bar()

        if focused_column in self.columns_container.columns:
            active_item = focused_column.get_active_item()
            self.preview_panel.update(active_item)
            if active_item is not None and not active_item.is_dir:
                self.columns_container.show_file_preview(
                    focused_column, active_item.path
                )
            focused_column.grab_navigation_focus()
        else:
            self.preview_panel.clear()

    def _focus_previous_column(self, column, keyval):
        """Moves focus left without changing selections or navigation state."""
        try:
            index = self.columns_container.columns.index(column)
        except ValueError:
            return
        if index > 0:
            # Keep the source column as the parent's immediate child, but drop
            # its own child/lookahead branch before moving focus left.
            self.columns_container.remove_columns_after(column)
            target = self.columns_container.columns[index - 1]
            self._request_column_focus(target, keyval, select_first=False)

    def _request_column_focus(self, column, keyval, select_first):
        """Replace the one focus target awaiting its matching key release."""
        if self.pending_focus_source_id is not None:
            GLib.source_remove(self.pending_focus_source_id)
        self.pending_focus_column = column
        self.pending_focus_keyval = keyval
        self.pending_focus_select_first = select_first
        self.pending_focus_source_id = GLib.idle_add(
            self._apply_pending_column_focus,
            True,
            priority=GLib.PRIORITY_HIGH,
        )

    def _apply_pending_column_focus(self, from_idle=False):
        """Apply and clear the sole pending focus transition."""
        source_id = self.pending_focus_source_id
        if source_id is not None and not from_idle:
            GLib.source_remove(source_id)
        column = self.pending_focus_column
        select_first = self.pending_focus_select_first
        self.pending_focus_column = None
        self.pending_focus_keyval = None
        self.pending_focus_source_id = None
        self.pending_focus_select_first = False
        if column in self.columns_container.columns:
            self.columns_container.reserve_child_slot(column)
            column.grab_navigation_focus(select_first=select_first)
            if select_first:
                self._restore_active_child_column(column)
            self._synchronize_column_context(column)
        return False

    def _restore_active_child_column(self, column):
        """Restore the lookahead for an unchanged ACTIVE directory."""
        item = column.get_active_item()
        if item is None or not item.is_dir:
            return
        try:
            index = self.columns_container.columns.index(column)
        except ValueError:
            return

        child_index = index + 1
        if child_index < len(self.columns_container.columns):
            child = self.columns_container.columns[child_index]
            if child.path == item.path:
                return
        self.columns_container.remove_columns_after(column)
        self.columns_container.add_column(item.path)

    def _synchronize_column_context(self, column):
        """Make the focused column immediately drive path and preview context."""
        if column not in self.columns_container.columns:
            return
        self.current_path = column.path
        self._update_path_bar()
        self.preview_panel.update(column.get_active_item())

    def _enter_active_item(self, column, keyval=None):
        """Focuses a directory child or opens a file through existing behavior."""
        item = column.get_active_item()
        if item is None:
            return
        if not item.is_dir:
            self._on_item_activated(item)
            return

        try:
            index = self.columns_container.columns.index(column)
        except ValueError:
            return
        child_index = index + 1
        if child_index < len(self.columns_container.columns):
            child = self.columns_container.columns[child_index]
            if child.path == item.path:
                if keyval is None:
                    self.columns_container.reserve_child_slot(child)
                    child.grab_navigation_focus(select_first=True)
                    self._restore_active_child_column(child)
                    self._synchronize_column_context(child)
                else:
                    self._request_column_focus(
                        child, keyval, select_first=True
                    )
                return


class MillerColumnsApp(Gtk.Application):
    """Main application"""

    def __init__(self, start_path=None):
        super().__init__(
            application_id="org.nemo.miller-columns",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        self.start_path = start_path

    def do_activate(self):
        """Activates the application"""
        win = MillerColumnsWindow(self, self.start_path)
        win.present()

    def do_command_line(self, command_line):
        """Handles command line arguments"""
        args = command_line.get_arguments()

        if len(args) > 1:
            path = args[1]
            # Handle file:// URI
            if path.startswith('file://'):
                path = urllib.parse.unquote(path[7:])
            self.start_path = path
        else:
            # Every launcher invocation without a path starts in the user's
            # home directory, rather than reusing an earlier invocation's
            # command-line path from this long-lived Gtk.Application.
            self.start_path = None

        self.activate()
        return 0


def main():
    """Entry point"""
    start_path = None

    if len(sys.argv) > 1:
        path = sys.argv[1]
        # Handle file:// URI
        if path.startswith('file://'):
            path = urllib.parse.unquote(path[7:])
        start_path = path

    app = MillerColumnsApp(start_path)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
