# Changelog

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
