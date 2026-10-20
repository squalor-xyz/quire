"""Cached TeX math rendering to inline SVG, and Pandoc AST substitution.

MathJax runs in plain Node (mathjax-render.mjs): no browser, no network. All
expressions of a document go through one Node process, and results are cached
by TeX source, display mode, and renderer version, as diagrams are.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from quire_mermaid import SVG_NS, prefix_svg, validate_svg

NORMALIZATION_VERSION = 2
ENGINE_DIR = Path(__file__).resolve().parent


def cache_directory() -> Path:
    if os.environ.get("XDG_CACHE_HOME"):
        root = Path(os.environ["XDG_CACHE_HOME"])
    else:
        root = Path.home() / ".cache"
    return root / "quire" / "math"


def renderer_available(engine: Path = ENGINE_DIR) -> bool:
    manifest = engine / "math/node_modules/mathjax/package.json"
    return bool(shutil.which("node")) and manifest.is_file()


def require_renderer(engine: Path = ENGINE_DIR) -> None:
    if not renderer_available(engine):
        raise SystemExit(
            "Math tooling missing. Install Node >=22.13 and npm, then run:\n"
            "  python3 install.py --with-math\n"
            "Or use --no-math to keep Pandoc's default math output."
        )


def math_nodes(value):
    """Yield every Pandoc Math node, in document order, including metadata."""
    if isinstance(value, dict):
        if value.get("t") == "Math":
            yield value
        else:
            for child in value.values():
                yield from math_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from math_nodes(child)


def labelled(svg: str, tex: str) -> str:
    """Keep the TeX source as the SVG's accessible name and title."""
    root = validate_svg(svg)
    root.set("aria-label", tex)
    title = ET.Element(f"{{{SVG_NS}}}title")
    title.text = tex
    root.insert(0, title)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return ET.tostring(root, encoding="unicode")


class MathRenderer:
    def __init__(self, engine: Path, verbose: bool, cache: Path | None = None):
        self.engine = engine
        self.verbose = verbose
        self.cache = cache if cache is not None else cache_directory()
        self.node = shutil.which("node")
        self.worker = engine / "mathjax-render.mjs"
        require_renderer(engine)
        self.fingerprint = self._call(["--fingerprint"], None).strip()
        self.worker_hash = hashlib.sha256(self.worker.read_bytes()).hexdigest()

    def _call(self, args: list[str], payload: dict | None) -> str:
        result = subprocess.run(
            [self.node, str(self.worker), *args],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            cwd=str(self.engine),
            timeout=120,
        )
        if result.returncode:
            raise SystemExit(
                result.stderr.strip() or f"MathJax exited with code {result.returncode}"
            )
        if self.verbose and result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.stdout

    def key(self, tex: str, display: bool) -> str:
        signature = json.dumps(
            {
                "tex": tex,
                "display": display,
                "renderer": self.fingerprint,
                "normalization": NORMALIZATION_VERSION,
                "worker": self.worker_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(signature.encode()).hexdigest()

    def _cached(self, key: str) -> str | None:
        try:
            svg = (self.cache / f"{key}.svg").read_text(encoding="utf-8")
            validate_svg(svg)
        except (OSError, UnicodeError, ValueError):
            return None
        return svg

    def _store(self, key: str, svg: str) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.cache, suffix=".svg", delete=False
        ) as temporary:
            pending = Path(temporary.name)
            try:
                temporary.write(svg)
                temporary.close()
                os.replace(pending, self.cache / f"{key}.svg")
            finally:
                pending.unlink(missing_ok=True)

    def render_many(
        self, items: list[tuple[str, bool]]
    ) -> list[tuple[str | None, str | None]]:
        """Render (tex, display) pairs; return (svg, None) or (None, message).

        Cache misses go to the worker in one process. TeX errors are reported
        per expression and never cached; a broken worker raises.
        """
        keys = [self.key(tex, display) for tex, display in items]
        found = {key: self._cached(key) for key in set(keys)}
        missing = [
            index
            for index, key in enumerate(keys)
            if found[key] is None and keys.index(key) == index
        ]
        failed: dict[str, str] = {}
        if missing:
            if self.verbose:
                print(
                    f"math: rendering {len(missing)} of {len(items)} expressions "
                    f"({len(items) - len(missing)} cached)",
                    file=sys.stderr,
                )
            payload = {
                "items": [{"tex": items[i][0], "display": items[i][1]} for i in missing]
            }
            reply = json.loads(self._call([], payload))
            results = reply.get("results") if isinstance(reply, dict) else None
            if not isinstance(results, list) or len(results) != len(missing):
                raise ValueError("renderer returned the wrong number of results")
            fresh = {}
            for index, result in zip(missing, results):
                svg = result.get("svg") if isinstance(result, dict) else None
                error = result.get("error") if isinstance(result, dict) else None
                if isinstance(svg, str) and error is None:
                    try:
                        validate_svg(svg)
                    except ValueError as exc:
                        failed[keys[index]] = f"renderer output rejected: {exc}"
                    else:
                        fresh[keys[index]] = svg
                elif error is not None and svg is None:
                    failed[keys[index]] = str(error)
                else:
                    raise ValueError("renderer returned a malformed result")
            for key, svg in fresh.items():
                self._store(key, svg)
            found.update(fresh)
        elif self.verbose:
            print(f"math: {len(items)} expressions cached", file=sys.stderr)
        return [
            (found[key], None) if found.get(key) else (None, failed[key])
            for key in keys
        ]


def locate(job, tex: str):
    """The source file holding an expression, falling back to the main file."""
    texts = [(job.source, job.original_markdown or job.markdown)]
    texts.extend(job.include_sources)
    for needle in (f"${tex}$", tex):
        for path, text in texts:
            if needle in text:
                return path
    return job.source


def render_math(ast: dict, job, renderer_factory=None, available=None) -> bool:
    """Replace Math nodes with inline SVG; return whether any were replaced.

    available(engine) reports whether the renderer is installed; it only decides
    whether HTML output falls back to MathML.
    """
    nodes = list(math_nodes(ast))
    if not nodes or job.no_math:
        return False
    probe = available or renderer_available
    if job.format == "html" and not probe(ENGINE_DIR):
        # Browsers draw Pandoc's MathML, so HTML degrades; WeasyPrint cannot.
        print(
            f"warning: {job.source}: math left as MathML because the renderer "
            "is not installed; run python3 install.py --with-math, or pass "
            "--no-math to keep MathML quietly",
            file=sys.stderr,
        )
        return False
    items = []
    for node in nodes:
        item = (node["c"][1], node["c"][0]["t"] == "DisplayMath")
        if item not in items:
            items.append(item)
    try:
        renderer = (renderer_factory or MathRenderer)(ENGINE_DIR, job.verbose)
        results = renderer.render_many(items)
    except (SystemExit, OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"error: {job.source}: document contains math: {exc}") from exc
    rendered = dict(zip(items, results))
    problems = [
        f"error: {locate(job, tex)}: TeX error in `{tex}`: {message}"
        for (tex, _), (_, message) in rendered.items()
        if message is not None
    ]
    if problems:
        raise SystemExit("\n".join(problems))
    for number, node in enumerate(nodes, 1):
        tex, display = node["c"][1], node["c"][0]["t"] == "DisplayMath"
        svg = prefix_svg(
            labelled(rendered[(tex, display)][0], tex),
            f"quire-x{number}-",
            keep_size=True,
        )
        classes = "quire-math quire-math-display" if display else "quire-math"
        node.clear()
        node.update(
            {"t": "RawInline", "c": ["html", f'<span class="{classes}">{svg}</span>']}
        )
    return True
