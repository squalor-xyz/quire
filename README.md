# Quire

Turn Markdown into PDF or standalone HTML with Pandoc and WeasyPrint. Quire provides a plain
stylesheet and supports separate profiles for custom styling and reusable content.
Mermaid diagrams render to inline SVG at build time when the optional renderer
is installed. Builds and viewing use local resources and make no network requests.

## Install

Requires Python 3.11 or newer, Pandoc, and WeasyPrint's native libraries.
On macOS with Homebrew:

```sh
brew install pandoc pango gdk-pixbuf libffi
python3 install.py
```

Run the installer from this checkout. It creates `.venv`, installs WeasyPrint
there if needed, and writes `~/.local/bin/quire`. It does not edit your shell
settings. If that directory is not on PATH, add this to your shell configuration:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

On other systems, install Pandoc and WeasyPrint's native libraries for your
platform before running the Python installer.

For Mermaid diagrams, also provide Node 22.13 or newer and npm, then run:

```sh
python3 install.py --with-mermaid
```

This installs the pinned Mermaid CLI (MIT) and Puppeteer (Apache-2.0), including
Chromium, inside the checkout. The browser and npm caches live in `.cache/`;
packages live in `node_modules/`. This optional setup downloads a substantial
browser runtime. Ordinary installation does not require Node or download it.
Builds never install tooling automatically. A document containing Mermaid blocks
fails with an installation hint if the renderer is missing; documents without
diagrams still work. `--no-mermaid` keeps diagrams as code instead.

HTML-only use requires Python and Pandoc, without WeasyPrint or its native libraries:

```sh
python3 quire.py --no-config notes.md -o notes.html
```

The launcher points at this checkout: keep it in place. After moving it, rerun
`python3 install.py` and re-register any profile shortcuts. Use
`--bin-dir PATH` to choose another launcher directory.

## Make a PDF

```sh
quire notes.md
```

Writes `notes.pdf` beside the source. With no selected profile, `plain.css`
provides US letter pages, sans-serif text, and page numbers.

```sh
quire notes.md -o output/notes.pdf       # Choose an output path
quire notes.md --no-config              # Use the plain style
quire notes.md --no-config --confidential
quire notes.md --css custom.css         # Replace the selected stylesheet
quire notes.md --no-css                 # Pass no stylesheet to Pandoc
quire notes.md --include-before intro.html
quire notes.md --resource-path assets   # Set the resource root
quire notes.md -o notes.html            # Standalone HTML
quire notes.md --format html            # Writes notes.html
quire notes.md --no-mermaid              # Leave diagrams as code
quire notes.md -v                        # Commands, renders, and cache hits
```

`--confidential` with no profile uses `plain-confidential.css`, prepends a
Confidential banner, and writes `notes-confidential.pdf`. The banner repeats
in the top margin on every page. `--css` replaces that stylesheet and
`--no-css` passes none; the banner text stays in the document either way.
Pass `--no-config` when a profile is your default and the document should
stay unbranded. `--letterhead` still requires a profile.

Input files must end in `.md` or `.markdown`. Output directories are created
automatically; an existing output PDF is overwritten. `--css` and
`--include-before` can be repeated. `--no-css` takes precedence over `--css`.
Relative command-line paths resolve from the working directory.

An explicit `--format html|pdf` overrides the output extension; an explicit `-o`
path is preserved even if its extension disagrees. Otherwise `.html` and `.pdf`
select their respective formats, and the default is PDF. Built-in output paths
use the chosen extension when no explicit output path is supplied. Profile
variants, CSS, includes, and suffixes apply to both formats.

HTML embeds CSS, images, local fonts, and diagram SVGs in one file. Local CSS
imports and resource URLs resolve relative to their stylesheet. Missing assets,
remote assets, scripts, and frames produce errors; ordinary hyperlinks are kept.
PDF uses the same embedded document. Successful builds print only `wrote <path>`;
`-v` prints commands, diagram rendering, and cache hits to stderr. A failed build
leaves an existing output file intact.

Diagrams scale to the PDF's actual page area without splitting across pages.
Oversized diagrams are reduced proportionally, so very large diagrams may have
small labels. `<br/>` and `<small>` labels are preserved as SVG text. Diagram
failures identify the source file, diagram number, and opening-fence line.

Rendered SVGs are cached in `$XDG_CACHE_HOME/quire/mermaid`, or
`~/.cache/quire/mermaid` by default. Source, profile theme/font settings, stylesheet
contents, and renderer/browser versions determine the cache key. Repeatability
assumes the same pinned runtime and fonts; clear this cache after changing system
fonts. Deleting the cache is safe; the next build recreates it.

## Use a profile

A profile is a separate directory containing `profile.toml` and its assets.
Register it under a name, or use its path directly:

```sh
quire install /path/to/profile --name acme
quire --config acme notes.md
acme-pdf notes.md
quire --profile /path/to/profile notes.md
quire configs
```

Registration stores the profile's path and creates an `acme-pdf` shortcut;
it does not copy assets or select a default. `--no-command` skips the shortcut.
With a profile, `--letterhead` and `--confidential` select that profile's
matching variant.

See [Profiles](docs/profiles.md) for defaults, selection order, and authoring.

## Help and development

```sh
quire --version
quire --help
quire install --help
python3 install.py --help
python3 test_quire.py
```

See [Contributing](CONTRIBUTING.md) for local checks and the code layout.
If a build reports missing tools, check that Pandoc is on PATH and WeasyPrint
is available in `.venv/bin` or on PATH. Rerunning the installer restores a
missing local Python runtime.

## License

Quire is licensed under the [MIT License](LICENSE).
