"""SVG validation, ID isolation, and cache location shared by the renderers."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"


def cache_directory(name: str) -> Path:
    """Return the per-renderer cache directory under the XDG or home cache root."""
    if os.environ.get("XDG_CACHE_HOME"):
        root = Path(os.environ["XDG_CACHE_HOME"])
    else:
        root = Path.home() / ".cache"
    return root / "quire" / name


def validate_svg(text: str) -> ET.Element:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("invalid SVG") from exc
    if root.tag not in {"svg", f"{{{SVG_NS}}}svg"}:
        raise ValueError("renderer did not produce SVG")
    try:
        box = [
            float(part) for part in re.split(r"[\s,]+", root.get("viewBox", "").strip())
        ]
    except ValueError as exc:
        raise ValueError("SVG has no usable viewBox") from exc
    if (
        len(box) != 4
        or not all(math.isfinite(value) for value in box)
        or box[2] <= 0
        or box[3] <= 0
    ):
        raise ValueError("SVG has no usable viewBox")
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject"}:
            raise ValueError(f"unsupported SVG element: {tag}")
        if any(name.lower().startswith("on") for name in node.attrib):
            raise ValueError("active SVG content")
    return root


def prefix_svg(text: str, prefix: str, keep_size: bool = False) -> str:
    """Keep cached diagrams reusable while isolating IDs and CSS selectors.

    keep_size leaves width, height, and style alone, for SVG sized in ex units.
    """
    root = validate_svg(text)
    ids = {node.get("id") for node in root.iter() if node.get("id")}
    replacements = {value: prefix + value for value in ids}

    def rewrite(value: str, selectors: bool = False) -> str:
        value = re.sub(
            r"url\(\s*(['\"]?)#([^)'\"\s]+)\1\s*\)",
            lambda match: f"url(#{replacements.get(match[2], match[2])})",
            value,
        )
        if selectors:
            # Only rewrite selector IDs, never color literals inside declarations.
            value = re.sub(
                r"([^{}]+)\{",
                lambda match: re.sub(
                    r"#([A-Za-z_][\w-]*)",
                    lambda item: "#" + replacements.get(item[1], item[1]),
                    match[1],
                )
                + "{",
                value,
            )
        return value

    for node in root.iter():
        for key, value in list(node.attrib.items()):
            if key == "id":
                node.set(key, replacements[value])
            elif key in {"aria-labelledby", "aria-describedby"}:
                node.set(
                    key,
                    " ".join(replacements.get(part, part) for part in value.split()),
                )
            elif key.rsplit("}", 1)[-1] == "href" and value.startswith("#"):
                node.set(key, "#" + replacements.get(value[1:], value[1:]))
            else:
                node.set(key, rewrite(value))
        if node.tag.rsplit("}", 1)[-1] == "style" and node.text:
            node.text = rewrite(node.text, selectors=True)
    if not keep_size:
        box = re.split(r"[\s,]+", root.get("viewBox").strip())
        root.set("width", box[2])
        root.set("height", box[3])
        root.set("style", "max-width:100%;height:auto")
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return ET.tostring(root, encoding="unicode")
