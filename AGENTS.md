# Nemo Miller Development Rules

## Repository model

- `main` is the pristine upstream baseline.
- Never commit development work directly to `main`.
- `dev` is the integration branch for accepted work.
- Implementation work must happen on task-specific branches such as `codex/<task>`.

## System safety

The stock Linux Mint Nemo installation must remain untouched.

Never:

- use `sudo`;
- install or copy project files into `/usr`;
- modify `/usr/bin/nemo`;
- modify `/usr/bin/nemo-desktop`;
- run `install.sh` unless a task explicitly authorizes it;
- copy the Nemo Python extension into `~/.local/share/nemo-python/extensions`;
- run `nemo -q`;
- kill or restart stock Nemo;
- change the default handler for `inode/directory`;
- change global MIME associations;
- modify `~/.config/mimeapps.list`;
- modify stock Nemo GSettings or dconf;
- install a `.desktop` file into user application directories unless explicitly authorized.

## Development model

The Miller application must remain runnable as a standalone GTK application.

Prefer:

- explicit separation of UI, navigation state, selection state, clipboard state and filesystem operations;
- Gio/GFile facilities for filesystem operations where practical;
- bounded and cancellable background operations;
- explicit error handling;
- non-blocking GTK main-thread behavior.

Avoid:

- shelling out for core filesystem operations when Gio provides the required semantics;
- scattered `shutil`, `os.rename`, `unlink`, etc. directly inside UI callbacks;
- blocking GTK's main thread with recursive scans or large file reads;
- hidden global state.

## Filesystem safety

Until explicitly enabled by a later task:

- do not implement permanent deletion;
- do not test destructive operations outside a dedicated test directory;
- do not overwrite existing files silently.

## Scope discipline

For each task:

1. inspect the existing architecture first;
2. state the proposed change;
3. modify only what the task requires;
4. run relevant static/tests checks;
5. report every changed file;
6. report unresolved risks and assumptions;
7. do not opportunistically refactor unrelated code.

## Git discipline

Before work:

- verify current branch;
- verify working tree status.

After work:

- show `git status --short`;
- show `git diff --stat`;
- summarize tests performed.

Never:

- force-push;
- rewrite accepted history;
- commit directly to `main`;
- merge into `dev` without review.
