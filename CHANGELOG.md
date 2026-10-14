# Changelog

## Unreleased

- Check for the Mermaid renderer before any target builds. A document with a Mermaid block used to fail with "Mermaid tooling missing" only when rendering reached its first diagram, after earlier targets had already been written. Now, if any target (without `--no-mermaid`) has a Mermaid fence in its Markdown or included sources and Node or the Mermaid runtime is missing, the call fails first with the same install hint, naming the source file and line, and writes nothing. One diagram anywhere in a batch stops the whole call. The scan reads source text, skips profile includes that do not apply and fences indented as code, and can still be tripped by a fence Pandoc ignores (such as one in an HTML comment); `--no-mermaid` gets past it.
- Turn `\newpage`, `\pagebreak`, and `\clearpage` into page breaks, as a block or inside a paragraph. Pandoc treats them as raw TeX and the HTML writer dropped them, so a page break written for LaTeX silently vanished. An optional `[n]` or `{}` suffix is accepted. Other raw TeX is still dropped, now with one warning per build naming the source file and a dropped command, and paragraphs left empty by that are removed.
- Add a repeatable `--add-css PATH` option that appends stylesheets after the selected stylesheets (profile variant, `plain.css`, `plain-confidential.css`, or `--css`), so a one-off adjustment no longer means copying the profile stylesheet. `--no-css` still passes none. Unless `--no-css` is set, a missing added stylesheet is an error before any output is written. Overriding a profile's named or pseudo-class `@page` rules (`@page letterhead`, `@page :first`) needs `!important`, and works only if the profile rule is not itself `!important`.

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
