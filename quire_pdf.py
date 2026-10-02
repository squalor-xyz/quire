"""WeasyPrint worker; loaded only by PDF builds, never by the unit suite."""

from __future__ import annotations

import re
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_DIAGRAM_HEIGHT = 0.65


def diagram_height_ratio(document: str) -> float:
    """Percentage max-height on diagram SVGs, or 65% when none is declared."""
    ratio = DEFAULT_DIAGRAM_HEIGHT
    for style in re.findall(r"(?is)<style[^>]*>(.*?)</style>", document):
        style = re.sub(r"(?s)/\*.*?\*/", "", style)
        for block in style.split("}"):
            if "{" not in block:
                continue
            selector, body = block.split("{", 1)
            if "quire-diagram" not in selector or "svg" not in selector:
                continue
            match = re.search(r"(?:^|;)\s*max-height\s*:\s*([0-9.]+)\s*%", body)
            if not match:
                continue
            value = float(match.group(1)) / 100
            if value > 0:
                ratio = value
    return ratio


def diagrams(document):
    for page in document.pages:
        page_box = page._page_box
        for box in page_box.descendants():
            element = box.element
            if element is None or not element.get("data-quire-diagram"):
                continue
            if type(box).__name__ != "BlockBox":
                continue
            svg = next((node for node in element.iter() if node.tag.rsplit("}", 1)[-1] == "svg"), None)
            if svg is None:
                raise ValueError("diagram has no SVG")
            values = [float(value) for value in re.split(r"[\s,]+", svg.get("viewBox", "").strip())]
            if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
                raise ValueError("diagram has invalid SVG dimensions")
            yield element.get("data-quire-diagram"), box, page_box, values[2:]


def render_pdf(source: Path, output: Path) -> None:
    from weasyprint import HTML, CSS
    try:
        from weasyprint.urls import URLFetcher
    except ImportError:  # WeasyPrint 62–69 use callable fetchers.
        from weasyprint import default_url_fetcher

        def offline_fetcher(url, *args, **kwargs):
            if urlsplit(url).scheme not in {"data", "file"}:
                raise ValueError(f"offline PDF cannot load resource: {url}")
            return default_url_fetcher(url, *args, **kwargs)
    else:
        offline_fetcher = URLFetcher(allowed_protocols={"data", "file"}, fail_on_errors=True)
    logging.getLogger("weasyprint").setLevel(logging.WARNING if "--verbose" in sys.argv else logging.ERROR)

    text = source.read_text(encoding="utf-8")
    ratio = diagram_height_ratio(text)
    caps = {}
    for _ in range(8):
        sizing = "\n".join(
            f'[data-quire-diagram="{index}"] > svg {{ width:{width}px !important; height:{height}px !important; }}'
            for index, (width, height) in caps.items()
        )
        document = HTML(string=text, base_url=source.as_uri(), url_fetcher=offline_fetcher).render(
            stylesheets=[CSS(string=sizing)] if sizing else [],
        )
        next_caps = dict(caps)
        for index, box, page, (width, height) in diagrams(document):
            chrome = (box.margin_top + box.margin_bottom + box.padding_top + box.padding_bottom
                      + box.border_top_width + box.border_bottom_width)
            available_width = min(box.width, page.width)
            available_height = min(page.height * ratio, page.height - chrome)
            if available_width <= 0 or available_height <= 0:
                raise ValueError(f"diagram {index} has no printable page area")
            scale = min(1, available_width / width, available_height / height)
            size = (width * scale, height * scale)
            previous = caps.get(index)
            if previous and previous[0] < size[0]:
                size = previous
            next_caps[index] = size
        if next_caps != caps:
            caps = next_caps
            continue
        for index, box, page, _ in diagrams(document):
            graphic = next((child for child in box.descendants()
                            if child.element is not None
                            and child.element.tag.rsplit("}", 1)[-1] == "svg"
                            and "ReplacedBox" in type(child).__name__), None)
            if graphic is None:
                raise ValueError(f"diagram {index} was not rendered as a graphic")
            x, y = graphic.content_box_x(), graphic.content_box_y()
            if (x < page.content_box_x() - 0.5 or y < page.content_box_y() - 0.5
                    or x + graphic.width > page.content_box_x() + page.width + 0.5
                    or y + graphic.height > page.content_box_y() + page.height + 0.5):
                raise ValueError(f"diagram {index} exceeds its printable page area")
        document.write_pdf(output)
        return
    raise ValueError("diagram page sizing did not converge")


if __name__ == "__main__":
    try:
        render_pdf(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as exc:
        print(f"error: PDF rendering failed: {exc}", file=sys.stderr)
        sys.exit(1)
