# Quire

<img src="assets/quire-512.png" alt="quire" width="128" />

Turn Markdown into PDF or standalone HTML with Pandoc and WeasyPrint. Quire provides a plain
stylesheet and supports separate profiles for custom styling and reusable content.
Mermaid diagrams and TeX math render to inline SVG at build time when the optional
renderers are installed. Builds and viewing use local resources and make no network requests.

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
diagrams still work. The check runs before any target builds, so one diagram
anywhere in a batch stops the whole call and nothing is written. It reads the
source text, so a Mermaid-looking fence that Pandoc does not treat as code (for
example inside an HTML comment) can still trigger the hint. `--no-mermaid`
keeps diagrams as code instead and gets past it.

For TeX math (`$...$` and `$$...$$`), also provide Node 22.13 or newer and npm,
then run:

```sh
python3 install.py --with-math
```

This installs the pinned MathJax package (Apache-2.0) from `math/package-lock.json`
into `math/node_modules/`. It needs no browser, and it is separate from
`--with-mermaid`; install either or both. Without it, Pandoc writes math as MathML,
which WeasyPrint cannot draw: the PDF shows the flattened symbols followed by the
TeX source. A PDF of a document containing math therefore fails with an
installation hint naming the source file if the renderer is missing, and `--no-math`
keeps Pandoc's output instead. HTML output does not fail: browsers draw MathML
themselves, so Quire keeps Pandoc's MathML and prints one warning naming the file
and the `--with-math` and `--no-math` options. The PDF error appears when that
document builds, so in a batch earlier targets may already be written.

HTML-only use requires Python and Pandoc, without WeasyPrint or its native
libraries. Math stays MathML in that case unless MathJax is installed (see above):

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
quire notes.md --add-css tweak.css      # Append a stylesheet after the selected one
quire notes.md --no-css                 # Pass no stylesheet to Pandoc
quire notes.md --include-before intro.html
quire notes.md --resource-path assets   # Set the resource root
quire notes.md -o notes.html            # Standalone HTML
quire notes.md --format html            # Writes notes.html
quire notes.md --no-mermaid              # Leave diagrams as code
quire notes.md --no-math                 # Keep Pandoc's math output
quire notes.md -v                        # Commands, renders, and cache hits
quire *.md                               # Build several documents
```

`--confidential` with no profile uses `plain-confidential.css`, prepends a
Confidential banner, and writes `notes-confidential.pdf`. The banner repeats
in the top margin on every page. `--css` replaces that stylesheet,
`--add-css` appends to it, and `--no-css` passes none; the banner text stays in
the document either way.
Pass `--no-config` when a profile is your default and the document should
stay unbranded. `--letterhead` still requires a profile.

Input files must end in `.md`, `.markdown`, or `.txt`. Output directories are
created automatically; an existing output PDF is overwritten, but an output
path that is the source file is an error. `--css`, `--add-css`, and
`--include-before` can be repeated. `--add-css` appends stylesheets, in order,
after the selected stylesheets (the profile variant's, `plain.css`,
`plain-confidential.css`, or `--css`), so a profile's branding and variant
selection are kept while a few rules change. `--no-css` takes precedence over
both `--css` and `--add-css`. A profile's named (`@page letterhead`) and
pseudo-class (`@page :first`) `@page` rules outrank a plain
`@page { margin: ... }`; add `!important` to override them, which works only
when the profile's own rule is not `!important`. Unless `--no-css` is set, a
missing added stylesheet is an error before any output is written.
Relative command-line paths resolve from the working directory.

Several targets build in order with the same options, each to its default
output. Every target is checked before anything is written, and the first
failed build stops the run. `-o` takes a single target, and two targets that
would write the same file are an error.

An explicit `--format html|pdf` overrides the output extension; an explicit `-o`
path is preserved even if its extension disagrees. Otherwise `.html` and `.pdf`
select their respective formats, and the default is PDF. Built-in output paths
use the chosen extension when no explicit output path is supplied. Profile
variants, CSS, includes, and suffixes apply to both formats.

HTML embeds CSS, images, local fonts, diagram SVGs, and math SVGs in one file. Local CSS
imports and resource URLs resolve relative to their stylesheet. Missing assets,
remote assets, scripts, and frames produce errors; ordinary hyperlinks are kept.
PDF uses the same embedded document. Successful builds print only `wrote <path>`;
`-v` prints commands, diagram and math rendering, and cache hits to stderr. A failed build
leaves an existing output file intact.

Diagrams scale to the PDF's actual page area without splitting across pages.
Oversized diagrams are reduced proportionally, so very large diagrams may have
small labels. `<br/>` and `<small>` labels are preserved as SVG text. Diagram
failures identify the source file, diagram number, and opening-fence line.

Math is typeset by MathJax in one Node process per document (about 40 ms for a
few equations), with no browser. Inline math sits on the text baseline and is sized
in `ex` units, so it follows the surrounding font size. Display math (`$$...$$`) is
centered on its own line, kept on one page, and scaled down if wider than the text.
Inline math is never broken across lines, so an expression wider than a line
overflows it. Each equation is SVG drawing, so it is not selectable or searchable
in the PDF; its TeX source is kept as the SVG's `aria-label` and `<title>`. A TeX
error (including an undefined macro, or markup MathJax cannot read) fails the build,
naming the source file and the expression. Macros defined inside math (`\def`,
`\newcommand`, `\renewcommand`, `\newenvironment`, `\let`, `\DeclareMathOperator`)
are rejected as undefined, with a clear error. Independently of that, every
expression is typeset with a fresh MathJax input, so a definition made by any
other command (`\definecolor`, `\newcolumntype`, `\Newextarrow`) cannot reach
another equation and results depend only on the cached expression. Define macros in the Markdown instead, as
`\newcommand{\R}{\mathbb{R}}` on its own line outside math: Pandoc expands them
before Quire sees the math. `\require` is not available. Math in the metadata
`title` is kept as plain TeX text in the page `<title>` and PDF title. Not
supported: numbered equations and cross-references, and `mhchem` (`\ce`), which
needs an extra font package. Results are cached in
`$XDG_CACHE_HOME/quire/math`, or `~/.cache/quire/math`, keyed by TeX source, inline
or display, and the MathJax and font versions.

Rendered SVGs are cached in `$XDG_CACHE_HOME/quire/mermaid`, or
`~/.cache/quire/mermaid` by default. Source, profile theme/font settings, stylesheet
contents, and renderer/browser versions determine the cache key. Repeatability
assumes the same pinned runtime and fonts; clear this cache after changing system
fonts. Deleting the cache is safe; the next build recreates it.

## Plain text

A `.txt` file is not parsed as Markdown. Every line is kept as written, in one
code block inside `<div class="quire-verbatim">`, so fixed-width forms,
invoices, and tables keep their columns. Lines are not wrapped (the plain
stylesheets use 9pt text, so about 90 columns fit on letter paper), a leading
`---` is not front matter, and backticks, `\newpage`, and Mermaid fences in
the text stay text. Tabs are expanded to 4-column stops. Output naming,
variants, and includes work as for Markdown; see [Profiles](docs/profiles.md)
to give these pages their own margins.

## Page breaks

`\newpage`, `\pagebreak`, and `\clearpage` (with an optional `[n]` or `{}`
suffix) on their own line, or inside a paragraph, start a new page. They work
between blocks and in lists and blockquotes, but not inside table cells. Quire
writes them as `quire-page-break` elements and puts the break rule first in the
document head, so it applies with `--no-css` and a profile stylesheet can
override it, for example to show a marker in HTML.

Quire drops all other raw TeX from the output and prints one warning per build
naming a dropped command. The warning names the source document, even when the
TeX came from a profile include. Raw TeX that only defines macros
(`\newcommand`, `\renewcommand`, `\providecommand`, `\def`, `\gdef`,
`\DeclareMathOperator`) is dropped without a warning when Pandoc parsed it from
the Markdown, because Pandoc has already expanded those macros where the
Markdown uses them. Raw TeX that is only comments is dropped silently in the same
case, and a definition followed by `\newpage` still gives a page break. The
rule does not look at whether the Markdown uses a definition, so a redefinition of
a LaTeX layout command is dropped silently too; it has no effect in the output
either way. A block that mixes definitions with other TeX, a definition Quire
cannot parse, and raw-attribute blocks such as `{=latex}` (where Pandoc does not
expand macros) still warn.

## Use a profile

A profile is a separate directory containing `profile.toml` and its assets.
Register it under a name, or use its path directly:

```sh
quire install /path/to/profile --name acme
quire --config acme notes.md
quire --prefer-config acme notes.md   # Plain output and a warning if acme is not registered
acme-pdf notes.md
quire --profile /path/to/profile notes.md
quire configs
quire default acme        # Use acme when no profile is chosen
quire default --clear     # Back to plain output
quire remove acme         # Unregister; the profile directory is kept
```

Registration stores the profile's path and creates an `acme-pdf` shortcut;
it does not copy assets or select a default; `quire default` does that. `--no-command` skips the shortcut.
With a profile, `--letterhead` and `--confidential` select that profile's
matching variant.

See [Profiles](docs/profiles.md) for defaults, selection order, and authoring.

## Help and development

```sh
quire --version
quire --help
quire install --help
quire default --help
quire remove --help
python3 install.py --help
python3 test_quire.py
```

See [Contributing](CONTRIBUTING.md) for local checks and the code layout.
If a build reports missing tools, check that Pandoc is on PATH and WeasyPrint
is available in `.venv/bin` or on PATH. Rerunning the installer restores a
missing local Python runtime.

## License

Quire is licensed under the [MIT License](LICENSE).
