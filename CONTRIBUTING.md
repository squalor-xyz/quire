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
| `quire_html.py`, `quire_mermaid.py`, `quire_pdf.py` | Resource embedding, diagrams, and PDF layout |
| `mermaid-render.mjs` | Offline browser worker and SVG label normalization |
| `test_render.py` | Tool-independent rendering tests, included in the unit suite |
| `test_render_integration.py` | Explicit real-tool acceptance checks |
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

## Rendering acceptance checks

Provide Pandoc, Node 22.13 or newer, npm, a working local WeasyPrint runtime,
and Poppler's `pdftotext` and `pdftoppm`. Poppler is only a test dependency;
Quire does not need it to build documents. Install the approved optional Node
dependencies without writing launchers:

```sh
PUPPETEER_CACHE_DIR="$PWD/.cache/puppeteer" npm ci --cache .cache/npm --no-audit --no-fund
PUPPETEER_CACHE_DIR="$PWD/.cache/puppeteer" PUPPETEER_SKIP_CHROME_HEADLESS_SHELL_DOWNLOAD=true node node_modules/puppeteer/install.mjs
python3 test_render_integration.py
```

This separate suite renders the eleven-diagram generic fixture, checks PDF text
and page bounds, tests custom paper sizes and variants, exercises cache hits and
failure behavior, and opens HTML in Chromium while blocking network requests.
It creates temporary documents and caches inside the checkout. Missing tools
skip this suite with an explicit reason; a skipped suite is not rendering
verification. Inspect rasterized output separately when changing diagram layout
or label normalization. The unit suite needs none of these tools.

To retain the fixture HTML, PDF, and page images for inspection:

```sh
QUIRE_RENDER_ARTIFACTS=.cache/render-check python3 test_render_integration.py IntegrationTests.test_fixture_html_pdf_cache_and_labels
```

Keep changes focused, ask before adding dependencies, and update documentation
when changing user-visible behavior.
