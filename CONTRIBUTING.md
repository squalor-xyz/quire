# Contributing to Quire

Use Python 3.11 or newer. The unit tests use the standard library; no package
installation is needed to run them.

## Code layout

| File | Purpose |
| --- | --- |
| `quire.py` | Load profiles, prepare Markdown, plan jobs, and invoke Pandoc |
| `install.py` | Set up the runtime, write launchers, and register profiles |
| `plain.css` | Default PDF styling |
| `test_quire.py` | Isolated unit tests with temporary profile assets |
| `requirements.txt` | WeasyPrint dependency |
| `AGENTS.md` | Project guidance for coding agents |

Brand assets belong in external profiles. See [Profiles](docs/profiles.md) for
the format and [README](README.md) for installation and usage.

## Check a change

Run from the repository root:

```sh
python3 test_quire.py
```

Tests create and clean temporary directories inside the checkout. They exercise
profile selection, Markdown preparation, output planning, configuration, and
launcher generation without invoking PDF tools or writing user settings.
Use generic fixtures and explicit config/bin paths for new tests.

For changes to rendering or styling, also build and inspect a PDF when the
runtime is available:

```sh
.venv/bin/python quire.py --no-config README.md -o .venv/quire-check.pdf
```

This requires Pandoc and WeasyPrint's native libraries. Check pagination, text,
and images as appropriate to the change. Report the checks run and any limits.
Do not use the real installer as a unit test; it writes launchers and may
download dependencies.

Keep changes focused, ask before adding dependencies, and update documentation
when changing user-visible behavior.
