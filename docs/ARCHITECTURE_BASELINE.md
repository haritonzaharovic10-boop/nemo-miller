# Architecture Baseline

## 1. Scope and repository state

This document characterizes the repository at commit
`3f8d346` (`docs: establish development and safety rules`) on branch
`codex/00-architecture-baseline`. The pre-work tree was clean. This is static,
read-only analysis of the complete repository; the application and integration
scripts were not run.

The repository has two Python modules, installation/removal scripts, English and
Italian READMEs, the license, `AGENTS.md`, `.gitignore`, and a tracked generated
`__pycache__/nemo_miller_columns.cpython-312.pyc`. Runtime application behavior is
almost entirely in `nemo_miller_columns.py`. The second Python module is a thin
Nemo menu-provider integration (`nemo-miller-columns-extension.py:13-76`). The
shell scripts install/remove files and restart Nemo, but are not part of the
standalone application's runtime and were not executed (`install.sh:20-120`,
`uninstall.sh:19-58`).

The source has no test suite, packaging metadata, formal model layer, or separate
controller/service modules. The only change made for this tranche is this file.

## 2. Application startup

### Standalone entry point

The executable entry point is the `if __name__ == "__main__"` block, which calls
`main()` and passes its return code to `sys.exit()`
(`nemo_miller_columns.py:1235-1251`). Startup is:

```text
shell / desktop / Nemo extension
        |
        v
main() -- parse argv[1] --> MillerColumnsApp(start_path)
        |                         |
        +---- app.run(argv) ------+
                                  v
                         do_command_line()
                         parse argv[1] again
                                  |
                                  v
                              activate()
                                  |
                                  v
                         do_activate()
                                  |
                                  v
                    MillerColumnsWindow(app, path)
```

`main()` recognizes only the first positional argument. It strips a literal
`file://` prefix and percent-decodes the remainder; otherwise it passes the
string unchanged (`nemo_miller_columns.py:1235-1247`). `MillerColumnsApp` uses
application ID `org.nemo.miller-columns` and
`Gio.ApplicationFlags.HANDLES_COMMAND_LINE` (`nemo_miller_columns.py:1205-1213`).
Consequently `do_command_line()` receives the arguments, repeats the same first
argument conversion, stores the path in `self.start_path`, and activates the
application (`nemo_miller_columns.py:1220-1232`). Additional arguments and URI
authorities/schemes are not handled.

`do_activate()` always constructs and presents a new `MillerColumnsWindow`; it
does not look for or reuse an existing window (`nemo_miller_columns.py:1215-1218`).
The window constructor:

1. sets a 1200x700 default and initializes `current_path` to the supplied path or
   `Path.home()`;
2. creates search state and the shared `SearchEngine`;
3. installs application-wide CSS;
4. builds the toolbar, content stack, Miller container, search view, and preview;
5. calls `_navigate_to(current_path)`;
6. attaches the window-level key handler and calls `show_all()`
   (`nemo_miller_columns.py:801-861`).

There is no `do_startup()`, `do_shutdown()`, window delete/destroy handler, or
explicit resource cleanup. Normal GTK/GApplication window lifecycle therefore
controls shutdown. A search thread is daemonized, but no close-time cancellation
or join is performed and queued GLib callbacks are not withdrawn
(`nemo_miller_columns.py:1127-1146`). Child processes opened by the application
are not retained or supervised.

### Nemo integration entry point

Nemo discovers `MillerColumnsExtension`, a `GObject.GObject` /
`Nemo.MenuProvider` (`nemo-miller-columns-extension.py:13-19`). It supplies a menu
item for one selected local directory and for a local folder background
(`nemo-miller-columns-extension.py:35-76`). Activation launches
`python3 ~/.local/share/nemo-miller-columns/nemo_miller_columns.py <folder>` with
`subprocess.Popen` (`nemo-miller-columns-extension.py:21-26`). URI conversion is
again a literal `file://` strip plus percent-decoding; non-file URIs are rejected
(`nemo-miller-columns-extension.py:28-33`). The standalone application itself
does not depend on the extension.

## 3. Component map

| Component | Responsibility and collaborators |
|---|---|
| `FileItem` (`nemo_miller_columns.py:25-61`) | Lightweight, non-dataclass snapshot of a `Path`, display name, `is_dir`, and `is_symlink`. Chooses icons from filename-guessed MIME types. `is_symlink` is recorded but never consumed. |
| `ColumnView` (`nemo_miller_columns.py:64-183`) | GTK vertical box containing one scrollable, single-selection `Gtk.ListBox`. Synchronously enumerates one directory, builds rows, forwards selection with `(column, item)` and activation with `item`, and can select a row by path. |
| `ResizeHandle` (`nemo_miller_columns.py:186-247`) | GTK event box/separator that tracks pointer drag state and reports horizontal deltas. The container owns the width policy. |
| `PreviewPanel` (`nemo_miller_columns.py:250-400`) | GTK view that synchronously renders an icon, name, guessed type, size/item count, modification time, parent path, and optional scaled image for the last item passed to `update()`. |
| `SearchResult` (`nemo_miller_columns.py:403-409`) | Dataclass value carrying result path, name, directory flag, and `name`/`content` match type. |
| `SearchEngine` (`nemo_miller_columns.py:412-509`) | Recursive `os.walk` generator for case-insensitive name and selected text-content matches. Owns one shared Boolean cancellation flag. It is not a GTK object. |
| `SearchResultsView` (`nemo_miller_columns.py:512-648`) | GTK result list and status/spinner. Owns rendered result rows and count; forwards row activation. It has no selection-change callback or preview integration. |
| `MillerColumnsContainer` (`nemo_miller_columns.py:651-795`) | GTK horizontal box and concrete owner/coordinator of `ColumnView` and `ResizeHandle` widgets. Stores ordered columns, handles, and width state; mediates selection callbacks and equal/manual sizing. |
| `MillerColumnsWindow` (`nemo_miller_columns.py:798-1202`) | Central UI composition root and de facto controller. Owns navigation path, widgets, preview coordination, subprocess launch policy, keyboard shortcuts, debounce, worker thread, and search mode. |
| `MillerColumnsApp` (`nemo_miller_columns.py:1205-1232`) | GApplication identity, command-line handling, and window construction. |
| `MillerColumnsExtension` (`nemo-miller-columns-extension.py:13-76`) | Optional Nemo-side launcher adapter. It has no shared in-process state with the standalone application. |

The dependency direction is predominantly `MillerColumnsWindow` -> GTK views ->
callback back to `MillerColumnsWindow`. Domain state and UI state are not
separated.

## 4. Navigation state and flow

### Represented state

- **Current directory:** `MillerColumnsWindow.current_path` is the path used by
  the path bar, search root, Back, “Open in Nemo,” and terminal actions
  (`nemo_miller_columns.py:805`, `983-1027`, `1058-1089`, `1127-1130`). During
  normal item selection it means selected directory, or selected file's parent
  (`nemo_miller_columns.py:1029-1038`). It is not guaranteed to be a directory:
  `_navigate_to()` accepts any existing path and finally assigns it unchanged.
- **Visible columns:** authoritative widget/order storage is
  `MillerColumnsContainer.columns`, with each `ColumnView.path` identifying the
  directory it lists (`nemo_miller_columns.py:659-680`).
- **Selected item:** there is no window/model field. Selection lives in each
  `Gtk.ListBox`; the most recently emitted `FileItem` is passed transiently to
  callbacks and the preview (`nemo_miller_columns.py:166-174`, `1029-1040`).
- **Parent/child relationship:** inferred from ordered columns and filesystem
  paths. Neither `FileItem` nor `ColumnView` stores explicit parent/child links.
- **History:** absent. “Back” is strictly `current_path.parent`, so it is an Up
  action rather than chronological history (`nemo_miller_columns.py:1058-1061`).
- **Active/focused column:** absent as application state. GTK owns focus. The code
  never records an active column or explicitly transfers focus when adding one.

### Item control flow

```text
Gtk.ListBox row-selected
  -> ColumnView._on_row_selected(column, row)
  -> MillerColumnsContainer._on_item_selected(column, item)
  -> MillerColumnsWindow._on_item_selected(column, item)
       -> remove all columns right of source column
       -> directory: add child column; current_path = item.path
          file:      no child;         current_path = item.path.parent
       -> rebuild path bar
       -> update preview
  -> schedule width distribution
```

Exact flows requested:

**A. Selecting a directory.** `ColumnView._on_row_selected()` forwards the row's
`FileItem` (`nemo_miller_columns.py:166-170`). The container forwards it and later
redistributes widths (`nemo_miller_columns.py:722-725`). The window removes stale
right-side columns, constructs/populates a child column for the directory, sets
`current_path` to that directory, rebuilds the path bar, and updates the preview
(`nemo_miller_columns.py:1029-1040`). A single selection is therefore the actual
“navigate deeper” operation.

**B. Activating a directory.** Row activation flows through
`ColumnView._on_row_activated()` to `MillerColumnsWindow._on_item_activated()`
(`nemo_miller_columns.py:171-174`, `1042-1057`). That window method acts only
when `not item.is_dir`; directory activation itself is a no-op. In normal pointer
use, selection may already have expanded the directory before the double-click.

**C. Navigating deeper.** This is identical to A: selecting a directory replaces
everything to the right and adds its listing. There is no separate navigation
controller or forward-history operation.

**D. Navigating back/up.** The toolbar Back button and a non-search-entry
Backspace call `_on_go_back()`, which invokes `_navigate_to(current_path.parent)`
unless already at the root (`nemo_miller_columns.py:940-944`, `1058-1061`,
`1195-1200`). Home and path-bar buttons also call `_navigate_to()`
(`nemo_miller_columns.py:946-950`, `995-999`, `1063-1069`). `_navigate_to()`
destroys all columns and rebuilds the absolute path from the filesystem root
(`nemo_miller_columns.py:1004-1027`).

**E. Selecting a file.** The same selection chain removes right-side columns,
sets `current_path` to the file's parent, rebuilds the path bar, and synchronously
previews the file (`nemo_miller_columns.py:1029-1040`). The selected file is not
stored outside its source listbox row and preview widgets.

**F. Activating a file.** `row-activated` reaches `_on_item_activated()`, which
starts `xdg-open <path>` (`nemo_miller_columns.py:1042-1057`). Search-result file
activation takes a separate route: exit search, rebuild the parent directory,
then independently starts `xdg-open <result.path>`
(`nemo_miller_columns.py:1159-1176`).

### Re-entrant path reconstruction defect

`_navigate_to()` intends to add the root, select each next component in the
previous column, and add a column for that component (`nemo_miller_columns.py:1013-1024`).
However, `ColumnView.select_path()` calls `Gtk.ListBox.select_row()`
(`nemo_miller_columns.py:176-183`); the resulting `row-selected` callback already
calls `MillerColumnsWindow._on_item_selected()`, which adds the child column.
`_navigate_to()` then adds the same child again explicitly. For `/home/user`, the
effective sequence is:

```text
add "/"
select "/home" -> callback adds "/home"
                 -> loop adds "/home" again
select "/home/user" in the second "/home" -> callback adds "/home/user"
                                           -> loop adds "/home/user" again
```

Thus a multi-component `_navigate_to()` rebuild can produce semantically
duplicate columns. The parallel arrays remain numerically consistent, but the
visible column chain and `current_path` are not a reliable single navigation
model. This affects startup, Back/Up, Home, path-bar navigation, and search-result
navigation.

## 5. Miller column lifecycle

The normal selection-driven lifecycle is:

```text
directory FileItem selected
  -> remove_columns_after(source)
       -> pop rightmost ColumnView; remove; destroy
       -> pop matching width
       -> pop rightmost ResizeHandle; remove; destroy
  -> add_column(selected path)
       -> ColumnView.__init__ -> populate synchronously
       -> create preceding ResizeHandle if a column already exists
       -> append column and -1 auto width
       -> pack/show widgets
       -> schedule width distribution
```

`MillerColumnsContainer` owns Python references in three parallel arrays and GTK
container ownership of the actual widgets (`nemo_miller_columns.py:654-661`).
`add_column()` constructs `ColumnView`; its constructor calls `populate()` before
the new widget is packed (`nemo_miller_columns.py:69-95`, `665-686`). A handle is
stored immediately before each non-first column. Its `column_index` is the index
of the left column (`nemo_miller_columns.py:669-675`).

Obsolete branches are removed from the right by `remove_columns_after()`. Each
column and corresponding trailing handle is removed from the box and destroyed;
its width entry is popped (`nemo_miller_columns.py:688-708`). `clear()` removes
and destroys every column and handle and clears all arrays
(`nemo_miller_columns.py:710-720`). There is no monitor, worker, signal-disconnect,
or per-column non-widget resource to cancel today.

Implicit invariants are:

- `len(column_widths) == len(columns)`;
- `len(handles) == max(0, len(columns) - 1)`;
- handle `i` separates columns `i` and `i + 1`, and has `column_index == i`;
- columns are expected to be an ordered ancestor-to-child path chain;
- a selected directory in column `i` is expected to equal `columns[i+1].path`.

The implementation explicitly maintains the first three during append/right-pop.
The re-entrant `_navigate_to()` behavior violates the semantic path-chain
expectations. Column objects and their widget selections are authoritative for
display, while `current_path` separately drives global actions. Neither is fully
derived from the other.

Width state is `-1` for auto-sized columns or an integer after handle dragging.
Auto columns share remaining allocation subject to a 100-pixel minimum
(`nemo_miller_columns.py:727-758`). Dragging fixes both adjacent widths
(`nemo_miller_columns.py:760-795`). Rebuilding navigation discards all widths.

## 6. Selection model

Both normal columns and search results use `Gtk.SelectionMode.SINGLE`
(`nemo_miller_columns.py:85-89`, `542-545`). Multiple selection does not exist
within a directory. Several visible columns can each retain a selected row, but
that represents the visual ancestry chain, not a multi-file selection.

There is no independent `SelectionState`, selected-path collection, anchor,
cursor, active-column index, or cut marker. Normal-list selection immediately
causes navigation/preview side effects. Search-list selection causes no callback;
only result activation is observed (`nemo_miller_columns.py:543-545`, `645-648`).
Keyboard focus remains GTK widget state and is not synchronized to a logical
selection. Adding a child does not focus that child.

Consequences for planned commands:

- **Ctrl+C / Ctrl+X:** no handlers, clipboard model, or unambiguous window-level
  selection source exists.
- **Ctrl+A:** no handler, and `SINGLE` prevents selecting all sibling rows.
- **Shift selection:** there is no multiple/range mode or anchor.
- **Multi-file operations:** no collection of targets or operation layer exists.
- **Keyboard movement:** GTK can move a focused listbox's selected row, but the
  resulting selection immediately rebuilds the child branch; there is no explicit
  active-column transfer or selection restoration policy.

Before filesystem commands are added, target selection must be made explicit so
an operation cannot accidentally infer targets from the wrong column's retained
GTK selection.

## 7. Keyboard handling

The only application-defined key-event handler is the window's
`key-press-event` connection to `_on_key_press()`
(`nemo_miller_columns.py:860`, `1178-1202`). SearchEntry and GtkListBox retain
their normal GTK key behavior; no other source-level shortcut or accelerator is
registered.

Current explicit shortcuts are:

| Input | Current behavior |
|---|---|
| Ctrl+F | Calls `search_entry.grab_focus()` and consumes the event. It checks `KEY_f`; there is no explicit handling for shifted `F` (`nemo_miller_columns.py:1180-1184`). |
| Escape | If `search_mode`, calls `_exit_search_mode()`; otherwise closes the window (`nemo_miller_columns.py:1186-1193`). The search entry's `stop-search` signal independently calls the same exit method (`nemo_miller_columns.py:966`, `1108-1110`). |
| Backspace | Calls parent navigation and consumes the event unless the search entry itself has focus (`nemo_miller_columns.py:1195-1200`). |
| Enter | Not explicitly handled. A focused GtkListBox may produce `row-activated`; files open, while the application's directory activation callback does nothing. In the search list, activation invokes result navigation/opening. |
| Up/Down | Not explicitly handled; behavior is GtkListBox's default only when that widget owns focus. Selection changes trigger the normal immediate branch rebuild. |
| Left/Right | Not explicitly mapped to Miller parent/child movement. Any behavior is GTK focus/navigation default, not application navigation semantics. |

The narrowest future insertion point for keyboard-first Miller navigation is the
existing window-level `_on_key_press()` dispatch, because it can see both search
mode/focus and all columns. It should delegate to navigation/selection actions
rather than directly mutate widgets. Those actions need an explicit active column
and selected path first. Up/Down should change selection in that column, Right
should enter/focus the child for a selected directory, Left should return focus to
the parent while preserving the path chain, Enter should activate the logical
selection, and Backspace should retain deliberate Up/history semantics. SearchEntry
editing must remain an early exclusion. No keyboard behavior is changed here.

## 8. File activation/opening

Normal file activation uses `subprocess.Popen(['xdg-open', str(item.path)])`
(`nemo_miller_columns.py:1042-1047`). This delegates MIME/default-application
choice to the desktop's `xdg-open` behavior. The application does not use
`Gio.File`, `Gio.AppInfo`, `Gtk.show_uri`, detected content types, or URI-aware
launch APIs. Paths are passed as local path strings.

The `try` catches only failure to start the process. A later nonzero exit or an
`xdg-open` association failure is neither observed nor presented. Immediate
launch exceptions use a modal GTK error dialog (`nemo_miller_columns.py:1047-1057`).
The duplicate search-result path uses the same command but reports only to stdout
(`nemo_miller_columns.py:1171-1176`).

The clean future insertion point for a private MIME policy is one injected
`OpenerPolicy`/`FileOpener` service called from both `_on_item_activated()` and
`_on_search_result_activated()`. It should accept a path/GFile, determine an
actual content type, map private overrides such as JSON and Rust to the chosen
IDE, and fall back to a Gio default-app launch. UI error presentation should stay
with the window. This consolidates policy without coupling it to `ColumnView` or
search results.

Two adjacent launch actions are not file activation policy: “Open in Nemo” starts
`nemo <current_path>` (`nemo_miller_columns.py:1071-1076`), while terminal launch
tries four executables with a `--working-directory` argument
(`nemo_miller_columns.py:1078-1089`). Neither validates that `current_path` still
exists and is a directory.

## 9. Filesystem operations

### Application and extension runtime

The runtime Python code implements read/enumerate/open behavior, but no direct
filesystem mutation:

- `Path.is_dir()` / `is_symlink()` snapshot item type
  (`nemo_miller_columns.py:28-32`).
- `Path.iterdir()` enumerates columns and counts directory preview children
  (`nemo_miller_columns.py:97-129`, `322-333`).
- `Path.stat()` reads preview metadata and enforces the search content-size limit
  (`nemo_miller_columns.py:323-343`, `484-489`).
- `Path.resolve()`, `exists()`, `parts`, `parent`, and `home()` support navigation
  (`nemo_miller_columns.py:805`, `983-1027`, `1058-1069`).
- `os.walk()` recursively enumerates search roots
  (`nemo_miller_columns.py:424-482`).
- built-in `open(..., 'r', encoding='utf-8', errors='ignore')` reads candidate
  search content (`nemo_miller_columns.py:503-506`).
- `os.path.expanduser()` computes the installed application path in the extension
  (`nemo-miller-columns-extension.py:16-20`).
- `subprocess.Popen()` launches Python/xdg-open/Nemo/terminals; these are process
  launches, not application-implemented file operations
  (`nemo-miller-columns-extension.py:21-26`,
  `nemo_miller_columns.py:1042-1089`, `1171-1176`).

There is no `shutil` import/use and no runtime use of `Gio.File`, `unlink`,
filesystem `remove`, rename, copy, move, mkdir, or trash APIs. Calls such as
`self.listbox.remove()` and `self.remove(col)` remove GTK children only
(`nemo_miller_columns.py:99-100`, `688-720`).

Current feature support is therefore:

| Capability | Support |
|---|---|
| Copy / cut / paste | Absent |
| Rename | Absent |
| Create directory | Absent |
| Trash | Absent |
| Permanent delete | Absent |
| Multi-file operations | Absent |
| Clipboard integration/state | Absent |
| Drag and drop | Absent; resize-handle pointer dragging is unrelated |

### Repository scripts

The non-runtime `install.sh` creates user directories, copies application and
extension files, creates a desktop launcher, can invoke `sudo apt`, updates the
desktop database, and runs `nemo -q` (`install.sh:25-103`). `uninstall.sh`
permanently removes those installed files/directories and runs `nemo -q`
(`uninstall.sh:24-58`). These scripts contain mutations forbidden for this
tranche and were only read. The READMEs describe the same operations. None of
these script operations are available from the running Miller window.

## 10. Filesystem refresh and monitoring

`ColumnView.populate()` is called only by `ColumnView.__init__()`
(`nemo_miller_columns.py:69-95`). It first clears existing rows, then synchronously
enumerates and rebuilds them (`nemo_miller_columns.py:97-129`), but no application
code calls `populate()` on an existing column.

There is no `Gio.FileMonitor`, `GFileMonitor`, inotify binding, polling loop,
refresh command, or refresh button. Directory data is repopulated only when a new
`ColumnView` is constructed—during initial/path rebuild or after selecting a
directory.

Therefore, if an external process runs:

```text
touch current_directory/foo.txt
```

while that directory's `ColumnView` remains open, `foo.txt` will **not** appear
automatically. It can appear only after a navigation action destroys and later
recreates that column (or an otherwise unused direct call to `populate()`). The
existing `FileItem` type flags and preview metadata are snapshots and also remain
stale.

The narrow insertion point is a `DirectoryMonitor` owned per live `ColumnView`
path, with lifecycle coordinated by `MillerColumnsContainer.add_column()`,
`remove_columns_after()`, and `clear()`. A Gio directory monitor should coalesce
bursts, schedule GTK row reconciliation on the main loop, preserve logical
selection, and be cancelled before its column is destroyed. Enumeration and
monitoring should be separable so refresh can be tested without GTK.

## 11. Preview subsystem

The data flow is:

```text
normal column row-selected
  -> window _on_item_selected(item)
  -> PreviewPanel.update(item)                 [GTK main thread]
       -> FileItem.get_icon(64)
       -> guessed MIME type
       -> directory: iterate/count immediate children
          file: stat byte size
       -> stat modification time
       -> show parent path
       -> if guessed image/*:
            GdkPixbuf.new_from_file_at_scale(..., 250, 250, preserve_aspect=True)
```

Every selected file/directory gets metadata and an icon. Only filename-guessed
`image/*` files get content preview (`nemo_miller_columns.py:295-350`, `375-392`).
There is no text, audio, video, PDF, or directory-content thumbnail preview.

All preview work is synchronous on the GTK main thread. Directory item counting
has no entry bound (`nemo_miller_columns.py:323-326`). Regular-file metadata has
no special byte limit. Image output is constrained to 250x250 pixels, but the
input file byte size and source dimensions are not checked before synchronous
decoder invocation (`nemo_miller_columns.py:381-387`). Large/slow directories,
slow mounts, metadata calls, and image decode can therefore stall the UI.

Metadata `PermissionError`/`OSError` is silently omitted
(`nemo_miller_columns.py:322-343`). Any image exception is silently converted to
a hidden image pane (`nemo_miller_columns.py:383-390`). Icon errors fall through
to a generic icon or `None` (`nemo_miller_columns.py:34-61`). If `get_icon()`
returns `None`, `update()` does not clear the prior icon. Hiding a failed or
non-image preview does not clear its prior pixbuf. More visibly, `_navigate_to()`
never calls `preview_panel.clear()`, so Back/Home/path/search-result navigation can
leave metadata for an item from the destroyed prior column. Because work is
synchronous there is no current async completion race, but this retained-widget
state is already a stale-preview risk. Any future async implementation will need
a selection/request generation token.

## 12. Search subsystem

### Lifecycle

The search root is `MillerColumnsWindow.current_path` at `_start_search()` time,
not a separately modeled location (`nemo_miller_columns.py:1126-1131`). The flow
is:

```text
SearchEntry search-changed
  -> cancel prior 300 ms GLib timeout
  -> empty: exit search
     nonempty: GLib.timeout_add(300, _start_search, query)
  -> _start_search
       -> cancel shared engine; join old thread up to 0.5 s
       -> show search stack; clear results; start spinner
       -> daemon threading.Thread(root=current_path, query)
  -> SearchEngine.search
       -> os.walk recursively, pruning hidden directory names
       -> directory/file substring name matches
       -> otherwise eligible file content match
       -> generator yields SearchResult
  -> worker schedules each add_result with GLib.idle_add
  -> worker finally schedules stop_search with GLib.idle_add
```

The debounce is 300 ms (`nemo_miller_columns.py:1091-1106`). Recursive traversal
uses `os.walk`, does not follow symlinked directories by default, prunes hidden
directory names, and skips hidden filenames (`nemo_miller_columns.py:424-482`).
Traversal order is not explicitly sorted.

Content search is limited to files at most 10 MiB and MIME names beginning with
one of the listed text/application prefixes (`nemo_miller_columns.py:415`,
`484-501`). MIME is inferred from the filename. Each candidate is read completely
as UTF-8 with decoding errors ignored and lowercased before substring comparison
(`nemo_miller_columns.py:503-506`). The per-file limit bounds a single read but
there is no traversal/result/time total bound.

Filesystem traversal and content reads occur in a Python daemon thread. GTK
mutations are correctly marshalled back through `GLib.idle_add`
(`nemo_miller_columns.py:1136-1146`); the worker does not directly mutate GTK.
Starting a replacement search can nevertheless block the GTK thread for up to
0.5 seconds in `join()` (`nemo_miller_columns.py:1117-1119`).

### Cancellation and races

Cancellation is one unsynchronized Boolean on the single shared `SearchEngine`
(`nemo_miller_columns.py:417-429`). It is checked between traversal/result steps,
not during `stat()` or a full-file read. More importantly, every call to
`search()` resets it to `False`. If an old worker is still alive after the
0.5-second join timeout, starting a new worker clears the cancellation intended
for the old worker. Both can then yield through the same view.

There is no search ID/generation attached to results or completion callbacks.
Already queued idle callbacks survive cancellation and `_exit_search_mode()`.
Consequences include:

- old results appearing in a new search or after the result view was cleared;
- an old `stop_search()` stopping the spinner and setting status during a newer
  search;
- two workers racing over the shared `cancelled` flag;
- callbacks remaining queued while a window is closing.

`_exit_search_mode()` requests cancellation, switches stacks, clears the entry
and result view, but does not join the worker or explicitly remove queued idle
sources (`nemo_miller_columns.py:1148-1157`). Clearing the search entry emits
another `search-changed`, so timeout cleanup partly relies on re-entrant GTK
signal behavior rather than explicit lifecycle cleanup.

`os.walk` has no `onerror` callback, so traversal failures are skipped without UI
reporting. Expected content read/stat errors return “no match” silently
(`nemo_miller_columns.py:508-509`). Unexpected worker exceptions are not caught by
`_search_thread_func`; its `finally` schedules completion but the exception is
only a thread traceback (`nemo_miller_columns.py:1136-1146`).

## 13. Error handling

| Condition | Current handling |
|---|---|
| Permission denied while populating a directory | Per-entry `PermissionError` is skipped. A top-level `PermissionError` becomes a `Gtk.Label("Permission denied")` in the list; other exceptions become `Gtk.Label("Error: ...")` (`nemo_miller_columns.py:97-129`). No structured error state is retained. |
| Permission denied during preview | Size/count and date fields are independently omitted without feedback (`nemo_miller_columns.py:322-343`). |
| Permission/search traversal errors | `os.walk` errors are implicitly ignored because no `onerror` is supplied; content `PermissionError`/`OSError`/`UnicodeDecodeError` becomes a silent non-match (`nemo_miller_columns.py:432-509`). |
| Missing/disappearing path | `_navigate_to()` silently returns for a nonexistent target (`nemo_miller_columns.py:1004-1009`). An item disappearing after population yields omitted preview metadata/hidden image; activation still starts `xdg-open`, whose later failure is not captured. Existing columns are never automatically reconciled. |
| Broken symlink | `FileItem` records `is_symlink` but does not use it. A broken target is generally classified as non-directory and rendered/opened as a file based on its name (`nemo_miller_columns.py:28-40`). No broken-link presentation exists. |
| File-open failure | Only immediate `Popen` exceptions are caught. Normal activation shows a modal dialog; search activation prints. Child exit status/stderr is ignored (`nemo_miller_columns.py:1042-1057`, `1171-1176`). |
| Image/icon failure | Broad exceptions are silently replaced by generic/no icon or a hidden preview (`nemo_miller_columns.py:34-61`, `381-392`, `598-603`). |
| Search failure | Expected file errors become non-matches; traversal errors are implicit; unexpected thread errors do not reach the UI, although `finally` queues normal completion (`nemo_miller_columns.py:484-509`, `1136-1146`). |
| Invalid input path | Literal `file://` stripping is not full URI parsing. `_navigate_to()` resolves without a surrounding exception handler, silently returns when `exists()` is false, and does not require a directory (`nemo_miller_columns.py:1004-1027`, `1220-1247`). A missing startup target can leave an empty content/path view; a file target can make `current_path` a file. |
| Nemo-extension launch/URI errors | Non-local URI yields no menu item; immediate subprocess error is printed only (`nemo-miller-columns-extension.py:21-33`). |

Broad `except Exception` occurs in icon selection, column population, image
preview, process launch, and result icon loading. Several paths either silently
swallow the exception or directly expose `str(e)` in a label/dialog without a
consistent error domain or recovery action.

## 14. Architectural risks

The priorities below reflect concrete current code and the risk of building a
keyboard-first daily-use manager on it.

### CRITICAL

1. **Navigation has no single source of truth and path rebuild is re-entrant.**
   `current_path`, ordered columns, and per-widget selections can diverge, and
   `_navigate_to()` can create duplicate semantic columns as described in section
   4. Keyboard actions or future file operations cannot safely infer active
   location/target until this is corrected.
2. **There is no explicit operation target selection.** Each column retains its
   own GTK single selection, but no active column/current item exists. Adding
   copy/cut/rename/trash against this state risks operating on an unintended path.
   This is a critical prerequisite risk, not evidence of current data loss—the
   current application has no mutation commands.

### HIGH

1. **Search generations are not isolated.** A replacement search can revive an
   older worker through the shared Boolean and accept stale results/completion.
2. **Displayed filesystem state never refreshes.** External creates, removes,
   renames, and metadata changes leave columns and `FileItem` snapshots stale.
3. **Directory enumeration and preview work block GTK.** Large/slow directories,
   per-row icon loads, directory counting, stat calls, and image decode all run
   synchronously with no cancellation.
4. **Search/window shutdown is incomplete.** Daemon workers and queued callbacks
   outlive view mode and can target a closing window; no close-time cleanup exists.
5. **Path validation is incomplete.** Missing paths fail silently and files are
   accepted as navigation locations, allowing global actions/search roots to
   receive a non-directory or stale `current_path`.

### MEDIUM

1. File activation is duplicated, local-path-only, subprocess-based, and detects
   only process-spawn errors; normal and search UI error behavior differs.
2. `FileItem` type data and preview metadata are snapshots with no refresh or
   structured error state; broken links are not distinguished in behavior.
3. Search reads each eligible file in full, cancellation is coarse, MIME is
   filename-guessed, and traversal/result totals are unbounded.
4. Preview state is not cleared during whole-path navigation and can describe a
   destroyed selection.
5. “Back” is labeled historically but is only parent navigation; no explicit
   history semantics exist.
6. Column and search error labels/results are UI constructs rather than typed
   data, making recovery, refresh, and test coverage difficult.

### LOW

1. The README describes Enter/click on a folder as activation-based navigation,
   while source behavior expands on selection and directory activation is a no-op
   (`README.md:97-105`; `nemo_miller_columns.py:1029-1057`).
2. The installed application path is hard-coded in the Nemo extension rather
   than discovered/configured (`nemo-miller-columns-extension.py:16-24`).
3. A generated CPython 3.12 `.pyc` is tracked despite `__pycache__` being ignored,
   creating version/platform and review noise; it does not define the documented
   source architecture.
4. Child launcher processes are not retained or observed, so asynchronous launch
   errors and lifecycle are invisible.

## 15. Recommended component boundaries

These are narrow seams suggested by current responsibilities, not a recommendation
for a wholesale rewrite.

### Navigation and selection model/controller

The first useful extraction is a small, GTK-independent state object containing:

- canonical current directory;
- ordered directory chain;
- active column index;
- cursor/selected paths per column;
- deliberate Up versus history semantics.

`MillerColumnsWindow._navigate_to()`, `_on_item_selected()`, `_on_go_back()`,
`_on_go_home()`, and `_on_path_button_clicked()` are the current insertion points
(`nemo_miller_columns.py:1004-1069`). A thin controller can translate model
transitions into idempotent container reconciliation. It should have a way to
select rows without treating programmatic restoration as a new navigation
request. Calling this combined seam `NavigationState` initially may be clearer
than creating separate `NavigationController` and `SelectionState` classes before
their responsibilities actually diverge.

Multi-selection for operations is a distinct concern from the per-column cursor.
When introduced, `SelectionState` should explicitly distinguish the active
directory's selected target set, cursor, and range anchor from the ancestor rows
that encode the visible Miller chain.

### Keyboard controller

Keep event receipt at `MillerColumnsWindow._on_key_press()`
(`nemo_miller_columns.py:1178-1202`) and extract only key-to-command mapping plus
focus exclusions. Commands should call navigation/selection actions. Direct
knowledge of listbox row layout should remain in a view adapter. This keeps the
standalone window as the composition root and avoids embedding application
shortcuts independently in every `ColumnView`.

### Clipboard and file operations

An in-memory `ClipboardState` should hold operation kind (`copy`/`cut`) and an
immutable list of source GFiles/URIs. A separate `FileOperationController` should
own Gio copy/move/mkdir/rename/trash requests, collision policy, progress,
cancellation, and structured outcomes. UI callbacks should submit operations and
render outcomes; they should not perform `Path`/`shutil` mutation directly. These
components have no current insertion point because no commands exist; inject them
at window construction before binding future actions. Do not add permanent delete
without separate explicit authorization.

### Directory enumeration and monitoring

Split directory enumeration from `ColumnView.populate()` into a provider that
returns row data/errors. A `DirectoryMonitor` can then be scoped to each live
column and feed the same reconciliation API. Container add/removal methods are
the monitor lifecycle boundary (`nemo_miller_columns.py:665-720`); GTK updates
must remain on the main thread and preserve model selection.

### Opener policy

One `FileOpener`/`OpenerPolicy` service should replace both `xdg-open` sites
(`nemo_miller_columns.py:1042-1057`, `1159-1176`). The policy owns content-type
overrides and fallback selection; the window owns user-visible errors. The Nemo
and terminal toolbar actions can remain separate launch commands because their
semantics are not MIME opening.

### Search controller

The current architecture also justifies a `SearchController`, even though it was
not in the suggested list. The window currently mixes debounce, thread lifecycle,
generation acceptance, mode switching, and view status
(`nemo_miller_columns.py:1091-1157`). A controller should give every request a
generation/cancellation token and publish results/errors/completion only if that
generation is current. `SearchEngine` can remain a GTK-independent producer after
its shared cancellation flag is removed.

### Preview loader

Keep `PreviewPanel` as rendering code, but move metadata/image loading behind a
bounded cancellable loader. Each request needs a generation tied to the logical
selection so a late completion cannot replace a newer preview. This is warranted
by existing synchronous blocking and stale retained state, not merely by naming
preference.

## 16. Recommended implementation sequence

The sequence should stay incremental, keep `nemo_miller_columns.py` directly
runnable after each tranche, and avoid depending on the Nemo extension:

1. **Navigation/selection foundation:** add focused tests for path-chain
   transitions, then introduce one canonical navigation state and idempotent
   column reconciliation. Correct programmatic selection re-entrancy, define
   active column/current item, validate directory inputs, and clear/reconcile
   preview state. Preserve mouse behavior as far as possible.
2. **Keyboard-first navigation:** route Up/Down/Left/Right/Enter/Backspace through
   those tested state transitions, with explicit search-entry and focus rules.
3. **Search lifecycle hardening:** add per-request tokens, bounded cancellation,
   non-blocking replacement, close-time cleanup, and structured traversal errors.
4. **Bounded preview/enumeration:** separate loading from GTK rendering and make
   slow work cancellable; preserve selection during row reconciliation.
5. **Live directory monitoring:** attach Gio monitors to live columns, coalesce
   events, and refresh through the same enumeration/reconciliation boundary.
6. **Central opener policy:** consolidate normal/search activation, add private
   content-type mappings, use Gio default-app fallback, and unify errors.
7. **Multi-selection and clipboard model:** define cursor/range semantics and
   copy/cut clipboard state without filesystem mutation first.
8. **Safe file operations:** add cancellable Gio-backed copy/move/rename/mkdir/
   trash incrementally with explicit collision/error UI and dedicated temporary
   test directories. Permanent deletion remains out of scope unless separately
   authorized.

The recommended next tranche is item 1: a non-destructive navigation/selection
foundation with tests. Keyboard shortcuts and filesystem operations should not be
layered onto the current ambiguous, re-entrant widget state.
