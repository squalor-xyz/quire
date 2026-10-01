#!/usr/bin/env python3
"""
Quire: build a PDF from Markdown via pandoc + WeasyPrint.

With no config, the document uses plain.css only (page size, type, page number).
`--confidential` without a profile uses plain-confidential.css and a running
Confidential mark. A named config points at a profile directory
(profile.toml, CSS, HTML fragments).

Examples:
  quire notes/foo.md
  quire notes/foo.md --css extra.css -o out/foo.pdf
  quire --no-config notes/foo.md --confidential
  quire --config squalor notes/foo.md --letterhead
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class Variant:
    name: str
    css: list[Path]
    includes: list[Include]
    metadata: dict[str, str]
    output_suffix: str
    ensure_in_front_matter: list[str]


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


@dataclass
class Job:
    source: Path
    output: Path
    markdown: str
    css: list[Path]
    metadata: dict[str, str]
    resource_path: str
    cwd: Path


def which_pandoc() -> str | None:
    return shutil.which("pandoc")


def resolve_weasyprint() -> str | None:
    venv_wp = ENGINE_DIR / ".venv" / "bin" / "weasyprint"
    if venv_wp.is_file() and os.access(venv_wp, os.X_OK):
        return str(venv_wp)
    return shutil.which("weasyprint")


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
        skip_if_body_contains=_str_list(raw, "skip_if_body_contains"),
        only_if_body_contains=_str_list(raw, "only_if_body_contains"),
        after_marker=after_marker,
        after_anchor=after_anchor,
        close_tag=close_tag,
        close_count=close_count,
    )


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
            raise SystemExit(f"error: resource_root is not a directory: {resource_root}")

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
    """Built-in variant forces letterhead and/or confidential. CLI flags can add either."""
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
    if not md_text.startswith("---"):
        return "", md_text
    end = md_text.find("\n---", 3)
    if end == -1:
        return "", md_text
    end += len("\n---")
    return md_text[:end], md_text[end:]


def ensure_front_matter(md_text: str, keys: list[str], metadata: dict[str, str]) -> str:
    if not keys:
        return md_text
    if md_text.startswith("---"):
        end = md_text.find("\n---", 3)
        if end != -1:
            block = md_text[3:end]
            extra = []
            for key in keys:
                if key.lower() not in block.lower():
                    extra.append(f"{key}: {metadata.get(key, 'true')}")
            if not extra:
                return md_text
            block = block.rstrip() + "\n" + "\n".join(extra) + "\n"
            return "---" + block + md_text[end:]
        return md_text
    lines = [f"{key}: {metadata.get(key, 'true')}" for key in keys]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + md_text


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


def apply_includes(md_text: str, includes: list[Include], extra_prefixes: list[str]) -> str:
    fm, body = split_front_matter(md_text)
    prefixes: list[str] = []
    afters: list[tuple[int, str]] = []
    for spec in includes:
        if not include_applies(spec, body):
            continue
        piece = spec.text.strip()
        if not piece:
            continue
        pos = insertion_point(body, spec)
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


def prepare_markdown(md_text: str, variant: Variant | None, extra_prefixes: list[str]) -> str:
    if variant is None:
        if not extra_prefixes:
            return md_text
        return apply_includes(md_text, [], extra_prefixes)
    text = ensure_front_matter(md_text, variant.ensure_in_front_matter, variant.metadata)
    return apply_includes(text, variant.includes, extra_prefixes)


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
        raise SystemExit(f"error: markdown file not found: {target} (built-ins: {known})")
    raise SystemExit(f"error: markdown file not found: {target}")


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
    include_before: list[Path] | None = None,
    cwd: Path | None = None,
) -> Job:
    work = (cwd or Path.cwd()).resolve()
    source, builtin = resolve_source(target, profile, work)
    if builtin is None and source.suffix.lower() not in {".md", ".markdown"}:
        raise SystemExit("error: target must be a markdown file or a built-in name")

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

    extra: list[str] = []
    for raw in include_before or []:
        path = raw if raw.is_absolute() else (work / raw)
        path = path.resolve()
        if not path.is_file():
            raise SystemExit(f"error: include file missing: {path}")
        extra.append(path.read_text(encoding="utf-8"))

    if output is None:
        out = builtin.output if builtin is not None else source.with_suffix(".pdf")
        if variant and variant.output_suffix:
            out = out.with_name(out.stem + variant.output_suffix + out.suffix)
    else:
        out = output if output.is_absolute() else (work / output).resolve()

    if no_css:
        css_files: list[Path] = []
    elif css:
        css_files = []
        for raw in css:
            path = raw if raw.is_absolute() else (work / raw)
            path = path.resolve()
            if not path.is_file():
                raise SystemExit(f"error: CSS missing: {path}")
            css_files.append(path)
    elif variant is not None and variant.css:
        css_files = list(variant.css)
    else:
        if not PLAIN_CSS.is_file():
            raise SystemExit(f"error: CSS missing: {PLAIN_CSS}")
        css_files = [PLAIN_CSS]

    text = source.read_text(encoding="utf-8")
    prepared = prepare_markdown(text, variant, extra)

    roots: list[Path] = []
    if resource_path is not None:
        roots.append(resource_path if resource_path.is_absolute() else work / resource_path)
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
    return Job(
        source=source,
        output=out,
        markdown=prepared,
        css=css_files,
        metadata=metadata,
        resource_path=":".join(str(path) for path in roots),
        cwd=job_cwd,
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
        env["DYLD_FALLBACK_LIBRARY_PATH"] = str(brew_lib) + (os.pathsep + prev if prev else "")
    return env


def render(job: Job) -> None:
    pandoc = which_pandoc()
    weasy = resolve_weasyprint()
    if not pandoc or not weasy:
        print(INSTALL_HELP, file=sys.stderr)
        missing = []
        if not pandoc:
            missing.append("pandoc")
        if not weasy:
            missing.append("weasyprint")
        raise SystemExit(f"error: missing required tool(s): {', '.join(missing)}")

    job.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quire-") as tmp:
        tmp_md = Path(tmp) / "doc.md"
        tmp_md.write_text(job.markdown, encoding="utf-8")
        cmd = [
            pandoc,
            str(tmp_md),
            "-f",
            "markdown",
            "-t",
            "html5",
            "-o",
            str(job.output),
            f"--pdf-engine={weasy}",
            f"--resource-path={job.resource_path}",
            "--standalone",
            "-V",
            "margin-top=20",
            "-V",
            "margin-bottom=20",
            "-V",
            "margin-left=18",
            "-V",
            "margin-right=18",
            "--metadata",
            "lang=en",
        ]
        for css in job.css:
            cmd.append(f"--css={css}")
        for key, value in job.metadata.items():
            cmd.extend(["--metadata", f"{key}={value}"])
        print("running:", " ".join(cmd), file=sys.stderr)
        try:
            subprocess.run(cmd, check=True, env=pandoc_env(weasy), cwd=str(job.cwd))
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"error: pandoc failed with exit {exc.returncode}") from exc
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


def profile_from_name(cfg: UserConfig, name: str) -> Path:
    if not name:
        raise SystemExit("error: config name is empty")
    if name not in cfg.profiles:
        known = ", ".join(sorted(cfg.profiles)) or "(none)"
        raise SystemExit(f"error: unknown config {name!r} (known: {known})")
    return cfg.profiles[name]


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
        help="Path to a .md file, or a built-in name from the profile." + builtin_help,
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
    parser.add_argument(
        "--config",
        default=None,
        help="Named profile from the user config." + known,
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
        help="Output PDF path (default: beside the source, or the built-in path)",
    )
    parser.add_argument(
        "--resource-path",
        type=Path,
        default=None,
        help="Pandoc resource root (default: profile resource_root, else the working directory)",
    )
    parser.add_argument(
        "--css",
        action="append",
        type=Path,
        default=None,
        help="Stylesheet. Repeatable. Replaces the profile CSS and plain.css",
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
    no_config: bool = False,
    user_config: UserConfig | None = None,
    environ: dict[str, str] | None = None,
) -> Path | None:
    """Pick a profile path.

    An entry-point default (build.py) skips the user config. Otherwise:
    --profile, --no-config, --config, QUIRE_CONFIG, QUIRE_PROFILE, then
    the config file's default.
    """
    if raw is not None:
        return raw
    if default_profile is not None:
        return default_profile
    if no_config:
        return None
    cfg = user_config if user_config is not None else UserConfig(default="", profiles={})
    env = os.environ if environ is None else environ
    if config_name:
        return profile_from_name(cfg, config_name)
    env_name = env.get("QUIRE_CONFIG", "").strip()
    if env_name:
        return profile_from_name(cfg, env_name)
    env_path = env.get("QUIRE_PROFILE", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    if cfg.default:
        return profile_from_name(cfg, cfg.default)
    return None


def main(
    argv: list[str] | None = None,
    *,
    description: str | None = None,
    default_profile: Path | None = None,
    prog: str | None = None,
    user_config: UserConfig | None = None,
) -> None:
    if description is None:
        description = "Build a PDF from Markdown (pandoc + WeasyPrint)."
    if prog is None:
        prog = os.environ.get("QUIRE_PROG") or "quire"
    args_in = list(sys.argv[1:] if argv is None else argv)
    # build.py pins a profile and does not grow these subcommands.
    if default_profile is None and args_in[:1] in (["install"], ["configs"]):
        import install as quire_install

        if args_in[0] == "install":
            quire_install.cmd_install(args_in[1:])
        else:
            quire_install.cmd_configs(args_in[1:])
        return
    # build.py pins a profile and does not consult the home config.
    cfg = None if default_profile is not None else (
        user_config if user_config is not None else load_user_config()
    )
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--profile", type=Path, default=None)
    pre.add_argument("--config", default=None)
    pre.add_argument("--no-config", action="store_true")
    known, _rest = pre.parse_known_args(args_in)
    profile_path = resolve_profile_arg(
        known.profile,
        default_profile,
        config_name=known.config,
        no_config=known.no_config,
        user_config=cfg,
    )
    profile = load_profile(profile_path) if profile_path is not None else None

    args = _parser(profile, description, prog, cfg).parse_args(args_in)
    # parse_args already exited on --help. Reload only if the chosen path changed.
    chosen = resolve_profile_arg(
        args.profile,
        default_profile,
        config_name=args.config,
        no_config=args.no_config,
        user_config=cfg,
    )
    if chosen != profile_path:
        profile = load_profile(chosen) if chosen is not None else None

    job = plan_job(
        args.target,
        profile=profile,
        letterhead=args.letterhead,
        confidential=args.confidential,
        output=args.output,
        resource_path=args.resource_path,
        css=args.css,
        no_css=args.no_css,
        include_before=args.include_before,
    )
    render(job)


if __name__ == "__main__":
    main()
