# Changelog

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
