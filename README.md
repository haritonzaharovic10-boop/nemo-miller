# Miller Columns

A keyboard-friendly GTK 3 file manager built around the Miller columns layout.
It runs as a standalone application and can optionally become the current
user's default directory handler on Linux desktops.

This project began as a fork of
[linux-nemo-miller-columns](https://github.com/davemin/linux-nemo-miller-columns).

*[Leggi in Italiano](README_IT.md)*

## Features

- Stable, resizable Miller columns with directory look-ahead.
- Native Up/Down navigation and explicit Left/Right column navigation.
- Separate active item and marked-item states for multi-file operations.
- Space toggles marks; Shift extends a range; Ctrl+A marks a whole column.
- Copy, cut, paste, rename, new-folder and Trash operations.
- GNOME/Nemo file clipboard interoperability.
- Multi-file drag and drop both into and out of the application.
- Text and image previews in the next column, with bounded file reads.
- Metadata inspector, recursive search, breadcrumbs and terminal/Nemo actions.
- No permanent-delete command and no silent destination overwrite.

## Requirements

- Python 3.10 or newer.
- GTK 3 and PyGObject (`python3-gi` on Debian-family distributions).
- GTK/GdkPixbuf/Gio introspection data.
- `xdg-utils` only when using `install.sh --default`.
- Nemo and `nemo-python` are optional and needed only for the legacy context
  menu extension.

Dependencies are deliberately not installed automatically. Use your Linux
distribution's package manager when one is missing.

## Run from source

```bash
python3 nemo_miller_columns.py
python3 nemo_miller_columns.py /path/to/directory
```

Running without a path opens the current user's home directory.

## User installation

Install the standalone application and menu launcher without changing the
default file manager:

```bash
./install.sh
```

To explicitly make Miller Columns the current user's default handler for
directories:

```bash
./install.sh --default
```

The installer:

- never uses `sudo`;
- never installs files into `/usr`;
- never installs or restarts the Nemo extension;
- records the previous directory handler before changing it;
- installs only below `${XDG_DATA_HOME:-~/.local/share}`.

Uninstall with:

```bash
./uninstall.sh
```

If `--default` recorded a previous directory handler, the uninstaller restores
it. Stock Nemo is never removed or modified.

## Keyboard and mouse controls

| Input | Action |
|---|---|
| `Up` / `Down` | Move the active item within the focused column |
| `Left` | Move to the previous Miller column |
| `Right` / `Enter` | Enter an active directory, or open an active file |
| `Backspace` | Navigate to the filesystem parent |
| `Space` | Toggle the active item's marked state |
| `Shift+Up/Down` | Extend marked range |
| `Ctrl+A` | Mark every item in the focused column |
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` | Copy, cut and paste |
| `F2` | Rename one active/marked item |
| `Delete` | Move active/marked items to Trash |
| `Ctrl+Shift+N` | Create a folder in the active destination |
| `Ctrl+F` | Focus search |
| `Esc` | Exit search, clear marks, then close |
| Click | Make an item active; a file becomes the sole mark |
| `Ctrl+Click` | Toggle a mark |
| `Shift+Click` | Mark a range |
| Double-click | Enter a directory or open a file |

The active item drives preview and Miller navigation. Marked items are only
operation targets, so marking several directories never creates competing
child columns.

## File-operation behavior

- A marked set is used first; otherwise the active item is the implicit target.
- Paste/new-folder targets the active directory, or the focused column's
  directory when the active item is a file or absent.
- Directories copy recursively and symlinks are preserved where practical.
- Existing destinations are rejected rather than overwritten.
- Copying/moving a directory into itself or its descendants is rejected.
- `Delete` uses Gio Trash and never falls back to permanent deletion.
- Partial cut failures retain failed paths in the clipboard.

## Known limitations

- Undo/redo is not implemented yet.
- There is no general filesystem monitor; built-in operations refresh affected
  visible columns, but external changes may need navigation to become visible.
- Recursive copy/move and some preview work can still block the GTK main thread.
- Clipboard and drag/drop accept local `file://` paths only.
- Search cancellation and stale-result delivery need further hardening.
- Desktop environments that call a file manager through a private D-Bus API
  may bypass the standard `inode/directory` association.

## Development

Run the display-free test suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Validate syntax without writing bytecode into the repository:

```bash
PYTHONPYCACHEPREFIX=/tmp/miller-columns-pycache \
python3 -m py_compile nemo_miller_columns.py \
  nemo-miller-columns-extension.py tests/*.py
```

Filesystem tests use dedicated temporary directories under `/tmp`. Do not test
mutating operations on personal data.

## Repository layout

```text
assets/                            Preview fallback artwork
nemo_miller_columns.py             Standalone GTK application
nemo-miller-columns-extension.py   Optional legacy Nemo context-menu source
tests/                              Standard-library tests
install.sh                          Safe per-user standalone installer
uninstall.sh                        Per-user uninstaller
```

The fallback preview artwork was generated specifically for this project and
is distributed under the repository's MIT license.

## License

[MIT](LICENSE). The original copyright notice is preserved.
