"""Source locations, cached Mermaid rendering, and Pandoc AST substitution."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path

from quire_common import cache_directory, prefix_svg, validate_svg

NORMALIZATION_VERSION = 3
ENGINE_DIR = Path(__file__).resolve().parent


def front_matter_end(text: str) -> int | None:
    """End of a leading YAML block's closing line, or None when there is none.

    As in Pandoc, the opener is a --- line (after an optional byte order mark)
    not followed by a blank line, and the block closes at the next --- or ...
    line. Pandoc otherwise treats the opener as a thematic break, so the rest
    of the buffer is still body.
    """
    lines = text.split("\n")
    if lines[0].lstrip("\ufeff").rstrip() != "---" or len(lines) < 2 or not lines[1].strip():
        return None
    offset = len(lines[0])
    for line in lines[1:]:
        offset += 1 + len(line)
        if line.rstrip() in {"---", "..."}:
            return offset
    return None


def fence_locations(text: str) -> list[tuple[str, int]]:
    """Locate fences; Pandoc's AST remains the authority on which are code."""
    found = []
    fence = None
    body = []
    start = indent = 0
    mermaid = False
    end = front_matter_end(text)
    front_matter_lines = 0 if end is None else text.count("\n", 0, end) + 1
    for number, original in enumerate(text.splitlines(), 1):
        if number <= front_matter_lines:
            continue
        line = re.sub(r"^(?: {0,3}> ?)+", "", original)
        if fence is None:
            list_prefix = re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", line)
            list_width = len(list_prefix[0]) if list_prefix else 0
            line = line[list_width:]
            match = re.match(r"^(\s*)(`{3,}|~{3,})([^\n]*)$", line)
            if not match:
                continue
            indent = list_width + len(match[1])
            fence = match[2]
            info = match[3].strip()
            mermaid = info == "mermaid" or bool(re.search(r"(?:^|[\s{])\.mermaid(?:\s|}|$)", info))
            start = number
            body = []
        elif re.fullmatch(r"\s*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
            if mermaid:
                found.append(("\n".join(body).rstrip("\n"), start))
            fence = None
        else:
            removed = min(indent, len(line) - len(line.lstrip(" ")))
            body.append(line[removed:])
    if fence is not None and mermaid:
        found.append(("\n".join(body).rstrip("\n"), start))
    return found


def code_blocks(value):
    if isinstance(value, dict):
        if value.get("t") == "CodeBlock" and "mermaid" in value["c"][0][1]:
            yield value
        else:
            for child in value.values():
                yield from code_blocks(child)
    elif isinstance(value, list):
        for child in value:
            yield from code_blocks(child)


def annotated_fences(text: str) -> str:
    """Attach original lines to candidate fences for Pandoc to validate.

    Candidates inside raw HTML or indented examples can resemble real fences.
    Parsing these annotations lets Pandoc discard those false candidates.
    """
    lines = text.splitlines(keepends=True)
    for _, number in fence_locations(text):
        original = lines[number - 1]
        newline = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
        bare = original.rstrip("\r\n")
        match = re.search(r"(`{3,}|~{3,})(.*)$", bare)
        info = match[2].strip()
        marker = f'data-quire-source-line="{number}"'
        if info == "mermaid":
            info = "{.mermaid " + marker + "}"
        elif info.startswith("{") and info.endswith("}"):
            info = info[:-1] + " " + marker + "}"
        else:
            continue
        lines[number - 1] = bare[:match.start()] + match[1] + info + newline
    return "".join(lines)


def source_locations(job, parse):
    locations = defaultdict(deque)
    for path, text in [(job.source, job.original_markdown or job.markdown), *job.include_sources]:
        annotated = annotated_fences(text)
        if annotated == text:
            continue
        ast = parse(annotated)
        for block in code_blocks(ast):
            attributes, source = block["c"]
            lines = [value for key, value in attributes[2] if key == "data-quire-source-line"]
            if lines:
                locations[source.rstrip("\n")].append((path, int(lines[-1])))
    return locations


def renderer_available(engine: Path = ENGINE_DIR) -> bool:
    manifest = engine / "node_modules/@mermaid-js/mermaid-cli/package.json"
    return bool(shutil.which("node")) and manifest.is_file()


def require_renderer(engine: Path = ENGINE_DIR) -> None:
    if not renderer_available(engine):
        raise SystemExit(
            "Mermaid tooling missing. Install Node >=22.13 and npm, then run:\n"
            "  python3 install.py --with-mermaid\n"
            "Or use --no-mermaid to keep diagrams as source text."
        )


class MermaidRenderer:
    def __init__(self, engine: Path, config: dict, font_css: str, verbose: bool,
                 cache: Path | None = None):
        self.engine = engine
        self.config = config
        self.font_css = font_css
        self.verbose = verbose
        self.cache = cache if cache is not None else cache_directory("mermaid")
        self.node = shutil.which("node")
        self.worker = engine / "mermaid-render.mjs"
        require_renderer(engine)
        self.env = os.environ.copy()
        self.env["PUPPETEER_CACHE_DIR"] = str(engine / ".cache" / "puppeteer")
        self.fingerprint = self._call(["--fingerprint"], None).strip()
        self.worker_hash = hashlib.sha256(self.worker.read_bytes()).hexdigest()

    def _call(self, args: list[str], payload: dict | None) -> str:
        result = subprocess.run(
            [self.node, str(self.worker), *args],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True, text=True, env=self.env, cwd=str(self.engine), timeout=120,
        )
        if result.returncode:
            raise SystemExit(result.stderr.strip() or f"Mermaid exited with code {result.returncode}")
        if self.verbose and result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.stdout

    def render(self, source: str, index: int) -> str:
        signature = json.dumps({
            "source": source, "config": self.config, "font_css": self.font_css,
            "renderer": self.fingerprint, "normalization": NORMALIZATION_VERSION,
            "worker": self.worker_hash,
        }, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(signature.encode()).hexdigest()
        path = self.cache / f"{key}.svg"
        try:
            svg = path.read_text(encoding="utf-8")
            validate_svg(svg)
        except (OSError, UnicodeError, ValueError):
            if self.verbose:
                print(f"mermaid block {index}: rendering {key[:12]}", file=sys.stderr)
            svg = self._call([], {"source": source, "config": self.config,
                                  "fontCSS": self.font_css, "seed": key})
            validate_svg(svg)
            self.cache.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache,
                                             suffix=".svg", delete=False) as temporary:
                pending = Path(temporary.name)
                try:
                    temporary.write(svg)
                    temporary.close()
                    os.replace(pending, path)
                finally:
                    pending.unlink(missing_ok=True)
        else:
            if self.verbose:
                print(f"mermaid block {index}: cache hit {key[:12]}", file=sys.stderr)
        return prefix_svg(svg, f"quire-m{index}-")


def render_blocks(ast: dict, job, font_css: str, renderer_factory=MermaidRenderer,
                  locations=None) -> bool:
    blocks = list(code_blocks(ast))
    if not blocks or job.no_mermaid:
        return False
    if locations is None:
        locations = defaultdict(deque)
        for path, text in [(job.source, job.original_markdown or job.markdown), *job.include_sources]:
            for content, line in fence_locations(text):
                locations[content].append((path, line))
    renderer = None
    for index, block in enumerate(blocks, 1):
        attributes, source = block["c"]
        candidates = locations[source.rstrip("\n")]
        if not candidates:
            raise SystemExit(f"error: {job.source}: Mermaid block {index}: cannot locate opening fence")
        path, line = candidates.popleft()
        try:
            if renderer is None:
                renderer = renderer_factory(ENGINE_DIR, job.mermaid, font_css, job.verbose)
            svg = renderer.render(source, index)
        except (SystemExit, OSError, ValueError, subprocess.SubprocessError) as exc:
            raise SystemExit(f"error: {path}: Mermaid block {index}, line {line}: {exc}") from exc
        classes = ["quire-diagram", *[item for item in attributes[1] if item != "mermaid"]]
        identifier = f' id="{html.escape(attributes[0], quote=True)}"' if attributes[0] else ""
        block.clear()
        block.update({"t": "RawBlock", "c": ["html", (
            f'<div class="{html.escape(" ".join(classes), quote=True)}"{identifier}'
            f' data-quire-diagram="{index}">{svg}</div>'
        )]})
    return True
