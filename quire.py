#!/usr/bin/env python3
"""
Quire: build PDF or standalone HTML from Markdown via Pandoc.

With no config, the document uses plain.css only (page size, type, page number).
`--confidential` without a profile uses plain-confidential.css and a running
Confidential mark. A named config points at a profile directory
(profile.toml, CSS, HTML fragments).

Examples:
  quire notes/foo.md
  quire notes/foo.md --css extra.css -o out/foo.pdf
  quire --no-config notes/foo.md --confidential
  quire --config acme notes/foo.md --letterhead
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from quire_mermaid import fence_locations, front_matter_end, require_renderer

__version__ = "1.2.0"

ENGINE_DIR = Path(__file__).resolve().parent
PLAIN_CSS = ENGINE_DIR / "plain.css"
PLAIN_CONFIDENTIAL_CSS = ENGINE_DIR / "plain-confidential.css"
PLAIN_CONFIDENTIAL_BANNER = '<div class="confidential-banner">Confidential</div>'

INSTALL_HELP = """
Missing tools for PDF build.

  1) Pandoc + native libs (macOS):
       brew install pandoc pango gdk-pixbuf libffi

  2) WeasyPrint in a local venv next to quire.py:
       python3 -m venv .venv
       .venv/bin/pip install -r requirements.txt

  3) Run:
       .venv/bin/python quire.py path/to/foo.md

Or install the quire command: python3 install.py
See README.md.
""".strip()

VARIANT_NAMES = {
    (False, False): "default",
    (True, False): "letterhead",
    (False, True): "confidential",
    (True, True): "letterhead-confidential",
}


@dataclass
class Include:
    text: str
    skip_if_body_contains: list[str] = field(default_factory=list)
    only_if_body_contains: list[str] = field(default_factory=list)
    after_marker: str | None = None
    after_anchor: str | None = None
    close_tag: str = "</div>"
    close_count: int = 1
    source: Path | None = None


@dataclass
class Variant:
    name: str
    css: list[Path]
    includes: list[Include]
    metadata: dict[str, str]
    output_suffix: str
    ensure_in_front_matter: list[str]
    mermaid: dict = field(default_factory=dict)
    fonts: list[str] | None = None


@dataclass
class Builtin:
    name: str
    source: Path
    output: Path
    variant: str


@dataclass
class Profile:
    root: Path
    resource_root: Path | None
    variants: dict[str, Variant]
    builtins: dict[str, Builtin]
    mermaid: dict = field(default_factory=dict)
    fonts: list[str] = field(default_factory=list)


@dataclass
class Job:
    source: Path
    output: Path
    markdown: str
    css: list[Path]
    metadata: dict[str, str]
    resource_path: str
    cwd: Path
    format: str = "pdf"
    no_mermaid: bool = False
    no_math: bool = False
    verbose: bool = False
    mermaid: dict = field(default_factory=dict)
    original_markdown: str = ""
    include_sources: list[tuple[Path, str]] = field(default_factory=list)


def which_pandoc() -> str | None:
    return shutil.which("pandoc")


def resolve_weasyprint() -> str | None:
    venv_wp = ENGINE_DIR / ".venv" / "bin" / "weasyprint"
    if venv_wp.is_file() and os.access(venv_wp, os.X_OK):
        return str(venv_wp)
    return shutil.which("weasyprint")


def weasyprint_python(weasy: str) -> Path:
    """Interpreter that can import the WeasyPrint behind the chosen script.

    The venv script pairs with the venv interpreter. A script found on PATH
    names its own interpreter on its #! line.
    """
    weasy_path = Path(weasy)
    venv_python = ENGINE_DIR / ".venv" / "bin" / "python"
    if (
        weasy_path == ENGINE_DIR / ".venv" / "bin" / "weasyprint"
        and venv_python.is_file()
    ):
        return venv_python
    try:
        with weasy_path.open("rb") as handle:
            first = handle.readline(512).decode("utf-8", "replace")
    except OSError:
        first = ""
    if first.startswith("#!"):
        try:
            words = shlex.split(first[2:])
        except ValueError:
            words = []
        if words and Path(words[0]).name == "env":
            words = [word for word in words[1:] if not word.startswith("-")]
            found = shutil.which(words[0]) if words else None
            words = [found] if found else []
        if words and Path(words[0]).is_file():
            return Path(words[0])
    sibling = weasy_path.parent / "python"
    if sibling.is_file():
        return sibling
    return Path(sys.executable)


def _str_list(data: dict, key: str) -> list[str]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"error: {key} must be a list of strings")
    return list(value)


def _resolve_under(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _load_include(root: Path, raw: object) -> Include:
    if not isinstance(raw, dict) or not isinstance(raw.get("file"), str):
        raise SystemExit("error: each include needs a file path")
    path = _resolve_under(root, raw["file"])
    if not path.is_file():
        raise SystemExit(f"error: include fragment missing: {path}")
    close_count = raw.get("close_count", 1)
    if not isinstance(close_count, int) or close_count < 1:
        raise SystemExit("error: close_count must be a positive integer")
    after_marker = raw.get("after_marker")
    after_anchor = raw.get("after_anchor")
    close_tag = raw.get("close_tag", "</div>")
    if after_marker is not None and not isinstance(after_marker, str):
        raise SystemExit("error: after_marker must be a string")
    if after_anchor is not None and not isinstance(after_anchor, str):
        raise SystemExit("error: after_anchor must be a string")
    if not isinstance(close_tag, str) or not close_tag:
        raise SystemExit("error: close_tag must be a string")
    return Include(
        text=path.read_text(encoding="utf-8"),
        source=path,
        skip_if_body_contains=_str_list(raw, "skip_if_body_contains"),
        only_if_body_contains=_str_list(raw, "only_if_body_contains"),
        after_marker=after_marker,
        after_anchor=after_anchor,
        close_tag=close_tag,
        close_count=close_count,
    )


def _load_mermaid(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise SystemExit("error: mermaid must be a table")
    unknown = set(raw) - {"theme", "font_family", "theme_variables"}
    if unknown:
        raise SystemExit(
            f"error: unknown mermaid settings: {', '.join(sorted(unknown))}"
        )
    result = {}
    for key, target in (("theme", "theme"), ("font_family", "fontFamily")):
        if key in raw:
            if not isinstance(raw[key], str) or not raw[key].strip():
                raise SystemExit(f"error: mermaid {key} must be a nonempty string")
            result[target] = raw[key]
    if "theme" in result and result["theme"] not in {
        "default",
        "neutral",
        "dark",
        "forest",
        "base",
    }:
        raise SystemExit("error: unsupported Mermaid theme")
    variables = raw.get("theme_variables", {})
    if not isinstance(variables, dict) or not all(
        isinstance(v, (str, int, float, bool)) for v in variables.values()
    ):
        raise SystemExit("error: mermaid theme_variables must contain scalar values")
    if variables:
        result["themeVariables"] = dict(variables)
    return result


def _load_fonts(raw: dict, label: str) -> list[str] | None:
    """Return the declared font families, or None when the key is absent."""
    if "fonts" not in raw:
        return None
    fonts = raw["fonts"]
    if not isinstance(fonts, list) or not all(
        isinstance(item, str) and item.strip() for item in fonts
    ):
        raise SystemExit(f"error: {label}fonts must be a list of nonempty strings")
    return [item.strip() for item in fonts]


def _load_variant(root: Path, name: str, raw: object) -> Variant:
    if not isinstance(raw, dict):
        raise SystemExit(f"error: variant {name} must be a table")
    css = [_resolve_under(root, item) for item in _str_list(raw, "css")]
    for path in css:
        if not path.is_file():
            raise SystemExit(f"error: CSS missing: {path}")
    includes_raw = raw.get("includes", [])
    if not isinstance(includes_raw, list):
        raise SystemExit(f"error: variant {name} includes must be a list")
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise SystemExit(f"error: variant {name} metadata must be a table of strings")
    suffix = raw.get("output_suffix", "")
    if not isinstance(suffix, str):
        raise SystemExit(f"error: variant {name} output_suffix must be a string")
    return Variant(
        name=name,
        css=css,
        includes=[_load_include(root, item) for item in includes_raw],
        metadata=dict(metadata),
        output_suffix=suffix,
        ensure_in_front_matter=_str_list(raw, "ensure_in_front_matter"),
        mermaid=_load_mermaid(raw.get("mermaid", {})),
        fonts=_load_fonts(raw, f"variant {name} "),
    )


def load_profile(path: Path) -> Profile:
    """Load a profile directory, or a profile.toml file."""
    path = path.expanduser().resolve()
    root = path.parent if path.is_file() else path
    toml_path = path if path.is_file() else root / "profile.toml"
    if not toml_path.is_file():
        raise SystemExit(f"error: profile not found: {toml_path}")
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"error: invalid profile {toml_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: invalid profile {toml_path}")

    resource_root = None
    if "resource_root" in data and data["resource_root"] is not None:
        if not isinstance(data["resource_root"], str):
            raise SystemExit("error: resource_root must be a string")
        resource_root = _resolve_under(root, data["resource_root"])
        if not resource_root.is_dir():
            raise SystemExit(
                f"error: resource_root is not a directory: {resource_root}"
            )

    variants_raw = data.get("variants", {})
    if not isinstance(variants_raw, dict) or not variants_raw:
        raise SystemExit("error: profile needs a [variants] table")
    variants = {
        name: _load_variant(root, name, raw) for name, raw in variants_raw.items()
    }
    if "default" not in variants:
        raise SystemExit("error: profile needs [variants.default]")

    builtins: dict[str, Builtin] = {}
    builtins_raw = data.get("builtins", {})
    if not isinstance(builtins_raw, dict):
        raise SystemExit("error: builtins must be a table")
    for name, raw in builtins_raw.items():
        if not isinstance(raw, dict):
            raise SystemExit(f"error: builtin {name} must be a table")
        source_raw = raw.get("source")
        output_raw = raw.get("output")
        variant = raw.get("variant", "default")
        if not isinstance(source_raw, str) or not isinstance(output_raw, str):
            raise SystemExit(f"error: builtin {name} needs source and output")
        if not isinstance(variant, str) or variant not in variants:
            raise SystemExit(f"error: builtin {name} variant is not in this profile")
        source = _resolve_under(root, source_raw)
        if not source.is_file():
            raise SystemExit(f"error: built-in source missing: {source}")
        builtins[name] = Builtin(
            name=name,
            source=source,
            output=_resolve_under(root, output_raw),
            variant=variant,
        )

    return Profile(
        root=root,
        resource_root=resource_root,
        variants=variants,
        builtins=builtins,
        mermaid=_load_mermaid(data.get("mermaid", {})),
        fonts=_load_fonts(data, "") or [],
    )


FONT_LIST_TIMEOUT = 60


class FontLookupError(Exception):
    """fc-list could not report the installed fonts; the message says why."""


def installed_font_families() -> set[str]:
    """Family names fontconfig reports. Raises FontLookupError when it cannot."""
    # A --format list prints each family unescaped on its own line; plain
    # `fc-list : family` backslash-escapes "-", "," and ":" in names.
    cmd = ["fc-list", "--format", "%{[]family{%{family}\\n}}"]
    try:
        done = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FONT_LIST_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FontLookupError(f"fc-list timed out after {FONT_LIST_TIMEOUT}s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise FontLookupError("fc-list is unavailable") from exc
    if done.returncode != 0:
        raise FontLookupError("fc-list failed")
    return {line.strip() for line in done.stdout.splitlines() if line.strip()}


class FontCheck:
    """Look up installed fonts at most once per call, warning once if impossible."""

    def __init__(self, profile_name: str) -> None:
        self.profile_name = profile_name
        self._families: set[str] | None = None
        self._looked_up = False

    def require(self, fonts: list[str]) -> None:
        if not self._looked_up:
            self._looked_up = True
            try:
                self._families = {name.casefold() for name in installed_font_families()}
            except FontLookupError as exc:
                print(
                    f"warning: {exc}; profile fonts were not checked", file=sys.stderr
                )
        if self._families is None:
            return
        for font in fonts:
            if font.casefold() not in self._families:
                raise SystemExit(
                    f'error: profile {self.profile_name} needs font "{font}", '
                    "which is not installed"
                )


def variant_name(letterhead: bool, confidential: bool) -> str:
    return VARIANT_NAMES[(letterhead, confidential)]


def plain_confidential_variant() -> Variant:
    """Unbranded confidential treatment used when no profile is selected."""
    return Variant(
        name="confidential",
        css=[PLAIN_CONFIDENTIAL_CSS],
        includes=[
            Include(
                text=PLAIN_CONFIDENTIAL_BANNER,
                skip_if_body_contains=["confidential-banner"],
            )
        ],
        metadata={"confidential": "true"},
        output_suffix="-confidential",
        ensure_in_front_matter=["confidential"],
    )


def flags_for_builtin(variant: str) -> tuple[bool, bool]:
    """Built-in variant forces letterhead and/or confidential.

    CLI flags can add either.
    """
    if variant == "default":
        return False, False
    if variant == "letterhead":
        return True, False
    if variant == "confidential":
        return False, True
    if variant == "letterhead-confidential":
        return True, True
    raise SystemExit(f"error: builtin variant cannot be combined with flags: {variant}")


def split_front_matter(md_text: str) -> tuple[str, str]:
    """Return (front_matter_including_delimiters_or_empty, body)."""
    end = front_matter_end(md_text)
    if end is None:
        return "", md_text
    return md_text[:end], md_text[end:]


def ensure_front_matter(md_text: str, keys: list[str], metadata: dict[str, str]) -> str:
    if not keys:
        return md_text
    end = front_matter_end(md_text)
    if end is not None:
        opener = md_text.index("\n")
        closer = md_text.rindex("\n", 0, end)
        block = md_text[opener:closer]
        extra = []
        for key in keys:
            if key.lower() not in block.lower():
                extra.append(f"{key}: {metadata.get(key, 'true')}")
        if not extra:
            return md_text
        block = block.rstrip() + "\n" + "\n".join(extra) + "\n"
        return md_text[:opener] + block + md_text[closer + 1 :]
    bom = "﻿" if md_text.startswith("﻿") else ""
    lines = [f"{key}: {metadata.get(key, 'true')}" for key in keys]
    return bom + "---\n" + "\n".join(lines) + "\n---\n\n" + md_text[len(bom) :]


def include_applies(spec: Include, body: str) -> bool:
    if spec.only_if_body_contains and not all(
        token in body for token in spec.only_if_body_contains
    ):
        return False
    if any(token in body for token in spec.skip_if_body_contains):
        return False
    return True


def insertion_point(body: str, spec: Include) -> int | None:
    """Index just after the matched block, or None to prepend.

    Counts closing tags from the anchor so a nested element does not swallow
    the fragment. If the marker is absent, the caller prepends instead.
    """
    if not spec.after_marker:
        return None
    start = body.find(spec.after_marker)
    if start == -1:
        return None
    search_from = start
    if spec.after_anchor:
        anchor_at = body.find(spec.after_anchor, start)
        if anchor_at != -1:
            search_from = anchor_at
    pos = body.find(spec.close_tag, search_from)
    if pos == -1:
        return None
    for _ in range(spec.close_count - 1):
        nxt = body.find(spec.close_tag, pos + 1)
        if nxt == -1:
            return None
        pos = nxt
    return pos + len(spec.close_tag)


def apply_includes(
    md_text: str,
    includes: list[Include],
    extra_prefixes: list[str],
    verbatim: bool = False,
) -> str:
    fm, body = split_front_matter(md_text)
    prefixes: list[str] = []
    afters: list[tuple[int, str]] = []
    for spec in includes:
        if not include_applies(spec, body):
            continue
        piece = spec.text.strip()
        if not piece:
            continue
        pos = None if verbatim else insertion_point(body, spec)
        if pos is None:
            prefixes.append(piece)
        else:
            afters.append((pos, piece))
    prefixes.extend(piece.strip() for piece in extra_prefixes if piece.strip())
    if not prefixes and not afters:
        return md_text

    built = body
    shift = 0
    grouped: list[tuple[int, list[str]]] = []
    for pos, piece in afters:
        if grouped and grouped[-1][0] == pos:
            grouped[-1][1].append(piece)
        else:
            grouped.append((pos, [piece]))
    for pos, pieces in grouped:
        insert = "\n\n" + "\n\n".join(pieces) + "\n"
        at = pos + shift
        built = built[:at] + insert + built[at:]
        shift += len(insert)

    if not prefixes:
        if not fm:
            return built
        return fm + "\n" + built

    prefix = "\n\n".join(prefixes) + "\n\n"
    if afters:
        return fm + "\n" + prefix + built
    return fm + "\n" + prefix + body.lstrip("\n")


def verbatim_markdown(text: str) -> str:
    """Wrap plain text in one code block so Pandoc keeps every line as written.

    The fence is longer than any backtick run in the text, so no line can close
    it, and the text never starts with a front matter opener.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    if not text.endswith("\n"):
        text += "\n"
    return f"::: quire-verbatim\n{fence}\n{text}{fence}\n:::\n"


def prepare_markdown(
    md_text: str,
    variant: Variant | None,
    extra_prefixes: list[str],
    verbatim: bool = False,
) -> str:
    """Add front matter keys and includes; `verbatim` text has no insertion points."""
    if variant is None:
        if not extra_prefixes:
            return md_text
        return apply_includes(md_text, [], extra_prefixes)
    text = ensure_front_matter(
        md_text, variant.ensure_in_front_matter, variant.metadata
    )
    return apply_includes(text, variant.includes, extra_prefixes, verbatim)


def resolve_source(
    target: str,
    profile: Profile | None,
    cwd: Path,
) -> tuple[Path, Builtin | None]:
    if profile and target in profile.builtins:
        return profile.builtins[target].source, profile.builtins[target]

    path = Path(target).expanduser()
    if not path.is_absolute():
        cand = cwd / path
        if cand.is_file():
            return cand.resolve(), None
        if profile and profile.resource_root:
            cand = profile.resource_root / path
            if cand.is_file():
                return cand.resolve(), None
    if path.is_file():
        return path.resolve(), None
    if profile and profile.builtins:
        known = ", ".join(sorted(profile.builtins))
        raise SystemExit(f"error: file not found: {target} (built-ins: {known})")
    raise SystemExit(f"error: file not found: {target}")


def _deduped_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def plan_job(
    target: str,
    *,
    profile: Profile | None,
    letterhead: bool = False,
    confidential: bool = False,
    output: Path | None = None,
    resource_path: Path | None = None,
    css: list[Path] | None = None,
    no_css: bool = False,
    add_css: list[Path] | None = None,
    include_before: list[Path] | None = None,
    cwd: Path | None = None,
    format: str | None = None,
    no_mermaid: bool = False,
    no_math: bool = False,
    verbose: bool = False,
    font_check: FontCheck | None = None,
) -> Job:
    work = (cwd or Path.cwd()).resolve()
    source, builtin = resolve_source(target, profile, work)
    if builtin is None and source.suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise SystemExit(
            "error: target must be a .md, .markdown, or .txt file or a built-in name"
        )

    if builtin is not None:
        forced_letterhead, forced_confidential = flags_for_builtin(builtin.variant)
        letterhead = letterhead or forced_letterhead
        confidential = confidential or forced_confidential

    variant: Variant | None = None
    if profile is not None:
        name = variant_name(letterhead, confidential)
        if name not in profile.variants:
            raise SystemExit(f"error: profile has no {name!r} variant")
        variant = profile.variants[name]
    elif letterhead:
        raise SystemExit("error: --letterhead requires a profile")
    elif confidential:
        if not PLAIN_CONFIDENTIAL_CSS.is_file():
            raise SystemExit(f"error: CSS missing: {PLAIN_CONFIDENTIAL_CSS}")
        variant = plain_confidential_variant()

    output_format = format or (
        output.suffix.lower()[1:]
        if output and output.suffix.lower() in {".pdf", ".html"}
        else "pdf"
    )
    if output_format not in {"pdf", "html"}:
        raise SystemExit("error: format must be html or pdf")
    extra: list[str] = []
    include_sources = []
    for raw in include_before or []:
        path = raw if raw.is_absolute() else (work / raw)
        path = path.resolve()
        if not path.is_file():
            raise SystemExit(f"error: include file missing: {path}")
        content = path.read_text(encoding="utf-8")
        extra.append(content)
        include_sources.append((path, content))

    if output is None:
        out = builtin.output if builtin is not None else source.with_suffix(".pdf")
        out = out.with_suffix(f".{output_format}")
        if variant and variant.output_suffix:
            out = out.with_name(out.stem + variant.output_suffix + out.suffix)
    else:
        out = output if output.is_absolute() else (work / output).resolve()
    if out.resolve() == source:
        raise SystemExit(f"error: output would overwrite the source: {source}")

    def checked(raw: Path) -> Path:
        path = raw if raw.is_absolute() else (work / raw)
        path = path.resolve()
        if not path.is_file():
            raise SystemExit(f"error: CSS missing: {path}")
        return path

    if no_css:
        css_files: list[Path] = []
    elif css:
        css_files = [checked(raw) for raw in css]
    elif variant is not None and variant.css:
        css_files = list(variant.css)
    else:
        if not PLAIN_CSS.is_file():
            raise SystemExit(f"error: CSS missing: {PLAIN_CSS}")
        css_files = [PLAIN_CSS]
    if not no_css:
        css_files.extend(checked(raw) for raw in add_css or [])

    # Only PDF embeds installed fonts; HTML leaves the font choice to the reader's
    # machine.
    if profile is not None and variant is not None and output_format == "pdf":
        fonts = variant.fonts if variant.fonts is not None else profile.fonts
        if fonts:
            (font_check or FontCheck(profile.root.name)).require(fonts)

    text = source.read_text(encoding="utf-8")
    verbatim = source.suffix.lower() == ".txt"
    if verbatim:
        text = verbatim_markdown(text)
    prepared = prepare_markdown(text, variant, extra, verbatim)

    roots: list[Path] = []
    if resource_path is not None:
        roots.append(
            resource_path if resource_path.is_absolute() else work / resource_path
        )
    elif profile is not None and profile.resource_root is not None:
        roots.append(profile.resource_root)
    else:
        roots.append(work)
    roots.append(source.parent)
    roots.append(ENGINE_DIR)
    if profile is not None:
        roots.append(profile.root)
    roots = _deduped_paths(roots)

    job_cwd = roots[0]
    metadata = dict(variant.metadata) if variant is not None else {}
    mermaid = {"theme": "neutral", "fontFamily": "Helvetica, Arial, sans-serif"}
    for settings in (
        profile.mermaid if profile else {},
        variant.mermaid if variant else {},
    ):
        variables = {
            **mermaid.get("themeVariables", {}),
            **settings.get("themeVariables", {}),
        }
        mermaid.update(settings)
        if variables:
            mermaid["themeVariables"] = variables
    if variant:
        body = split_front_matter(text)[1]
        include_sources.extend(
            (item.source, item.text)
            for item in variant.includes
            if item.source and include_applies(item, body)
        )
    return Job(
        source=source,
        output=out,
        markdown=prepared,
        css=css_files,
        metadata=metadata,
        resource_path=os.pathsep.join(str(path) for path in roots),
        cwd=job_cwd,
        format=output_format,
        no_mermaid=no_mermaid,
        no_math=no_math,
        verbose=verbose,
        mermaid=mermaid,
        original_markdown=text,
        include_sources=include_sources,
    )


def pandoc_env(weasy: str) -> dict[str, str]:
    env = os.environ.copy()
    weasy_path = Path(weasy)
    if weasy_path.parent.name == "bin":
        env["PATH"] = str(weasy_path.parent) + os.pathsep + env.get("PATH", "")
    brew_lib = Path("/opt/homebrew/lib")
    if not brew_lib.is_dir():
        brew_lib = Path("/usr/local/lib")
    if brew_lib.is_dir():
        prev = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        env["DYLD_FALLBACK_LIBRARY_PATH"] = str(brew_lib) + (
            os.pathsep + prev if prev else ""
        )
    return env


def _run(
    cmd: list[str],
    job: Job,
    *,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    if job.verbose:
        print("running:", shlex.join(cmd), file=sys.stderr)
    try:
        result = subprocess.run(
            cmd,
            input=input,
            capture_output=True,
            text=True,
            check=True,
            cwd=str(job.cwd),
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (
            exc.stderr.strip()
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        raise SystemExit(f"error: {Path(cmd[0]).name} failed: {detail}") from exc
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.stdout


PAGE_BREAK_TEX = re.compile(
    r"\\(?:newpage|pagebreak|clearpage)(?:\[[^\]]*\])?(?:\{\})?"
)
# The head of a macro definition that Pandoc already expanded: \newcommand{\x}[n],
# \def\x#1, \DeclareMathOperator*{\x}. The brace-balanced body follows it.
DEFINITION_HEAD = re.compile(
    r"(?:\\(?:(?:new|renew|provide)command|DeclareMathOperator)\*?\s*"
    r"(?:\{\s*\\(?:[A-Za-z@]+|.)\s*\}|\\(?:[A-Za-z@]+|.))(?:\s*\[[^\]]*\]){0,2}"
    r"|\\g?def\s*\\(?:[A-Za-z@]+|.)(?:#\d)*)\s*",
    re.S,
)
# LaTeX layout and preamble commands with no effect in the HTML/PDF path. A
# definition of one is not a silent macro definition: it keeps its warning.
LAYOUT_COMMANDS = (
    "baselinestretch",
    "arraystretch",
    "familydefault",
    "rmdefault",
    "sfdefault",
    "ttdefault",
    "linespread",
)
LAYOUT_DEFINITION = re.compile(
    r"\\[A-Za-z]+\*?\s*\{?\s*\\(?:" + "|".join(LAYOUT_COMMANDS) + r")(?![A-Za-z@])"
)
TEX_BRACE = re.compile(r"\\.|[{}]", re.S)
PAGE_BREAK_CSS = (
    ".quire-page-break { break-after:page; page-break-after:always; } "
    "span.quire-page-break { display:block; height:0; }"
)
# Inline math SVG is sized in ex units by the renderer, so it follows the text.
# Only display math needs layout rules.
MATH_CSS = (
    "<style>.quire-math-display { display:block; text-align:center; margin:1em 0; "
    "break-inside:avoid; page-break-inside:avoid; } "
    ".quire-math-display > svg { max-width:100%; height:auto; }</style>"
)


def plain_text(node) -> str:
    """Text of a Pandoc metadata value, with math as its TeX source."""
    if isinstance(node, list):
        return "".join(plain_text(child) for child in node)
    if not isinstance(node, dict):
        return ""
    kind, content = node.get("t"), node.get("c")
    if kind == "Str" or kind == "MetaString":
        return content
    if kind in ("Space", "SoftBreak", "LineBreak"):
        return " "
    if kind == "Math":
        return content[1]
    if kind in ("Code", "RawInline"):
        return content[1]
    return plain_text(content)


def balanced_group(text: str, start: int) -> int | None:
    """End of the {...} group that begins at `start`, or None if it is not closed."""
    if text[start : start + 1] != "{":
        return None
    depth = 0
    for token in TEX_BRACE.finditer(text, start):
        depth += {"{": 1, "}": -1}.get(token.group(), 0)
        if depth == 0:
            return token.end()
    return None


def without_definitions(text: str) -> str:
    """Raw TeX with macro definitions and comments removed."""
    rest = []
    i = 0
    while i < len(text):
        head = DEFINITION_HEAD.match(text, i)
        silent = head and not LAYOUT_DEFINITION.match(head.group())
        end = balanced_group(text, head.end()) if silent else None
        if end is not None:
            i = end
        elif text[i] == "%":
            newline = text.find("\n", i)
            i = newline if newline >= 0 else len(text)
        else:
            width = 2 if text[i] == "\\" else 1
            rest.append(text[i : i + width])
            i += width
    return "".join(rest).strip()


def convert_raw_tex(node, dropped: list[str]) -> None:
    """Turn page-break TeX into raw HTML and drop other raw TeX, in place.

    The HTML writer discards raw TeX, so a page break written for LaTeX would
    otherwise vanish. Dropped commands are appended to `dropped`.
    """
    if isinstance(node, list):
        for child in list(node):
            if (
                isinstance(child, dict)
                and child.get("t") in ("RawBlock", "RawInline")
                and child["c"][0] in ("tex", "latex")
            ):
                kind, text = child["c"]
                # Pandoc's Markdown reader emits the definitions it expanded as
                # "tex"; a {=latex} raw attribute is not expanded, so it still warns.
                if kind == "tex":
                    text = without_definitions(text)
                if PAGE_BREAK_TEX.fullmatch(text.strip()):
                    tag = "div" if child["t"] == "RawBlock" else "span"
                    child["c"] = ["html", f'<{tag} class="quire-page-break"></{tag}>']
                else:
                    if kind != "tex" or text:
                        command = re.search(r"\\[A-Za-z]+|\\.", text)
                        dropped.append(command.group(0) if command else text.strip())
                    node.remove(child)
            else:
                convert_raw_tex(child, dropped)
                if (
                    isinstance(child, dict)
                    and child.get("t") in ("Para", "Plain")
                    and child["c"] == []
                ):
                    node.remove(child)
    elif isinstance(node, dict):
        for child in node.values():
            convert_raw_tex(child, dropped)


def render(job: Job) -> None:
    from quire_html import Resources
    from quire_math import math_nodes, render_math
    from quire_mermaid import code_blocks, render_blocks, source_locations

    pandoc = which_pandoc()
    if not pandoc:
        raise SystemExit(
            "error: missing pandoc; install it before building PDF or HTML"
        )
    weasy = resolve_weasyprint() if job.format == "pdf" else None
    if job.format == "pdf" and not weasy:
        raise SystemExit(
            "error: missing WeasyPrint; run python3 install.py\n" + INSTALL_HELP
        )

    roots = [Path(path) for path in job.resource_path.split(os.pathsep)]
    resources = Resources(roots)
    # CSS is embedded before the renderer sees it, so fonts never need fetching.
    css = []
    for path in job.css:
        resources.active.add(path.resolve())
        try:
            css.append(resources.css(path.read_text(encoding="utf-8"), path.parent))
        finally:
            resources.active.remove(path.resolve())

    def parse(text):
        return json.loads(
            _run([pandoc, "-f", "markdown", "-t", "json"], job, input=text)
        )

    ast = parse(job.markdown)
    locations = (
        source_locations(job, parse)
        if not job.no_mermaid and any(code_blocks(ast))
        else None
    )
    dropped: list[str] = []
    convert_raw_tex(ast["blocks"], dropped)
    convert_raw_tex(ast.get("meta", {}), dropped)
    if dropped:
        print(
            f"warning: {job.source}: dropped raw TeX (including {dropped[0]}); "
            "only \\newpage, \\pagebreak, and \\clearpage are converted",
            file=sys.stderr,
        )
    has_diagrams = render_blocks(ast, job, "\n".join(css), locations=locations)
    meta = ast.get("meta", {})
    title_text = (
        plain_text(meta["title"])
        if "title" in meta
        and "pagetitle" not in meta
        and any(math_nodes(meta["title"]))
        else ""
    )
    has_math = render_math(ast, job)
    cmd = [pandoc, "-f", "json", "-t", "html5", "--standalone", "--metadata", "lang=en"]
    if "title" not in ast.get("meta", {}) and "pagetitle" not in ast.get("meta", {}):
        cmd.extend(["--metadata", f"pagetitle={job.source.stem}"])
    elif has_math and title_text.strip():
        # The title's math is now SVG, which a <title> element cannot hold.
        cmd.extend(["--metadata", f"pagetitle={title_text}"])
    # An existing stylesheet suppresses Pandoc's default document CSS.
    for path in job.css:
        cmd.append(f"--css={path}")
    for key, value in job.metadata.items():
        cmd.extend(["--metadata", f"{key}={value}"])
    document = _run(cmd, job, input=json.dumps(ast))
    document = resources.document(document)
    # First in the head, so a stylesheet can override the rule.
    document = document.replace("<head>", f"<head>\n<style>{PAGE_BREAK_CSS}</style>", 1)
    if has_diagrams:
        family = "".join(
            char for char in job.mermaid.get("fontFamily", "") if char not in "<>{}"
        )
        family = family or "Helvetica, Arial, sans-serif"
        safety = (
            "<style>.quire-diagram { break-inside:avoid; page-break-inside:avoid; "
            "break-before:avoid; page-break-before:avoid; "
            "max-width:100%; margin:1em 0; line-height:0; } "
            ".quire-diagram > svg { display:block; max-width:100%; height:auto; } "
            f".quire-diagram text {{ font-family:{family}; }}</style>"
        )
        document = document.replace("</head>", safety + "\n</head>", 1)
    if has_math:
        document = document.replace("</head>", MATH_CSS + "\n</head>", 1)
    if job.format == "html":
        policy = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; "
            "font-src data:; media-src data:; base-uri 'none'; form-action 'none'"
        )
        document = document.replace(
            "<head>",
            '<head>\n<meta http-equiv="Content-Security-Policy" '
            f'content="{policy}">',
            1,
        )
    job.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".quire-", dir=job.output.parent
    ) as temporary:
        folder = Path(temporary)
        prepared = folder / "document.html"
        prepared.write_text(document, encoding="utf-8")
        if job.format == "pdf":
            interpreter = weasyprint_python(weasy)
            result = folder / "document.pdf"
            cmd = [
                str(interpreter),
                str(ENGINE_DIR / "quire_pdf.py"),
                str(prepared),
                str(result),
            ]
            if job.verbose:
                cmd.append("--verbose")
            _run(cmd, job, env=pandoc_env(weasy))
        else:
            result = prepared
        os.replace(result, job.output)
    print(f"wrote {job.output}")


@dataclass
class UserConfig:
    default: str
    profiles: dict[str, Path]
    path: Path | None = None


def user_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "quire" / "config.toml"


def load_user_config(path: Path | None = None) -> UserConfig:
    """Load ~/.config/quire/config.toml. A missing file is an empty config."""
    cfg_path = user_config_path() if path is None else path
    if not cfg_path.is_file():
        return UserConfig(default="", profiles={}, path=cfg_path)
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"error: invalid config {cfg_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: invalid config {cfg_path}")
    default = data.get("default", "")
    if default is None:
        default = ""
    if not isinstance(default, str):
        raise SystemExit("error: config default must be a string")
    profiles_raw = data.get("profiles", {})
    if not isinstance(profiles_raw, dict):
        raise SystemExit("error: config [profiles] must be a table of paths")
    profiles: dict[str, Path] = {}
    for name, raw in profiles_raw.items():
        if not isinstance(raw, str) or not raw.strip():
            raise SystemExit(f"error: config profile {name} must be a path")
        profiles[name] = Path(raw).expanduser()
    return UserConfig(default=default.strip(), profiles=profiles, path=cfg_path)


def unknown_config_message(
    cfg: UserConfig, name: str, source: str = "", kind: str = "error"
) -> str:
    """Say a config name is not registered, where it was looked up, and how to
    fix it.
    """
    known = ", ".join(sorted(cfg.profiles)) or "(none)"
    origin = f" from {source}" if source else ""
    if kind == "warning":
        lines = [
            f"warning: preferred config {name!r} is not registered (known: {known})"
        ]
    else:
        lines = [f"error: unknown config {name!r}{origin} (known: {known})"]
    if cfg.path is not None:
        note = "" if cfg.path.is_file() else " (missing)"
        lines.append(f"config file: {cfg.path}{note}")
    if source == "the config default":
        lines.append("fix it with: quire default <name>, or quire default --clear")
    else:
        hint = f"quire install <profile-dir> --name {shlex.quote(name)}"
        lines.append(f"register it with: {hint}")
    return "\n".join(lines)


def profile_from_name(cfg: UserConfig, name: str, source: str = "") -> Path:
    """Look up a registered profile; source says where an implicit name came from."""
    if not name:
        raise SystemExit("error: config name is empty")
    if name not in cfg.profiles:
        raise SystemExit(unknown_config_message(cfg, name, source))
    return cfg.profiles[name]


def _check_config_profile(path: Path, cfg: UserConfig | None) -> None:
    """Name the config when its registered profile has gone missing."""
    if cfg is None:
        return
    names = sorted(
        name for name, registered in cfg.profiles.items() if registered == path
    )
    if not names:
        return
    resolved = path.expanduser()
    toml_path = resolved if resolved.is_file() else resolved / "profile.toml"
    if toml_path.is_file():
        return
    raise SystemExit(
        f"error: config {names[0]!r} points to {path}, which has no profile.toml; "
        f"re-register with: quire install <profile dir> --name {names[0]}"
    )


def config_name_arg(text: str) -> str:
    """Trim a --config or --prefer-config name; a blank one then fails as empty."""
    return text.strip()


def _parser(
    profile: Profile | None,
    description: str,
    prog: str | None,
    user_config: UserConfig | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description, prog=prog)
    builtin_help = ""
    if profile and profile.builtins:
        builtin_help = " Built-ins: " + ", ".join(sorted(profile.builtins)) + "."
    parser.add_argument(
        "target",
        nargs="+",
        help="Paths to .md or .txt files, or built-in names from the profile."
        + builtin_help,
    )
    known = ""
    if user_config and user_config.profiles:
        known = " Known: " + ", ".join(sorted(user_config.profiles)) + "."
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="Profile directory or profile.toml (overrides --config)",
    )
    named = parser.add_mutually_exclusive_group()
    named.add_argument(
        "--config",
        type=config_name_arg,
        default=None,
        help="Named profile from the user config." + known,
    )
    named.add_argument(
        "--prefer-config",
        type=config_name_arg,
        default=None,
        metavar="NAME",
        help="Like --config, but warn and carry on if NAME is not registered",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore the default profile and use the plain style",
    )
    parser.add_argument(
        "--confidential",
        action="store_true",
        help=(
            "Mark the document confidential. With a profile, select its "
            "confidential variant; otherwise use the plain confidential style"
        ),
    )
    parser.add_argument(
        "--letterhead",
        action="store_true",
        help="Select the profile's letterhead variant",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output path; .html or .pdf selects the format "
            "(default: PDF beside the source)"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("html", "pdf"),
        default=None,
        help="Output format; overrides the output extension",
    )
    parser.add_argument(
        "--no-mermaid", action="store_true", help="Keep Mermaid diagrams as code blocks"
    )
    parser.add_argument(
        "--no-math",
        action="store_true",
        help="Keep Pandoc's default output for TeX math instead of rendering SVG",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print commands, diagram and math rendering, and cache hits to stderr",
    )
    parser.add_argument(
        "--resource-path",
        type=Path,
        default=None,
        help=(
            "Pandoc resource root "
            "(default: profile resource_root, else the working directory)"
        ),
    )
    parser.add_argument(
        "--css",
        action="append",
        type=Path,
        default=None,
        help="Stylesheet. Repeatable. Replaces the profile CSS and plain.css",
    )
    parser.add_argument(
        "--add-css",
        action="append",
        type=Path,
        default=None,
        help="Stylesheet appended after the selected CSS. Repeatable",
    )
    parser.add_argument(
        "--no-css",
        action="store_true",
        help="Pass no stylesheet to pandoc",
    )
    parser.add_argument(
        "--include-before",
        action="append",
        type=Path,
        default=None,
        help="HTML fragment to insert before the document body. Repeatable",
    )
    return parser


def resolve_profile_arg(
    raw: Path | None,
    default_profile: Path | None,
    *,
    config_name: str | None = None,
    prefer_name: str | None = None,
    no_config: bool = False,
    user_config: UserConfig | None = None,
    environ: dict[str, str] | None = None,
    pending: list[str] | None = None,
    selected: list[str] | None = None,
) -> Path | None:
    """Pick a profile path.

    An entry-point default (build.py) skips the user config. Otherwise:
    --profile, --no-config, --config, --prefer-config, QUIRE_CONFIG,
    QUIRE_PROFILE, then the config file's default. An unregistered
    --prefer-config name adds one warning to pending (printed at once if
    pending is None) and falls through to the rest. A selection by config name
    appends that name to selected.
    """
    if raw is not None:
        return raw
    if default_profile is not None:
        return default_profile
    if no_config:
        return None
    cfg = (
        user_config if user_config is not None else UserConfig(default="", profiles={})
    )
    env = os.environ if environ is None else environ
    used = selected if selected is not None else []
    if config_name is not None:
        if not config_name:
            raise SystemExit("error: config name is empty")
        used.append(config_name)
        return profile_from_name(cfg, config_name)
    if prefer_name is not None:
        if not prefer_name:
            raise SystemExit("error: config name is empty")
        if prefer_name in cfg.profiles:
            used.append(prefer_name)
            return cfg.profiles[prefer_name]
        message = unknown_config_message(cfg, prefer_name, kind="warning")
        if pending is None:
            print(message, file=sys.stderr)
        else:
            pending.append(message)
    env_name = env.get("QUIRE_CONFIG", "").strip()
    if env_name:
        used.append(env_name)
        return profile_from_name(cfg, env_name, "QUIRE_CONFIG")
    env_path = env.get("QUIRE_PROFILE", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    if cfg.default:
        used.append(cfg.default)
        return profile_from_name(cfg, cfg.default, "the config default")
    return None


# Anything that can open a container where an indented fence is not code.
CONTAINER_OPENERS = re.compile(
    r"^\s*(?:[-+*]|\d+[.)]|[A-Za-z#]+[.)]|\(\S+\)|:)\s|^\[\^"
)


def is_indented_code(text: str, line: int) -> bool:
    """True for a fence indented 4+ columns with no container opener before it."""
    lines = text.splitlines()
    opener = re.sub(r"^(?: {0,3}>[ \t]?)+", "", lines[line - 1]).expandtabs(4)
    if len(opener) - len(opener.lstrip(" ")) < 4:
        return False
    return not any(CONTAINER_OPENERS.match(earlier) for earlier in lines[: line - 1])


def check_mermaid(jobs: list[Job]) -> None:
    """Fail before any render if a diagram needs a renderer that is missing.

    Fences are found in the source text, so a false positive only costs the hint.
    """
    first = next(
        (
            (path, line)
            for job in jobs
            if not job.no_mermaid
            for path, text in [
                (job.source, job.original_markdown or job.markdown),
                *job.include_sources,
            ]
            for _, line in fence_locations(text)
            if not is_indented_code(text, line)
        ),
        None,
    )
    if first is None:
        return
    try:
        require_renderer()
    except SystemExit as exc:
        raise SystemExit(f"error: {first[0]}: line {first[1]}: {exc}") from exc


def main(
    argv: list[str] | None = None,
    *,
    description: str | None = None,
    default_profile: Path | None = None,
    prog: str | None = None,
    user_config: UserConfig | None = None,
) -> None:
    if description is None:
        description = "Build PDF or self-contained HTML from Markdown."
    if prog is None:
        prog = os.environ.get("QUIRE_PROG") or "quire"
    args_in = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args_in:
        print(f"{prog} {__version__}")
        return
    # build.py pins a profile and does not grow these subcommands.
    if default_profile is None and args_in[:1] in (
        ["install"],
        ["configs"],
        ["default"],
        ["remove"],
    ):
        import install as quire_install

        if args_in[0] == "install":
            quire_install.cmd_install(args_in[1:])
        elif args_in[0] == "default":
            quire_install.cmd_default(args_in[1:])
        elif args_in[0] == "remove":
            quire_install.cmd_remove(args_in[1:])
        else:
            quire_install.cmd_configs(args_in[1:])
        return
    # build.py pins a profile and does not consult the home config.
    cfg = (
        None
        if default_profile is not None
        else (user_config if user_config is not None else load_user_config())
    )
    pre = argparse.ArgumentParser(prog=prog, add_help=False)
    pre.add_argument("--profile", type=Path, default=None)
    named = pre.add_mutually_exclusive_group()
    named.add_argument("--config", type=config_name_arg, default=None)
    named.add_argument("--prefer-config", type=config_name_arg, default=None)
    pre.add_argument("--no-config", action="store_true")
    known, _rest = pre.parse_known_args(args_in)
    # Held back so --help and argparse errors stay quiet; a fallback error still
    # shows it.
    pending: list[str] = []
    try:
        profile_path = resolve_profile_arg(
            known.profile,
            default_profile,
            config_name=known.config,
            prefer_name=known.prefer_config,
            no_config=known.no_config,
            user_config=cfg,
            pending=pending,
        )
        if profile_path is not None and known.profile is None:
            _check_config_profile(profile_path, cfg)
        profile = load_profile(profile_path) if profile_path is not None else None
    except SystemExit:
        for message in pending:
            print(message, file=sys.stderr)
        raise

    args = _parser(profile, description, prog, cfg).parse_args(args_in)
    for message in pending:
        print(message, file=sys.stderr)
    used: list[str] = []
    # parse_args already exited on --help. Reload only if the chosen path changed.
    chosen = resolve_profile_arg(
        args.profile,
        default_profile,
        config_name=args.config,
        prefer_name=args.prefer_config,
        no_config=args.no_config,
        user_config=cfg,
        pending=[],
        selected=used,
    )
    if chosen != profile_path:
        if chosen is not None and args.profile is None:
            _check_config_profile(chosen, cfg)
        profile = load_profile(chosen) if chosen is not None else None
    if (
        chosen is None
        and args.letterhead
        and args.prefer_config
        and args.profile is None
        and default_profile is None
        and not args.no_config
    ):
        raise SystemExit(
            f"error: --letterhead requires a profile, and the preferred config "
            f"{args.prefer_config!r} is not registered"
        )

    if args.output is not None and len(args.target) > 1:
        raise SystemExit("error: --output needs a single target")
    # Plan every target first so a bad one fails before any output is written.
    font_check = None
    if profile is not None:
        font_check = FontCheck(used[0] if used else profile.root.name)
    jobs = [
        plan_job(
            target,
            profile=profile,
            letterhead=args.letterhead,
            confidential=args.confidential,
            output=args.output,
            resource_path=args.resource_path,
            css=args.css,
            no_css=args.no_css,
            add_css=args.add_css,
            include_before=args.include_before,
            format=args.format,
            no_mermaid=args.no_mermaid,
            no_math=args.no_math,
            verbose=args.verbose,
            font_check=font_check,
        )
        for target in args.target
    ]
    sources: dict[Path, Path] = {}
    for job in jobs:
        if job.output in sources:
            raise SystemExit(
                f"error: {sources[job.output]} and {job.source} "
                f"would both write {job.output}"
            )
        sources[job.output] = job.source
    check_mermaid(jobs)
    for job in jobs:
        render(job)


if __name__ == "__main__":
    main()
