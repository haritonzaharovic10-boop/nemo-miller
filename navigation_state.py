"""GTK-independent navigation state for the Miller columns view."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple


class NavigationError(ValueError):
    """Raised when an initial navigation directory cannot be used."""


class SelectionEventGate:
    """Distinguish user selection events from programmatic reconciliation.

    GTK emits ``row-selected`` for ``select_row()`` as well as for user input.
    A ColumnView owns one gate and suppresses only the calls it makes while
    restoring canonical cursor state.
    """

    def __init__(self) -> None:
        self._programmatic_depth = 0

    @property
    def user_notifications_enabled(self) -> bool:
        return self._programmatic_depth == 0

    @contextmanager
    def programmatic_change(self) -> Iterator[None]:
        self._programmatic_depth += 1
        try:
            yield
        finally:
            self._programmatic_depth -= 1


@dataclass(frozen=True)
class NavigationSnapshot:
    """An immutable, internally consistent navigation snapshot."""

    visible_directories: Tuple[Path, ...]
    cursor_paths: Tuple[Optional[Path], ...]
    active_column_index: int

    @property
    def current_directory(self) -> Path:
        return self.visible_directories[-1]

    @property
    def current_item(self) -> Optional[Path]:
        return self.cursor_paths[self.active_column_index]


class NavigationState:
    """Canonical path-chain, active-column, and per-column cursor state.

    A cursor records one item in a column. Ancestor cursors point to the next
    visible directory and are not a multi-file selection model.
    """

    def __init__(self, initial_directory) -> None:
        self._snapshot: Optional[NavigationSnapshot] = None
        self.last_error: Optional[str] = None
        if not self.navigate_to(initial_directory):
            raise NavigationError(self.last_error or "Invalid initial directory")

    @property
    def snapshot(self) -> NavigationSnapshot:
        # Construction cannot complete without a valid first snapshot.
        assert self._snapshot is not None
        return self._snapshot

    @property
    def visible_directories(self) -> Tuple[Path, ...]:
        return self.snapshot.visible_directories

    @property
    def cursor_paths(self) -> Tuple[Optional[Path], ...]:
        return self.snapshot.cursor_paths

    @property
    def active_column_index(self) -> int:
        return self.snapshot.active_column_index

    @property
    def current_directory(self) -> Path:
        return self.snapshot.current_directory

    @property
    def current_item(self) -> Optional[Path]:
        return self.snapshot.current_item

    def navigate_to(self, target) -> bool:
        """Atomically replace state with the chain for an existing directory."""
        try:
            snapshot = self._snapshot_for_directory(target)
        except NavigationError as error:
            self.last_error = str(error)
            return False

        self._snapshot = snapshot
        self.last_error = None
        return True

    def select_item(self, column_index: int, item_path, is_directory: bool) -> bool:
        """Apply a user selection from one column as one atomic transition."""
        if not 0 <= column_index < len(self.visible_directories):
            self.last_error = f"Invalid column index: {column_index}"
            return False

        directory = self.visible_directories[column_index]
        item = Path(item_path)
        if not item.is_absolute():
            item = directory / item

        if item.parent != directory:
            self.last_error = f"Item does not belong to column: {item}"
            return False

        try:
            if is_directory:
                if not item.is_dir():
                    self.last_error = f"Directory is not usable: {item}"
                    return False
            elif item.is_dir() or not (item.exists() or item.is_symlink()):
                self.last_error = f"File item is not usable: {item}"
                return False
        except OSError as error:
            self.last_error = f"Cannot inspect item {item}: {error}"
            return False

        directories = self.visible_directories[:column_index + 1]
        cursors = list(self.cursor_paths[:column_index + 1])
        cursors[column_index] = item

        if is_directory:
            directories += (item,)
            cursors.append(None)

        snapshot = NavigationSnapshot(
            visible_directories=directories,
            cursor_paths=tuple(cursors),
            active_column_index=column_index,
        )
        self._validate_snapshot(snapshot)
        self._snapshot = snapshot
        self.last_error = None
        return True

    def clear_cursor(self, column_index: int) -> bool:
        """Clear one user cursor and discard its dependent child columns."""
        if not 0 <= column_index < len(self.visible_directories):
            self.last_error = f"Invalid column index: {column_index}"
            return False

        directories = self.visible_directories[:column_index + 1]
        cursors = list(self.cursor_paths[:column_index + 1])
        cursors[column_index] = None

        snapshot = NavigationSnapshot(
            visible_directories=directories,
            cursor_paths=tuple(cursors),
            active_column_index=column_index,
        )
        self._validate_snapshot(snapshot)
        self._snapshot = snapshot
        self.last_error = None
        return True

    @classmethod
    def _snapshot_for_directory(cls, target) -> NavigationSnapshot:
        try:
            directory = Path(target).expanduser().resolve(strict=True)
            is_directory = directory.is_dir()
        except (OSError, RuntimeError) as error:
            raise NavigationError(f"Cannot resolve directory {target}: {error}") from error

        if not is_directory:
            raise NavigationError(f"Navigation target is not a directory: {directory}")

        root = Path(directory.anchor)
        directories = [root]
        current = root
        for part in directory.parts[1:]:
            current = current / part
            directories.append(current)

        directory_tuple = tuple(directories)
        snapshot = NavigationSnapshot(
            visible_directories=directory_tuple,
            cursor_paths=directory_tuple[1:] + (None,),
            active_column_index=len(directory_tuple) - 1,
        )
        cls._validate_snapshot(snapshot)
        return snapshot

    @staticmethod
    def _validate_snapshot(snapshot: NavigationSnapshot) -> None:
        directories = snapshot.visible_directories
        cursors = snapshot.cursor_paths

        if not directories:
            raise AssertionError("Navigation must contain at least one directory")
        if len(cursors) != len(directories):
            raise AssertionError("Every visible directory must have one cursor slot")
        if len(set(directories)) != len(directories):
            raise AssertionError("Visible directory paths must be unique")
        if not 0 <= snapshot.active_column_index < len(directories):
            raise AssertionError("Active column must refer to a visible directory")

        for index, directory in enumerate(directories):
            cursor = cursors[index]
            if cursor is not None and cursor.parent != directory:
                raise AssertionError("A cursor must belong to its column directory")
            if index + 1 < len(directories):
                child = directories[index + 1]
                if child.parent != directory:
                    raise AssertionError("Visible directories must form a direct path chain")
                if cursor != child:
                    raise AssertionError("Ancestor cursor must identify its child column")
