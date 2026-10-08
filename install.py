#!/usr/bin/env python3
"""
Install the quire command.

  python3 install.py

Writes ~/.local/bin/quire and creates .venv when WeasyPrint is missing.
Pass --profile to also register a named config (same as `quire install`).
Re-running updates the launcher path. It does not edit the shell rc.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
QUIRE_PY = APP_DIR / "quire.py"
REQUIREMENTS = APP_DIR / "requirements.txt"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def launcher_text(python: Path, script: Path, config_name: str | None) -> str:
    py = sh_quote(str(python))
    src = sh_quote(str(script))
    prog = "quire" if config_name is None else f"{config_name}-pdf"
    if config_name:
        name = sh_quote(config_name)
        body = f"exec {py} {src} --config {name} \"$@\""
    else:
        body = f"exec {py} {src} \"$@\""
    return f"#!/bin/sh\nQUIRE_PROG={sh_quote(prog)}\nexport QUIRE_PROG\n{body}\n"


def config_template(name: str, profile: Path) -> str:
    path = toml_escape(str(profile))
    return (
        "# Profile name from the table below, or empty for plain.css.\n"
        'default = ""\n'
        "\n"
        "[profiles]\n"
        f'{name} = "{path}"\n'
    )


PROFILES_HEADER = re.compile(r"\s*\[\s*(?:profiles|\"profiles\"|'profiles')\s*\]\s*(?:#.*)?")


def parse_config(text: str) -> tuple[dict, dict]:
    """Parse config text for an edit; return the document and its profiles."""
    try:
        original = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"error: invalid config; file left unchanged: {exc}") from exc
    profiles = original.get("profiles", {})
    if not isinstance(profiles, dict):
        raise SystemExit("error: config [profiles] must be a table; file left unchanged")
    return original, profiles


def verified(rendered: str, expected: dict) -> str:
    """Return the edited text only if it parses to exactly the expected document."""
    try:
        updated = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(
            "error: unsupported config formatting; file left unchanged"
        ) from exc
    if updated != expected:
        raise SystemExit("error: unsafe config update; file left unchanged")
    return rendered


def key_assignment(name: str) -> re.Pattern[str]:
    key = re.escape(name)
    return re.compile(rf"\s*(?:{key}|\"{key}\"|'{key}')\s*=")


def profiles_section(lines: list[str]) -> tuple[int, int] | None:
    """Line range of the [profiles] table body, or None when it is absent."""
    for start, line in enumerate(lines):
        if PROFILES_HEADER.fullmatch(line):
            break
    else:
        return None
    for end in range(start + 1, len(lines)):
        if lines[end].lstrip().startswith("["):
            return start + 1, end
    return start + 1, len(lines)


def upsert_profile(text: str, name: str, profile: Path) -> str:
    """Set profiles.<name> and leave every other line alone."""
    original, profiles = parse_config(text)
    expected = dict(original)
    expected["profiles"] = {**profiles, name: str(profile)}
    if not text.strip():
        return config_template(name, profile)
    assignment = f'{name} = "{toml_escape(str(profile))}"'
    lines = text.splitlines()
    section = profiles_section(lines)
    if section is None:
        suffix = text
        if suffix and not suffix.endswith("\n"):
            suffix += "\n"
        rendered = suffix + "\n[profiles]\n" + assignment + "\n"
    else:
        start, end = section
        assignment_re = key_assignment(name)
        for index in range(start, end):
            if assignment_re.match(lines[index]):
                lines[index] = assignment
                break
        else:
            lines.insert(end, assignment)
        rendered = "\n".join(lines)
        if text.endswith("\n"):
            rendered += "\n"
    return verified(rendered, expected)


def set_default(text: str, name: str) -> str:
    """Set the top-level default and leave every other line alone. "" clears it."""
    original, profiles = parse_config(text)
    if name and name not in profiles:
        known = ", ".join(sorted(profiles)) or "(none)"
        raise SystemExit(f"error: unknown config {name!r} (known: {known})")
    expected = {**original, "default": name}
    assignment = f'default = "{toml_escape(name)}"'
    lines = text.splitlines()
    first_table = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines)
    )
    assignment_re = key_assignment("default")
    for index in range(first_table):
        if assignment_re.match(lines[index]):
            lines[index] = assignment
            break
    else:
        lines.insert(0, assignment)
    rendered = "\n".join(lines)
    if text.endswith("\n") or not text:
        rendered += "\n"
    return verified(rendered, expected)


def remove_profile(text: str, name: str) -> str:
    """Delete profiles.<name> and leave every other line alone."""
    original, profiles = parse_config(text)
    if name not in profiles:
        known = ", ".join(sorted(profiles)) or "(none)"
        raise SystemExit(f"error: unknown config {name!r} (known: {known})")
    if original.get("default") == name:
        raise SystemExit(
            f"error: {name} is the default; run `quire default --clear` or choose another first"
        )
    expected = dict(original)
    expected["profiles"] = {key: value for key, value in profiles.items() if key != name}
    lines = text.splitlines()
    section = profiles_section(lines)
    assignment_re = key_assignment(name)
    matches = [index for index in range(*section) if assignment_re.match(lines[index])] if section else []
    if not matches:
        raise SystemExit("error: unsupported config formatting; file left unchanged")
    del lines[matches[0]]
    rendered = "\n".join(lines)
    if text.endswith("\n"):
        rendered += "\n"
    return verified(rendered, expected)


def is_launcher(path: Path, name: str) -> bool:
    """True when the file is the shortcut quire would write for this name."""
    try:
        text = path.read_text(encoding="utf-8")
        words = shlex.split(text.splitlines()[-1])
    except (OSError, UnicodeError, ValueError, IndexError):
        return False
    if len(words) != 6 or words[0] != "exec" or Path(words[2]).name != "quire.py":
        return False
    return text == launcher_text(Path(words[1]), Path(words[2]), name)


def default_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "quire"


def default_bin_dir() -> Path:
    found = shutil.which("quire")
    if found:
        return Path(found).resolve().parent
    return Path.home() / ".local" / "bin"


def check_name(name: str) -> None:
    if name == "quire" or not NAME_RE.fullmatch(name or ""):
        raise SystemExit(
            "error: config name must start with a letter, then use only letters, "
            f"digits, or hyphens (got {name!r})"
        )


def resolve_profile_dir(profile: Path) -> Path:
    profile = profile.expanduser().resolve()
    if profile.name == "profile.toml" and profile.is_file():
        profile = profile.parent
    if not (profile / "profile.toml").is_file():
        raise SystemExit(f"error: profile.toml not found in {profile}")
    return profile


def ensure_venv(python: Path | None) -> Path:
    """Return the venv interpreter, creating it and installing WeasyPrint if needed."""
    if python is not None:
        return python
    venv_python = APP_DIR / ".venv" / "bin" / "python"
    if not venv_python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(APP_DIR / ".venv")], check=True)
    check = subprocess.run(
        [str(venv_python), "-c", "import weasyprint"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            check=True,
        )
    return venv_python


def ensure_mermaid() -> None:
    """Install the optional locked Node runtime dependencies inside the checkout."""
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise SystemExit("error: --with-mermaid requires Node >=22.13 and npm on PATH")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", version.stdout)
    if not match or tuple(map(int, match.groups())) < (22, 13, 0):
        raise SystemExit("error: --with-mermaid requires Node >=22.13")
    env = os.environ.copy()
    env["PUPPETEER_CACHE_DIR"] = str(APP_DIR / ".cache" / "puppeteer")
    env["PUPPETEER_SKIP_CHROME_HEADLESS_SHELL_DOWNLOAD"] = "true"
    env["npm_config_cache"] = str(APP_DIR / ".cache" / "npm")
    subprocess.run([npm, "ci", "--prefix", str(APP_DIR), "--no-audit", "--no-fund"],
                   check=True, cwd=APP_DIR, env=env)
    # Recent npm versions gate dependency scripts. Run this reviewed installer
    # explicitly; older npm versions simply reuse the browser already downloaded.
    subprocess.run([node, str(APP_DIR / "node_modules/puppeteer/install.mjs")],
                   check=True, cwd=APP_DIR, env=env)


def write_text(path: Path, text: str, executable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def register_config(
    *,
    name: str,
    profile: Path,
    bin_dir: Path,
    config_dir: Path,
    python: Path,
    script: Path,
    command: bool = True,
) -> None:
    """Point a config name at a profile directory. Does not install quire itself."""
    check_name(name)
    profile = resolve_profile_dir(profile)
    script = script.expanduser().resolve()
    if not script.is_file():
        raise SystemExit(f"error: quire.py missing: {script}")
    python = python.expanduser().resolve()
    bin_dir = bin_dir.expanduser().resolve()
    config_dir = config_dir.expanduser().resolve()
    config_path = config_dir / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    write_text(config_path, upsert_profile(existing, name, profile), False)
    print(f"config {config_path}")
    print(f"  {name} = {profile}")
    print(f"use: quire --config {name} file.md")
    if command:
        shortcut = bin_dir / f"{name}-pdf"
        write_text(shortcut, launcher_text(python, script, name), True)
        print(f"installed {shortcut}")
        if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
            print(
                f"\n{bin_dir} is not on PATH. Add it in the shell that starts quire:\n"
                f'  export PATH="{bin_dir}:$PATH"',
                file=sys.stderr,
            )


def install(
    *,
    bin_dir: Path,
    config_dir: Path,
    name: str | None = None,
    profile: Path | None = None,
    python: Path | None = None,
    script: Path | None = None,
    ensure_runtime: bool = True,
    with_mermaid: bool = False,
) -> None:
    if with_mermaid:
        ensure_mermaid()
    if ensure_runtime:
        interpreter = ensure_venv(None)
    else:
        if python is None:
            raise SystemExit("error: python is required when runtime setup is skipped")
        interpreter = python
    quire_py = (script or QUIRE_PY).resolve()
    if not quire_py.is_file():
        raise SystemExit(f"error: quire.py missing: {quire_py}")

    bin_dir = bin_dir.expanduser().resolve()
    write_text(bin_dir / "quire", launcher_text(interpreter, quire_py, None), True)
    print(f"installed {bin_dir / 'quire'}")
    if profile is not None:
        resolved = resolve_profile_dir(profile)
        register_config(
            name=name or resolved.name,
            profile=resolved,
            bin_dir=bin_dir,
            config_dir=config_dir,
            python=interpreter,
            script=quire_py,
            command=True,
        )
    if shutil.which("pandoc") is None:
        print(
            "pandoc was not found. PDF builds need it:\n"
            "  brew install pandoc pango gdk-pixbuf libffi",
            file=sys.stderr,
        )


def cmd_install(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="quire install",
        description="Register a profile directory under a name.",
    )
    parser.add_argument("profile", type=Path, help="Directory with profile.toml, or that file")
    parser.add_argument("--name", default=None, help="Config name (default: the directory name)")
    parser.add_argument(
        "--no-command",
        action="store_true",
        help="Do not write the name-pdf shortcut",
    )
    parser.add_argument("--bin-dir", type=Path, default=None)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--python", type=Path, default=None)
    args = parser.parse_args(argv)
    profile = resolve_profile_dir(args.profile)
    register_config(
        name=args.name or profile.name,
        profile=profile,
        bin_dir=args.bin_dir or default_bin_dir(),
        config_dir=args.config_dir or default_config_dir(),
        python=args.python or Path(sys.executable),
        script=QUIRE_PY,
        command=not args.no_command,
    )


def cmd_configs(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="quire configs", description="List named PDF configs.")
    parser.add_argument("--config-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    config_dir = (args.config_dir or default_config_dir()).expanduser()
    import quire

    cfg = quire.load_user_config(config_dir / "config.toml")
    if not cfg.profiles:
        print("no configs")
        return
    width = max(len(name) for name in cfg.profiles)
    for name in sorted(cfg.profiles):
        note = "  (default)" if cfg.default and name == cfg.default else ""
        print(f"{name:<{width}}  {cfg.profiles[name]}{note}")


def cmd_default(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="quire default",
        description="Choose the profile used when no other selection applies.",
    )
    parser.add_argument("name", nargs="?", default=None, help="Registered config name")
    parser.add_argument("--clear", action="store_true", help="Use plain output by default")
    parser.add_argument("--config-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if (args.name is None) == (not args.clear):
        parser.error("give a config name or --clear")
    config_path = (args.config_dir or default_config_dir()).expanduser() / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    write_text(config_path, set_default(existing, args.name or ""), False)
    print(f"default = {args.name}" if args.name else "default cleared")


def cmd_remove(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="quire remove",
        description="Unregister a profile name. The profile directory is not touched.",
    )
    parser.add_argument("name", help="Registered config name")
    parser.add_argument("--bin-dir", type=Path, default=None)
    parser.add_argument("--config-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    config_path = (args.config_dir or default_config_dir()).expanduser() / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    write_text(config_path, remove_profile(existing, args.name), False)
    print(f"removed {args.name} from {config_path}")
    shortcut = (args.bin_dir or default_bin_dir()).expanduser() / f"{args.name}-pdf"
    if is_launcher(shortcut, args.name):
        shortcut.unlink()
        print(f"removed {shortcut}")
    elif shortcut.exists():
        print(f"{shortcut} was not written by quire; left in place", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Install quire and register a PDF config.")
    parser.add_argument(
        "--name",
        default=None,
        help="Config name to register with --profile (default: the profile directory name).",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="Optional profile directory to register. Omit to install the quire command only.",
    )
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local" / "bin")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument(
        "--skip-venv",
        action="store_true",
        help="Do not create the venv. Requires --python.",
    )
    parser.add_argument("--python", type=Path, default=None,
                        help="Interpreter for the launchers. Requires --skip-venv.")
    parser.add_argument("--with-mermaid", action="store_true",
                        help="Install local Mermaid tooling and Chromium; requires Node >=22.13 and npm")
    args = parser.parse_args(argv)
    if args.name and args.profile is None:
        raise SystemExit("error: --name requires --profile")
    if args.python and not args.skip_venv:
        raise SystemExit("error: --python requires --skip-venv")
    install(
        bin_dir=args.bin_dir,
        config_dir=args.config_dir or default_config_dir(),
        name=args.name,
        profile=args.profile,
        python=args.python,
        ensure_runtime=not args.skip_venv,
        with_mermaid=args.with_mermaid,
    )


if __name__ == "__main__":
    main()
