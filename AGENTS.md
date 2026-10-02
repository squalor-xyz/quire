# Quire project guidance

This checkout is the standalone Quire project. Work only inside this repository;
do not inspect a parent checkout or depend on its layout.

## Architecture

- `quire.py`: profile loading, config selection, Markdown preparation, job
  planning, and rendering through Pandoc and WeasyPrint.
- `install.py`: local runtime setup, shell launchers, and profile registration.
- `plain.css`: unbranded default stylesheet.
- `plain-confidential.css`: the same treatment plus a running Confidential
  mark, used when `--confidential` is set and no profile is selected.
- `test_quire.py`: stdlib unittest suite with temporary, generic profiles.
- `requirements.txt`: WeasyPrint, the only direct pip dependency.
- `quire_html.py`, `quire_mermaid.py`, `quire_pdf.py`: offline resource embedding,
  diagram rendering/cache, and PDF page fitting.
- `mermaid-render.mjs`: browser worker using the locked optional Mermaid runtime.
- `test_render.py`: rendering unit tests included by `test_quire.py`.
- `test_render_integration.py`: explicit real-tool acceptance tests.

Requires Python 3.11 or newer (`tomllib`). Brand profiles and assets belong in
separate directories. Do not add brand CSS, fragments, or wrapper scripts here.

## Behavior to preserve

- Standalone CLI selection order: `--profile`, `--no-config`, `--config`,
  `QUIRE_CONFIG`, `QUIRE_PROFILE`, user `default`, then plain output.
- Embedded callers may pin `default_profile`; that overrides user selection
  except an explicit `--profile`.
- Profile registration updates the path without changing the user's `default`.
- `--css` replaces profile CSS and `plain.css`; `--no-css` overrides both.
- Each letterhead/confidential combination selects a variant. Use a complete
  stylesheet for each combination rather than stacking brand stylesheets.
- Profiles remain external; registration stores paths rather than copying files.

## Local checks

From the repository root:

```sh
python3 test_quire.py
```

Tests use temporary directories inside this checkout and explicit config/bin
paths. They need neither a brand profile nor PDF tools. Keep new tests isolated
from home config, launchers, environment-based profile selection, and external
assets. Do not run the real installer as a check: it can download packages and
write outside this repository.

A real render additionally needs Pandoc, WeasyPrint, and its native libraries.
Report whether rendering was checked; unit tests alone do not verify PDF output.

Optional real-tool acceptance checks use `python3 test_render_integration.py`.
They need Node/npm, the locked local Mermaid runtime and Chromium, and Poppler
in addition to PDF tools. Keep their caches and artifacts inside the checkout.
Do not add network access during document builds or install dependencies
automatically at build time. The explicit `--with-mermaid` installer option is
the only installer path that sets up the Node runtime dependencies.

## Change boundaries

- Read the code and reuse its patterns. Keep changes small and scoped.
- Ask before adding dependencies or accessing paths outside this repository.
- Do not commit, push, configure remotes, or publish without an explicit request.
- Keep the README and profile documentation aligned with CLI behavior.
