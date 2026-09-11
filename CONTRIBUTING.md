# Contributing

Contributions, bug reports and focused pull requests are welcome.

## Development setup

Run the application directly from the repository:

```bash
python3 nemo_miller_columns.py /tmp
```

Do not install the Nemo extension or replace your system file manager merely
to test a change. Standalone execution is the supported development path.

## Checks

Before submitting a pull request, run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

PYTHONPYCACHEPREFIX=/tmp/miller-columns-pycache \
python3 -m py_compile nemo_miller_columns.py \
  nemo-miller-columns-extension.py tests/*.py

git diff --check
```

Use dedicated temporary directories under `/tmp` for tests that mutate the
filesystem. Never use personal data as a file-operation fixture.

## Scope and safety

- Keep the GTK application runnable without Nemo integration.
- Never overwrite existing files silently.
- `Delete` must remain Trash-only unless permanent deletion is designed and
  reviewed as a separate feature.
- Do not add automatic package installation or commands that restart the
  user's file manager.
- Keep pull requests narrow and document behavior changes and known risks.
