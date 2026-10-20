# Changelog

## Unreleased

- Render TeX math to inline SVG without a browser or network. Pandoc writes `$...$` and `$$...$$` as MathML, which WeasyPrint cannot draw, so a PDF showed the flattened symbols followed by the TeX source. Math nodes are now replaced with MathJax SVG (inline on the text baseline, sized in `ex` so it follows the font size; display centered, kept on one page, and scaled to fit the text width), with the TeX source kept as the SVG's `aria-label` and `<title>`. All equations of a document render in one Node process, and results are cached in `$XDG_CACHE_HOME/quire/math` by TeX source, display mode, and MathJax version. A TeX error, including an undefined macro, fails the build and names the source file and the expression. Macros defined inside math (`\def`, `\newcommand`, and similar) are rejected with a clear error, and each expression is typeset with a fresh MathJax input, so no definition (including `\definecolor`, `\newcolumntype`, `\Newextarrow`) carries over to another equation; define macros in the Markdown, where Pandoc expands them. Math in a metadata `title` stays plain text in the page title. A PDF of a document with math and no renderer fails with an installation hint; HTML keeps Pandoc's MathML and prints one warning, since browsers draw it. The renderer is optional and separate from Mermaid: `python3 install.py --with-math` installs the locked MathJax 4.1.3 under `math/` (no Puppeteer or Chromium). `--no-math` keeps Pandoc's previous output. Numbered equations, cross-references, and `mhchem` are not supported.
- Trim surrounding whitespace from `--config` and `--prefer-config` names, so `--config " acme "` selects `acme`. A name that is blank after trimming is an empty name and fails with `error: config name is empty`, as `--config ""` does; `--config "   "` no longer reports an unknown config. A blank `QUIRE_CONFIG` is still treated as unset.
- Reject an empty `--config` name. `--config ""` is now an error (`error: config name is empty`) instead of being ignored, matching `--prefer-config`; a blank `QUIRE_CONFIG` is still treated as unset.
- Add `--prefer-config NAME` for scripts that want a brand when the machine has it. A registered NAME is selected as `--config` would; an unregistered one prints one warning (naming the config file and the `quire install` command) and selection continues with `QUIRE_CONFIG`, `QUIRE_PROFILE`, the user default, then plain output. It sits in the `--config` slot, below `--profile` and `--no-config`; combining it with `--config` is a usage error (exit 2), and an empty name is an error. The warning is held back until the arguments parse, so `--help` and usage errors do not print it. A registered name whose profile directory is missing still fails, and `--letterhead` with no profile left says the preferred config was not registered. `--config` is unchanged and stays strict.
- Add an optional `fonts` list to `profile.toml`, at profile level and per variant (a variant's list replaces the profile's). WeasyPrint silently substitutes a fallback when a stylesheet names a font that is not installed, so a missing brand font produced a normal-looking PDF in the wrong typeface. Now, before any target builds, each listed family is checked against fontconfig (`fc-list`, case-insensitive), and a missing one fails with `error: profile NAME needs font "FAMILY", which is not installed`, writing nothing. NAME is the config name that selected the profile, else its directory name. If `fc-list` is unavailable, fails, or times out (60 seconds), Quire warns once and continues. PDF output only; HTML is not checked. The check runs whenever a profile is selected, including with `--css`, `--no-css`, and `--add-css`. Profiles without `fonts` behave as before.
- Accept `.txt` targets and render them verbatim. The whole file becomes one code block inside `<div class="quire-verbatim">`, so fixed-width forms, invoices, and tables keep their columns, and nothing in the text (a leading `---`, backtick fences, `\newpage`, Mermaid fences) is interpreted. Tabs are expanded to 4-column stops. Includes, variants, and output naming work as for Markdown. Both plain stylesheets keep `.quire-verbatim pre` unwrapped at 9pt; a profile can give these pages their own margins with `.quire-verbatim { page: verbatim; }` and `@page verbatim { ... }`. Other suffixes are still rejected.
- Check for the Mermaid renderer before any target builds. A document with a Mermaid block used to fail with "Mermaid tooling missing" only when rendering reached its first diagram, after earlier targets had already been written. Now, if any target (without `--no-mermaid`) has a Mermaid fence in its Markdown or included sources and Node or the Mermaid runtime is missing, the call fails first with the same install hint, naming the source file and line, and writes nothing. One diagram anywhere in a batch stops the whole call. The scan reads source text, skips profile includes that do not apply and fences indented as code, and can still be tripped by a fence Pandoc ignores (such as one in an HTML comment); `--no-mermaid` gets past it.
- Turn `\newpage`, `\pagebreak`, and `\clearpage` into page breaks, as a block or inside a paragraph. Pandoc treats them as raw TeX and the HTML writer dropped them, so a page break written for LaTeX silently vanished. An optional `[n]` or `{}` suffix is accepted. Other raw TeX is still dropped, now with one warning per build naming the source file and a dropped command, and paragraphs left empty by that are removed.
- Add a repeatable `--add-css PATH` option that appends stylesheets after the selected stylesheets (profile variant, `plain.css`, `plain-confidential.css`, or `--css`), so a one-off adjustment no longer means copying the profile stylesheet. `--no-css` still passes none. Unless `--no-css` is set, a missing added stylesheet is an error before any output is written. Overriding a profile's named or pseudo-class `@page` rules (`@page letterhead`, `@page :first`) needs `!important`, and works only if the profile rule is not itself `!important`.
- Say where an unknown config name was looked up. The error now names the config file it read, notes when that file is missing, says whether the name came from `QUIRE_CONFIG` or the config `default`, and suggests the fix: `quire install <profile-dir> --name NAME`, or `quire default` for a stale default. The known names are still listed.

## 1.1.1

- Name the config when its registered profile directory no longer has a `profile.toml`, and suggest `quire install <profile dir> --name NAME` to re-register it. Previously the error showed only the missing path.

## 1.1.0

- Refuse to build when the output path is the source file. Previously `quire notes.md -o notes.md` replaced the Markdown with the PDF.
- Run the PDF worker with the interpreter named by the chosen `weasyprint` script. A WeasyPrint on PATH (Homebrew, pipx) previously ran under `.venv/bin/python` or the launching Python, which may not import it.
- `python3 install.py --python PATH` now requires `--skip-venv`. Previously it was ignored, and the launchers used `.venv`.
- Recognize YAML front matter as Pandoc does: a `---` opener not followed by a blank line, closed by `---` or `...`, with an optional byte order mark. A document that starts with a horizontal rule is no longer read as front matter.
- Add `quire default NAME` and `quire default --clear` to set or clear the default profile without editing `config.toml`.
- Add `quire remove NAME` to unregister a profile and delete its `NAME-pdf` shortcut. Shortcuts Quire did not write and profile directories are left alone.
- Accept several targets in one call, as in `quire *.md`. All targets are checked before any is built.

## 1.0.2

- Keep `<small>` sub-labels below the line above them. Each line advances by the height of the larger line.
- Break flowchart labels only at spaces and `<br/>`. A long word widens its node.
- Size node boxes from the lines that are drawn.
- Draw an opaque background behind edge labels so the connector does not show through the text.
- Keep each table row on one page, and keep the header with the first row. A profile stylesheet can replace either rule.

## 1.0.1

- Cap each PDF diagram at 65% of the page content height, or at the `max-height` percentage on `.quire-diagram > svg`. Diagrams are never enlarged past their intrinsic size.
- Keep headings with the diagram that follows them.
- Render flowchart labels as centred SVG text. Line breaks remain line breaks, and `<small>` text is drawn smaller.
- Changing the diagram font renders again instead of reusing a cached image.

## 1.0.0

First public release.

- Build a PDF or one self-contained HTML file from Markdown with Pandoc.
- Style documents with the plain stylesheet, or with an external profile for letterhead and confidential variants.
- `--confidential` without a profile uses the plain confidential stylesheet and a running Confidential mark.
- Render Mermaid diagrams to inline SVG. Install the renderer with `python3 install.py --with-mermaid`. Document builds do not download it and do not use the network.
- Embed local CSS, images, and fonts in HTML. Remote resources, scripts, and frames are errors. Ordinary links stay links.
- Fit diagrams to the PDF page. A successful build prints `wrote <path>`.
