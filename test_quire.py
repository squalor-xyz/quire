#!/usr/bin/env python3
"""Stdlib tests for quire profile selection, config lookup, and install text."""

from __future__ import annotations

import io
import os
import re
import stat
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import install
import quire
from test_render import RenderTests, HTMLResourceTests, MermaidTests

APP_DIR = Path(__file__).resolve().parent


def temporary_directory():
    return tempfile.TemporaryDirectory(dir=APP_DIR)


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.profile_dir = self.root / "profile"
        self.profile_dir.mkdir()
        fragments = self.profile_dir / "fragments"
        fragments.mkdir()
        (fragments / "letterhead.html").write_text(
            '<div class="letterhead-block"><div class="letterhead-id">'
            '<span class="letterhead-url">example.invalid</span></div></div>',
            encoding="utf-8",
        )
        (fragments / "running-mark.html").write_text(
            '<img class="running-mark" src="mark.svg" alt="" />', encoding="utf-8"
        )
        (fragments / "notice.html").write_text(
            '<div class="confidential-banner">Confidential</div>', encoding="utf-8"
        )
        (self.profile_dir / "sample.md").write_text("Hello\n", encoding="utf-8")
        tables = ['resource_root = ".."']
        for name in quire.VARIANT_NAMES.values():
            css_name = f"{name}.css"
            (self.profile_dir / css_name).write_text("body { color: #222; }", encoding="utf-8")
            tables.extend([f"[variants.{name}]", f'css = ["{css_name}"]'])
            if "confidential" in name:
                tables.extend([
                    'output_suffix = "-confidential"',
                    'metadata = { confidential = "true" }',
                    'ensure_in_front_matter = ["confidential"]',
                ])
            if "letterhead" in name:
                tables.extend([
                    f"[[variants.{name}.includes]]",
                    'file = "fragments/letterhead.html"',
                    'skip_if_body_contains = ["letterhead-block"]',
                    f"[[variants.{name}.includes]]",
                    'file = "fragments/running-mark.html"',
                    'only_if_body_contains = ["letterhead-block"]',
                ])
            if "confidential" in name:
                tables.extend([
                    f"[[variants.{name}.includes]]",
                    'file = "fragments/notice.html"',
                    'after_marker = "letterhead-block"',
                    'after_anchor = "letterhead-id"',
                    'close_count = 2',
                ])
        for name, variant in (("sample", "default"), ("letter-sample", "letterhead"),
                              ("letterhead-blank", "letterhead")):
            tables.extend([
                f"[builtins.{name}]",
                'source = "sample.md"',
                f'output = "../output/{name}.pdf"',
                f'variant = "{variant}"',
            ])
        (self.profile_dir / "profile.toml").write_text("\n".join(tables), encoding="utf-8")
        self.profile = quire.load_profile(self.profile_dir)

    def test_variants_and_builtins_resolve(self) -> None:
        self.assertEqual(
            set(self.profile.variants),
            {"default", "letterhead", "confidential", "letterhead-confidential"},
        )
        self.assertEqual(self.profile.resource_root, self.root)
        sample = self.profile.builtins["sample"]
        self.assertEqual(
            sample.output,
            self.root / "output" / "sample.pdf",
        )
        self.assertTrue(sample.source.is_file())
        letter = self.profile.builtins["letter-sample"]
        self.assertEqual(letter.variant, "letterhead")
        self.assertEqual(
            letter.output,
            self.root / "output" / "letter-sample.pdf",
        )
        for variant in self.profile.variants.values():
            for css in variant.css:
                self.assertTrue(css.is_file(), css)

    def test_profile_precedence(self) -> None:
        cfg = quire.UserConfig(
            default="acme",
            profiles={
                "acme": Path("/tmp/acme"),
                "other": Path("/tmp/other"),
            },
        )
        # An embedded caller can pin a profile independently of user settings.
        self.assertEqual(
            quire.resolve_profile_arg(
                None,
                self.profile_dir,
                user_config=cfg,
                environ={"QUIRE_PROFILE": "/tmp/env", "QUIRE_CONFIG": "other"},
            ),
            self.profile_dir,
        )
        self.assertEqual(
            quire.resolve_profile_arg(
                Path("/tmp/flag"),
                None,
                config_name="acme",
                user_config=cfg,
                environ={},
            ),
            Path("/tmp/flag"),
        )
        self.assertEqual(
            quire.resolve_profile_arg(None, None, config_name="acme", user_config=cfg, environ={}),
            Path("/tmp/acme"),
        )
        self.assertIsNone(
            quire.resolve_profile_arg(
                None,
                None,
                no_config=True,
                config_name="acme",
                user_config=cfg,
                environ={},
            )
        )
        self.assertEqual(
            quire.resolve_profile_arg(
                None,
                None,
                user_config=cfg,
                environ={"QUIRE_CONFIG": "other"},
            ),
            Path("/tmp/other"),
        )
        self.assertEqual(
            quire.resolve_profile_arg(
                None,
                None,
                user_config=cfg,
                environ={"QUIRE_PROFILE": "/tmp/env"},
            ),
            Path("/tmp/env"),
        )
        self.assertEqual(
            quire.resolve_profile_arg(None, None, user_config=cfg, environ={}),
            Path("/tmp/acme"),
        )
        with self.assertRaises(SystemExit):
            quire.resolve_profile_arg(
                None, None, config_name="missing", user_config=cfg, environ={}
            )
        self.assertIsNone(quire.resolve_profile_arg(None, None, environ={}))
        self.assertEqual(
            quire.resolve_profile_arg(
                self.profile_dir, None, no_config=True, user_config=cfg, environ={}
            ),
            self.profile_dir,
        )

    def test_variant_matrix(self) -> None:
        self.assertEqual(quire.variant_name(False, False), "default")
        self.assertEqual(quire.variant_name(True, False), "letterhead")
        self.assertEqual(quire.variant_name(False, True), "confidential")
        self.assertEqual(quire.variant_name(True, True), "letterhead-confidential")

    def test_letterhead_injects_fragment(self) -> None:
        job = quire.plan_job(
            "letter-sample",
            profile=self.profile,
            cwd=self.root,
        )
        fragment = (self.profile_dir / "fragments" / "letterhead.html").read_text(encoding="utf-8").strip()
        self.assertIn(fragment, job.markdown)
        self.assertEqual(job.css, [self.profile_dir / "letterhead.css"])
        self.assertNotIn("confidential-banner", job.markdown)
        self.assertEqual(job.output.name, "letter-sample.pdf")

    def test_existing_band_skips_fragment_and_adds_running_mark(self) -> None:
        body = """<div class="letterhead-block">
<div class="letterhead-id">
<div class="letterhead-name">Example</div>
<div class="letterhead-url">example.invalid</div>
</div>
</div>

Hello
"""
        with temporary_directory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_text(body, encoding="utf-8")
            job = quire.plan_job(
                str(path),
                profile=self.profile,
                letterhead=True,
                cwd=Path(tmp),
            )
        mark = (self.profile_dir / "fragments" / "running-mark.html").read_text(encoding="utf-8").strip()
        self.assertIn(mark, job.markdown)
        self.assertEqual(job.markdown.count("letterhead-block"), 1)
        self.assertTrue(job.markdown.strip().startswith("<img"))

    def test_banner_lands_after_existing_band(self) -> None:
        body = """<div class="letterhead-block">
<img class="letterhead-seal" src="seal.png" alt="" />
<div class="letterhead-id">
<div class="letterhead-name">Example</div>
<div class="letterhead-url">example.invalid</div>
</div>
</div>

Hello
"""
        with temporary_directory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_text(body, encoding="utf-8")
            job = quire.plan_job(
                str(path),
                profile=self.profile,
                letterhead=True,
                confidential=True,
                cwd=Path(tmp),
            )
        banner = "confidential-banner"
        self.assertEqual(job.markdown.count(banner), 1)
        self.assertLess(job.markdown.find("letterhead-url"), job.markdown.find(banner))
        self.assertLess(job.markdown.find(banner), job.markdown.find("Hello"))
        self.assertIn("confidential: true", job.markdown.split("Hello", 1)[0])
        self.assertEqual(job.css[0].name, "letterhead-confidential.css")
        self.assertTrue(job.output.name.endswith("-confidential.pdf"))

    def test_builtin_letterhead_plus_confidential_flag(self) -> None:
        job = quire.plan_job(
            "letterhead-blank",
            profile=self.profile,
            confidential=True,
            cwd=self.root,
        )
        self.assertEqual(job.css[0].name, "letterhead-confidential.css")
        self.assertEqual(job.output.name, "letterhead-blank-confidential.pdf")
        self.assertIn("letterhead-block", job.markdown)
        self.assertIn("confidential-banner", job.markdown)

    def test_unbranded_does_not_use_profile(self) -> None:
        path = self.root / "plain.md"
        text = "# Notes\n\nJust text.\n"
        path.write_text(text, encoding="utf-8")
        job = quire.plan_job(str(path), profile=None, cwd=self.root)
        self.assertEqual(job.markdown, text)
        self.assertEqual(job.css, [quire.PLAIN_CSS])
        self.assertNotIn("default.css", str(job.css[0]))
        self.assertEqual(job.output, path.resolve().with_suffix(".pdf"))
        with self.assertRaises(SystemExit):
            quire.plan_job("sample", profile=None, cwd=self.root)
        for letterhead, confidential in ((True, False), (True, True)):
            with self.subTest(letterhead=letterhead, confidential=confidential):
                with self.assertRaises(SystemExit) as raised:
                    quire.plan_job(
                        str(path),
                        profile=None,
                        letterhead=letterhead,
                        confidential=confidential,
                        cwd=self.root,
                    )
                self.assertIn("letterhead", str(raised.exception))

    def test_plain_confidential(self) -> None:
        path = self.root / "plain.md"
        text = "# Notes\n\nJust text.\n"
        path.write_text(text, encoding="utf-8")
        job = quire.plan_job(str(path), profile=None, confidential=True, cwd=self.root)
        self.assertEqual(job.markdown.count("confidential-banner"), 1)
        self.assertIn(quire.PLAIN_CONFIDENTIAL_BANNER, job.markdown)
        self.assertLess(job.markdown.find("confidential-banner"), job.markdown.find("# Notes"))
        self.assertIn("confidential: true", job.markdown.split("# Notes", 1)[0])
        self.assertEqual(job.metadata["confidential"], "true")
        self.assertEqual(job.css, [quire.PLAIN_CONFIDENTIAL_CSS])
        self.assertTrue(quire.PLAIN_CONFIDENTIAL_CSS.is_file())
        self.assertEqual(job.output, path.resolve().with_name("plain-confidential.pdf"))

    def test_plain_confidential_skips_existing_banner(self) -> None:
        path = self.root / "marked.md"
        text = '<div class="confidential-banner">Already</div>\n\nHello\n'
        path.write_text(text, encoding="utf-8")
        job = quire.plan_job(str(path), profile=None, confidential=True, cwd=self.root)
        self.assertEqual(job.markdown.count("confidential-banner"), 1)
        self.assertNotIn(quire.PLAIN_CONFIDENTIAL_BANNER, job.markdown)

    def test_plain_confidential_overrides(self) -> None:
        path = self.root / "plain.md"
        path.write_text("# Notes\n", encoding="utf-8")
        css = self.root / "custom.css"
        css.write_text("body { font-size: 12pt; }", encoding="utf-8")
        intro = self.root / "intro.html"
        intro.write_text("<p>Intro</p>", encoding="utf-8")

        replaced = quire.plan_job(
            str(path),
            profile=None,
            confidential=True,
            css=[Path("custom.css")],
            include_before=[Path("intro.html")],
            cwd=self.root,
        )
        self.assertEqual(replaced.css, [css.resolve()])
        self.assertIn("confidential-banner", replaced.markdown)
        self.assertLess(
            replaced.markdown.find("confidential-banner"),
            replaced.markdown.find("Intro"),
        )

        bare = quire.plan_job(
            str(path), profile=None, confidential=True, no_css=True, cwd=self.root,
        )
        self.assertEqual(bare.css, [])
        self.assertIn("confidential-banner", bare.markdown)

        chosen = quire.plan_job(
            str(path),
            profile=None,
            confidential=True,
            output=Path("chosen/result.pdf"),
            cwd=self.root,
        )
        self.assertEqual(chosen.output, self.root / "chosen" / "result.pdf")

    def test_custom_css_replaces_selected_stylesheets(self) -> None:
        css = self.root / "custom.css"
        css.write_text("body { font-size: 12pt; }", encoding="utf-8")
        source = self.profile_dir / "sample.md"
        for profile in (None, self.profile):
            with self.subTest(profile=profile):
                job = quire.plan_job(
                    str(source), profile=profile, css=[Path("custom.css")], cwd=self.root
                )
                self.assertEqual(job.css, [css])

    def test_no_css_overrides_custom_and_profile_css(self) -> None:
        job = quire.plan_job(
            "letter-sample", profile=self.profile, no_css=True,
            css=[Path("missing.css")], cwd=self.root,
        )
        self.assertEqual(job.css, [])
        self.assertIn("letterhead-block", job.markdown)

    def test_add_css_appends_after_selected_stylesheets(self) -> None:
        first = self.root / "first.css"
        second = self.root / "second.css"
        custom = self.root / "custom.css"
        for path in (first, second, custom):
            path.write_text("body { color: black; }", encoding="utf-8")
        source = self.profile_dir / "sample.md"
        extra = [Path("first.css"), Path("second.css")]
        plain = quire.plan_job(
            str(source), profile=None, add_css=extra, cwd=self.root
        )
        self.assertEqual(plain.css, [quire.PLAIN_CSS, first, second])
        base = quire.plan_job("letter-sample", profile=self.profile, cwd=self.root)
        added = quire.plan_job(
            "letter-sample", profile=self.profile, add_css=extra, cwd=self.root
        )
        self.assertEqual(added.css, base.css + [first, second])
        reordered = quire.plan_job(
            "letter-sample", profile=self.profile,
            add_css=[Path("second.css"), Path("first.css")], cwd=self.root,
        )
        self.assertEqual(reordered.css, base.css + [second, first])
        replaced = quire.plan_job(
            str(source), profile=self.profile, css=[Path("custom.css")],
            add_css=extra, cwd=self.root,
        )
        self.assertEqual(replaced.css, [custom, first, second])

    def test_add_css_follows_confidential_stylesheet(self) -> None:
        extra = self.root / "extra.css"
        extra.write_text("body { color: black; }", encoding="utf-8")
        source = self.profile_dir / "sample.md"
        job = quire.plan_job(
            str(source), profile=None, confidential=True,
            add_css=[Path("extra.css")], cwd=self.root,
        )
        self.assertEqual(job.css, [quire.PLAIN_CONFIDENTIAL_CSS, extra])

    def test_main_passes_add_css_to_the_planned_job(self) -> None:
        extra = self.root / "extra.css"
        extra.write_text("body { color: black; }", encoding="utf-8")
        source = self.profile_dir / "sample.md"
        cfg = quire.UserConfig(default="", profiles={})
        argv = ["--no-config", str(source), "--add-css", str(extra)]
        with patch("quire.render") as render:
            quire.main(argv, user_config=cfg)
        (job,) = [call.args[0] for call in render.call_args_list]
        self.assertEqual(job.css, [quire.PLAIN_CSS, extra])
        with patch("quire.render") as render:
            with self.assertRaisesRegex(SystemExit, "CSS missing"):
                quire.main(
                    ["--no-config", str(source), "--add-css", str(self.root / "gone.css")],
                    user_config=cfg,
                )
        render.assert_not_called()

    def test_no_css_drops_added_stylesheets(self) -> None:
        job = quire.plan_job(
            "letter-sample", profile=self.profile, no_css=True,
            add_css=[Path("missing.css")], cwd=self.root,
        )
        self.assertEqual(job.css, [])

    def test_missing_added_css_is_an_error(self) -> None:
        (self.root / "ok.css").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "CSS missing"):
            quire.plan_job(
                "letter-sample", profile=self.profile,
                add_css=[Path("ok.css"), Path("missing.css")], cwd=self.root,
            )

    def test_add_css_option_is_repeatable(self) -> None:
        args = quire._parser(None, "", "quire").parse_args(
            ["a.md", "--add-css", "x.css", "--add-css", "y.css"]
        )
        self.assertEqual(args.add_css, [Path("x.css"), Path("y.css")])

    def test_explicit_output_overrides_builtin_and_variant_suffix(self) -> None:
        for target in ("letter-sample", str(self.profile_dir / "sample.md")):
            with self.subTest(target=target):
                job = quire.plan_job(
                    target, profile=self.profile, confidential=True,
                    output=Path("chosen/result.pdf"), cwd=self.root,
                )
                self.assertEqual(job.output, self.root / "chosen" / "result.pdf")
                self.assertEqual(job.metadata["confidential"], "true")

    def test_output_cannot_overwrite_source(self) -> None:
        (self.root / "notes.md").write_text("Hello\n", encoding="utf-8")
        for output in (Path("notes.md"), self.root / "notes.md", Path("./sub/../notes.md")):
            with self.subTest(output=output):
                with self.assertRaisesRegex(SystemExit, "overwrite the source"):
                    quire.plan_job("notes.md", profile=None, output=output, cwd=self.root)


class ConfigFileTests(unittest.TestCase):
    def setUp(self) -> None:
        tool_lookup = patch("install.shutil.which", return_value=None)
        tool_lookup.start()
        self.addCleanup(tool_lookup.stop)

    def test_load_user_config(self) -> None:
        with temporary_directory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                'default = "acme"\n\n[profiles]\nacme = "/tmp/acme"\n',
                encoding="utf-8",
            )
            cfg = quire.load_user_config(path)
            missing = quire.load_user_config(Path(tmp) / "absent.toml")
        self.assertEqual(cfg.default, "acme")
        self.assertEqual(cfg.profiles["acme"], Path("/tmp/acme"))
        self.assertEqual(missing.default, "")
        self.assertEqual(missing.profiles, {})

    def test_upsert_preserves_default_and_other_profiles(self) -> None:
        original = (
            "# keep\n"
            'default = "other"\n'
            "\n"
            "[profiles]\n"
            'other = "/tmp/other"\n'
            'acme = "/old/path"\n'
        )
        updated = install.upsert_profile(original, "acme", Path("/new/path"))
        self.assertIn('default = "other"', updated)
        self.assertIn('other = "/tmp/other"', updated)
        self.assertIn('acme = "/new/path"', updated)
        self.assertNotIn("/old/path", updated)
        self.assertIn("# keep", updated)

    def test_upsert_accepts_toml_spacing_quotes_and_comments(self) -> None:
        for header in ("[profiles]", "[profiles] # registered profiles"):
            for assignment in (
                'acme="/old"',
                '  acme\t= "/old" # old path',
                '"acme" = "/old"',
                "'acme' = '/old'",
            ):
                for newline in ("", "\n"):
                    with self.subTest(header=header, assignment=assignment, newline=newline):
                        original = (
                            '# keep this comment\ndefault = "other"\n'
                            f'{header}\nother = "/other"\n{assignment}\n'
                            '[extra] # unrelated table\nsetting = "keep"' + newline
                        )
                        updated = install.upsert_profile(original, "acme", Path("/new"))
                        expected = tomllib.loads(original)
                        expected["profiles"]["acme"] = "/new"
                        self.assertEqual(tomllib.loads(updated), expected)
                        self.assertIn(header, updated)
                        self.assertIn('# keep this comment', updated)
                        self.assertIn('[extra] # unrelated table', updated)
                        self.assertEqual(updated.endswith("\n"), bool(newline))

    def test_upsert_adds_profile_before_commented_next_table(self) -> None:
        for original in (
            '# keep\ndefault = "other"\n[profiles] # paths\nother = "/other"\n'
            '[extra] # settings\nsetting = "keep"\n',
            '# keep\ndefault = ""\n[extra]\nsetting = "keep"\n',
        ):
            with self.subTest(original=original):
                updated = install.upsert_profile(original, "acme", Path("/new"))
                expected = tomllib.loads(original)
                expected.setdefault("profiles", {})["acme"] = "/new"
                self.assertEqual(tomllib.loads(updated), expected)
                self.assertIn('# keep', updated)

    def test_registration_rejects_invalid_or_unsafe_updates_without_writing(self) -> None:
        for original in (
            '[profiles]\nacme = "unterminated',
            'profiles = "not a table"\n',
            'profiles.acme = "/old"\n',
            '[profiles]\nacme = """\n/old\n"""\n',
            '[extra]\nvalue = """\n[profiles]\n'
            'acme = "/inside-string"\n"""\n[profiles]\nacme = "/old"\n',
        ):
            with self.subTest(original=original), temporary_directory() as tmp:
                root = Path(tmp)
                profile = root / "profile"
                profile.mkdir()
                (profile / "profile.toml").write_text("# test\n", encoding="utf-8")
                config_dir = root / "config"
                config_dir.mkdir()
                config_path = config_dir / "config.toml"
                config_path.write_text(original, encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, "file left unchanged"):
                    install.register_config(
                        name="acme", profile=profile, bin_dir=root / "bin",
                        config_dir=config_dir, python=root / "python",
                        script=APP_DIR / "quire.py",
                    )
                self.assertEqual(config_path.read_text(encoding="utf-8"), original)
                self.assertFalse((root / "bin").exists())

    def test_install_python_requires_skip_venv(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            with patch("install.install") as mocked, \
                    self.assertRaisesRegex(SystemExit, "--python requires --skip-venv"):
                install.main(["--python", str(root / "python"), "--bin-dir", str(root / "bin"),
                              "--config-dir", str(root / "config")])
            mocked.assert_not_called()
            self.assertFalse((root / "bin").exists())

    def test_install_writes_launchers_without_touching_default(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                'default = "other"\n\n[profiles]\nother = "/tmp/other"\n',
                encoding="utf-8",
            )
            fake_python = root / "python"
            fake_python.write_text("#!/bin/sh\n", encoding="utf-8")
            profile = root / "profile"
            profile.mkdir()
            (profile / "profile.toml").write_text("# test\n", encoding="utf-8")
            install.install(
                bin_dir=root / "bin",
                config_dir=config_dir,
                name="acme",
                profile=profile,
                python=fake_python,
                script=APP_DIR / "quire.py",
                ensure_runtime=False,
            )
            quire_bin = (root / "bin" / "quire").read_text(encoding="utf-8")
            brand_bin = (root / "bin" / "acme-pdf").read_text(encoding="utf-8")
            mode = (root / "bin" / "quire").stat().st_mode
            saved = (config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn(str(fake_python), quire_bin)
        self.assertNotIn("--config", quire_bin)
        self.assertIn("QUIRE_PROG='quire'", quire_bin)
        self.assertIn("--config 'acme'", brand_bin)
        self.assertIn("QUIRE_PROG='acme-pdf'", brand_bin)
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertIn('default = "other"', saved)
        self.assertIn(str(profile.resolve()), saved)
        self.assertIn('other = "/tmp/other"', saved)


class NamedConfigTests(unittest.TestCase):
    def _profile(self, root: Path, dirname: str) -> Path:
        profile = root / dirname
        profile.mkdir()
        (profile / "profile.toml").write_text("# test\n", encoding="utf-8")
        return profile

    def test_install_uses_directory_name_and_lists_it(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            profile = self._profile(root, "acme")
            config_dir = root / "cfg"
            bin_dir = root / "bin"
            quire.main(
                [
                    "install",
                    str(profile),
                    "--bin-dir",
                    str(bin_dir),
                    "--config-dir",
                    str(config_dir),
                ]
            )
            saved = (config_dir / "config.toml").read_text(encoding="utf-8")
            shortcut = (bin_dir / "acme-pdf").read_text(encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                quire.main(["configs", "--config-dir", str(config_dir)])
        self.assertIn(f'acme = "{profile.resolve()}"', saved)
        self.assertIn('default = ""', saved)
        self.assertIn("--config 'acme'", shortcut)
        self.assertIn("acme", buf.getvalue())
        self.assertIn(str(profile.resolve()), buf.getvalue())

    def test_default_sets_and_clears_without_touching_other_lines(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            config_dir = root / "cfg"
            bin_dir = root / "bin"
            for name in ("acme", "other"):
                quire.main(["install", str(self._profile(root, name)), "--no-command",
                            "--bin-dir", str(bin_dir), "--config-dir", str(config_dir)])
            path = config_dir / "config.toml"
            path.write_text("# mine\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                quire.main(["default", "acme", "--config-dir", str(config_dir)])
            chosen = path.read_text(encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                quire.main(["default", "--clear", "--config-dir", str(config_dir)])
            cleared = path.read_text(encoding="utf-8")
        self.assertEqual(chosen, before.replace('default = ""', 'default = "acme"'))
        self.assertEqual(cleared, before)

    def test_default_inserts_missing_key_and_rejects_unknown_names(self) -> None:
        original = '# mine\n[profiles]\nacme = "/tmp/acme"\n'
        updated = install.set_default(original, "acme")
        self.assertEqual(tomllib.loads(updated)["default"], "acme")
        self.assertTrue(updated.endswith(original))
        with temporary_directory() as tmp:
            config_dir = Path(tmp)
            path = config_dir / "config.toml"
            path.write_text(original, encoding="utf-8")
            for argv in (["missing"], [], ["acme", "--clear"]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                        redirect_stderr(io.StringIO()):
                    quire.main(["default", *argv, "--config-dir", str(config_dir)])
            self.assertEqual(path.read_text(encoding="utf-8"), original)
        with self.assertRaisesRegex(SystemExit, "file left unchanged"):
            install.set_default('default = """\n[x]\n"""\n[profiles]\nacme = "/a"\n', "acme")

    def test_remove_drops_entry_and_its_launcher_only(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            config_dir = root / "cfg"
            bin_dir = root / "bin"
            common = ["--bin-dir", str(bin_dir), "--config-dir", str(config_dir)]
            for name in ("acme", "other"):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    quire.main(["install", str(self._profile(root, name)), *common])
            path = config_dir / "config.toml"
            before = path.read_text(encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                quire.main(["remove", "acme", *common])
            after = path.read_text(encoding="utf-8")
            self.assertEqual(
                after, "".join(line for line in before.splitlines(keepends=True)
                               if not line.startswith("acme ="))
            )
            self.assertFalse((bin_dir / "acme-pdf").exists())
            self.assertTrue((bin_dir / "other-pdf").exists())
            self.assertTrue((root / "acme" / "profile.toml").is_file())

            (bin_dir / "other-pdf").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
            errors = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                quire.main(["remove", "other", *common])
            self.assertIn("left in place", errors.getvalue())
            self.assertTrue((bin_dir / "other-pdf").exists())
            self.assertNotIn("other", tomllib.loads(path.read_text(encoding="utf-8"))["profiles"])

    def test_remove_refuses_default_and_unknown_names(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            config_dir = root / "cfg"
            bin_dir = root / "bin"
            common = ["--bin-dir", str(bin_dir), "--config-dir", str(config_dir)]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                quire.main(["install", str(self._profile(root, "acme")), *common])
                quire.main(["default", "acme", "--config-dir", str(config_dir)])
            path = config_dir / "config.toml"
            before = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "default --clear"):
                quire.main(["remove", "acme", *common])
            with self.assertRaisesRegex(SystemExit, "unknown config"):
                quire.main(["remove", "missing", *common])
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertTrue((bin_dir / "acme-pdf").exists())

    def test_missing_registered_profile_names_the_config(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            config_dir = root / "cfg"
            common = ["--bin-dir", str(root / "bin"), "--config-dir", str(config_dir)]
            profile = self._profile(root, "acme")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                quire.main(["install", str(profile), *common])
            (profile / "profile.toml").unlink()
            cfg = quire.load_user_config(config_dir / "config.toml")
            with self.assertRaisesRegex(SystemExit, r"config 'acme' points to .*quire install"):
                quire.main(["--config", "acme", "x.md"], user_config=cfg)
            with self.assertRaisesRegex(SystemExit, "profile not found"):
                quire.main(["--profile", str(profile), "x.md"], user_config=cfg)

    def test_unknown_config_names_config_path(self) -> None:
        with temporary_directory() as tmp:
            path = Path(tmp) / "cfg" / "config.toml"
            cfg = quire.load_user_config(path)
            with self.assertRaises(SystemExit) as caught:
                quire.main(["--config", "acme", "x.md"], user_config=cfg)
            self.assertIn(f"{path} (missing)", str(caught.exception))

    def test_unknown_config_suggests_install(self) -> None:
        with temporary_directory() as tmp:
            cfg = quire.load_user_config(Path(tmp) / "config.toml")
            with self.assertRaises(SystemExit) as caught:
                quire.main(["--config", "acme", "x.md"], user_config=cfg)
            self.assertIn("quire install <profile-dir> --name acme", str(caught.exception))

    def test_unknown_config_lists_known_names(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            config_dir = root / "cfg"
            common = ["--bin-dir", str(root / "bin"), "--config-dir", str(config_dir)]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                quire.main(["install", str(self._profile(root, "other")), *common])
            path = config_dir / "config.toml"
            cfg = quire.load_user_config(path)
            with self.assertRaises(SystemExit) as caught:
                quire.main(["--config", "acme", "x.md"], user_config=cfg)
            message = str(caught.exception)
            self.assertIn("(known: other)", message)
            self.assertIn(str(path), message)
            self.assertNotIn("(missing)", message)

    def test_unknown_config_from_environment_names_the_source(self) -> None:
        with temporary_directory() as tmp:
            cfg = quire.load_user_config(Path(tmp) / "config.toml")
            with self.assertRaises(SystemExit) as caught:
                quire.resolve_profile_arg(
                    None, None, user_config=cfg, environ={"QUIRE_CONFIG": "acme"}
                )
            message = str(caught.exception)
            self.assertIn("unknown config 'acme' from QUIRE_CONFIG (known:", message)
            self.assertIn("config.toml (missing)", message)
            self.assertIn("quire install <profile-dir> --name acme", message)

    def test_stale_config_default_suggests_default_command(self) -> None:
        with temporary_directory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('default = "gone"\n[profiles]\n', encoding="utf-8")
            cfg = quire.load_user_config(path)
            with self.assertRaises(SystemExit) as caught:
                quire.resolve_profile_arg(None, None, user_config=cfg, environ={})
            message = str(caught.exception)
            self.assertIn("unknown config 'gone' from the config default (known:", message)
            self.assertIn(f"config file: {path}\n", message)
            self.assertIn("quire default <name>", message)
            self.assertIn("quire default --clear", message)
            self.assertNotIn("quire install", message)

    def test_unknown_config_quotes_name_in_install_hint(self) -> None:
        with temporary_directory() as tmp:
            cfg = quire.load_user_config(Path(tmp) / "config.toml")
            with self.assertRaises(SystemExit) as caught:
                quire.main(["--config", "my co", "x.md"], user_config=cfg)
            self.assertIn("--name 'my co'", str(caught.exception))

    def test_unknown_config_reads_xdg_config_home(self) -> None:
        with temporary_directory() as tmp:
            env = {"XDG_CONFIG_HOME": tmp, "QUIRE_CONFIG": "", "QUIRE_PROFILE": ""}
            with patch.dict(os.environ, env), self.assertRaises(SystemExit) as caught:
                quire.main(["--config", "acme", "x.md"])
            self.assertIn(f"{tmp}/quire/config.toml (missing)", str(caught.exception))

    def _usable(self, root: Path, dirname: str) -> Path:
        """A profile that loads: one default variant with a stylesheet."""
        profile = self._profile(root, dirname)
        (profile / "style.css").write_text("body { color: black; }", encoding="utf-8")
        (profile / "profile.toml").write_text(
            '[variants.default]\ncss = ["style.css"]\n', encoding="utf-8"
        )
        return profile

    def _prefer(self, tmp: str, *, registered: bool = True, default: str = ""):
        """A config for --prefer-config tests, a source file, and an env that selects nothing."""
        root = Path(tmp)
        profiles = {"acme": self._usable(root, "acme")} if registered else {}
        if default:
            profiles[default] = self._usable(root, default)
        cfg = quire.UserConfig(
            default=default, profiles=profiles, path=root / "cfg" / "config.toml"
        )
        source = root / "x.md"
        source.write_text("hello\n", encoding="utf-8")
        return cfg, source, {"QUIRE_CONFIG": "", "QUIRE_PROFILE": ""}

    def _run_main(self, argv: list[str], cfg: quire.UserConfig, env: dict[str, str]):
        """Run main without rendering; return the planned jobs and stderr text."""
        errors = io.StringIO()
        with patch.dict(os.environ, env), patch("quire.render") as render, \
                redirect_stderr(errors):
            quire.main(argv, user_config=cfg)
        return [call.args[0] for call in render.call_args_list], errors.getvalue()

    def test_prefer_config_selects_a_registered_name(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp)
            self.assertEqual(
                quire.resolve_profile_arg(
                    None, None, prefer_name="acme", user_config=cfg, environ={}
                ),
                cfg.profiles["acme"],
            )
            jobs, errors = self._run_main(["--prefer-config", "acme", str(source)], cfg, env)
        self.assertEqual(errors, "")
        self.assertEqual(jobs[0].css, [cfg.profiles["acme"] / "style.css"])

    def test_prefer_config_unregistered_warns_once_and_goes_plain(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            second = Path(tmp) / "y.md"
            second.write_text("again\n", encoding="utf-8")
            jobs, errors = self._run_main(
                ["--prefer-config", "acme", str(source), str(second)], cfg, env
            )
        self.assertEqual([job.css for job in jobs], [[quire.PLAIN_CSS]] * 2)
        self.assertEqual(errors.count("warning:"), 1)
        self.assertIn("'acme'", errors)
        self.assertIn(f"config file: {cfg.path} (missing)", errors)
        self.assertIn("quire install <profile-dir> --name acme", errors)

    def test_prefer_config_unregistered_falls_down_the_selection_order(self) -> None:
        with temporary_directory() as tmp:
            cfg, _source, _env = self._prefer(tmp, registered=False, default="mine")
            other = cfg.profiles["mine"]
            pick = {"QUIRE_CONFIG": "mine", "QUIRE_PROFILE": "/elsewhere"}
            with redirect_stderr(io.StringIO()):
                self.assertEqual(
                    quire.resolve_profile_arg(
                        None, None, prefer_name="acme", user_config=cfg, environ=pick
                    ),
                    other,
                )
                self.assertEqual(
                    quire.resolve_profile_arg(
                        None, None, prefer_name="acme", user_config=cfg,
                        environ={"QUIRE_PROFILE": "/elsewhere"},
                    ),
                    Path("/elsewhere"),
                )
                self.assertEqual(
                    quire.resolve_profile_arg(
                        None, None, prefer_name="acme", user_config=cfg, environ={}
                    ),
                    other,
                )
                cfg.default = ""
                self.assertIsNone(
                    quire.resolve_profile_arg(
                        None, None, prefer_name="acme", user_config=cfg, environ={}
                    )
                )

    def test_prefer_config_follows_profile_and_no_config_precedence(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            profile = self._usable(Path(tmp), "direct")
            jobs, errors = self._run_main(
                ["--prefer-config", "acme", "--profile", str(profile), str(source)], cfg, env
            )
            self.assertEqual(errors, "")
            self.assertEqual(jobs[0].css, [profile / "style.css"])
            cfg.profiles["acme"] = self._usable(Path(tmp), "registered")
            for argv in (["--no-config"], ["--no-config", "--prefer-config", "gone"]):
                jobs, errors = self._run_main(
                    ["--prefer-config", "acme", *argv, str(source)], cfg, env
                )
                self.assertEqual(errors, "")
                self.assertEqual(jobs[0].css, [quire.PLAIN_CSS])

    def test_prefer_config_with_config_is_a_usage_error(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            errors = io.StringIO()
            with patch.dict(os.environ, env), patch("quire.render") as render, \
                    redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
                quire.main(
                    ["--prefer-config", "acme", "--config", "acme", str(source)],
                    user_config=cfg,
                )
        self.assertEqual(caught.exception.code, 2)
        self.assertRegex(errors.getvalue(), "not allowed with argument --(prefer-)?config")
        self.assertNotIn("unknown config", errors.getvalue())
        render.assert_not_called()

    def test_prefer_config_help_shows_exclusive_flags(self) -> None:
        with temporary_directory() as tmp:
            cfg, _source, env = self._prefer(tmp)
            out = io.StringIO()
            with patch.dict(os.environ, env), redirect_stdout(out), \
                    self.assertRaises(SystemExit):
                quire.main(["--help"], user_config=cfg)
        self.assertRegex(out.getvalue(), r"\[--config CONFIG \| --prefer-config NAME\]")

    def test_prefer_config_warning_waits_for_argparse(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            for argv in (["--help"], ["--bogus", str(source)]):
                errors = io.StringIO()
                with patch.dict(os.environ, env), redirect_stdout(io.StringIO()), \
                        redirect_stderr(errors), self.assertRaises(SystemExit):
                    quire.main(["--prefer-config", "acme", *argv], user_config=cfg)
                self.assertNotIn("warning:", errors.getvalue())

    def test_prefer_config_warning_precedes_a_fallback_error(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            env["QUIRE_CONFIG"] = "gone"
            errors = io.StringIO()
            with patch.dict(os.environ, env), redirect_stderr(errors), \
                    self.assertRaisesRegex(SystemExit, "unknown config 'gone' from QUIRE_CONFIG"):
                quire.main(["--prefer-config", "acme", str(source)], user_config=cfg)
        self.assertEqual(errors.getvalue().count("warning: preferred config 'acme'"), 1)

    def test_prefer_config_warning_precedes_a_broken_fallback_profile(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False, default="mine")
            (cfg.profiles["mine"] / "profile.toml").unlink()
            errors = io.StringIO()
            with patch.dict(os.environ, env), redirect_stderr(errors), \
                    self.assertRaisesRegex(SystemExit, "config 'mine' points to"):
                quire.main(["--prefer-config", "acme", str(source)], user_config=cfg)
        self.assertEqual(errors.getvalue().count("warning: preferred config 'acme'"), 1)

    def test_prefer_config_is_ignored_when_a_profile_is_pinned(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            pinned = self._usable(Path(tmp), "pinned")
            errors = io.StringIO()
            with patch.dict(os.environ, env), patch("quire.render") as render, \
                    redirect_stderr(errors):
                quire.main(
                    ["--prefer-config", "nope", str(source)],
                    default_profile=pinned, user_config=cfg,
                )
        (job,) = [call.args[0] for call in render.call_args_list]
        self.assertEqual(job.css, [pinned / "style.css"])
        self.assertEqual(errors.getvalue(), "")

    def test_prefer_config_empty_name_is_an_error(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp)
            with patch.dict(os.environ, env), \
                    self.assertRaisesRegex(SystemExit, "error: config name is empty"):
                quire.main(["--prefer-config", "", str(source)], user_config=cfg)

    def test_config_empty_name_is_an_error_and_does_not_fall_through(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, default="mine")
            selecting = {**env, "QUIRE_CONFIG": "acme", "QUIRE_PROFILE": "/elsewhere"}
            for pick in (env, selecting):
                with self.subTest(env=pick), patch.dict(os.environ, pick), \
                        patch("quire.render") as render:
                    with self.assertRaisesRegex(
                        SystemExit, "^error: config name is empty$"
                    ):
                        quire.main(["--config", "", str(source)], user_config=cfg)
                    render.assert_not_called()

    def test_blank_quire_config_env_stays_unset(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, default="mine")
            for blank in ("", "   "):
                jobs, _ = self._run_main([str(source)], cfg, {**env, "QUIRE_CONFIG": blank})
                self.assertEqual(jobs[0].css, [cfg.profiles["mine"] / "style.css"])

    def _exit_message(
        self, argv: list[str], cfg: quire.UserConfig, env: dict[str, str]
    ) -> tuple[str, str]:
        """Run main, which must exit; return the exit message and stderr, no render."""
        errors = io.StringIO()
        with patch.dict(os.environ, env), patch("quire.render") as render:
            with redirect_stderr(errors):
                with self.assertRaises(SystemExit) as caught:
                    quire.main(argv, user_config=cfg)
        render.assert_not_called()
        return str(caught.exception), errors.getvalue()

    def test_config_blank_name_is_an_error_like_an_empty_one(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, default="mine")
            selecting = {**env, "QUIRE_CONFIG": "acme", "QUIRE_PROFILE": "/elsewhere"}
            argv = ["--config", "   ", str(source)]
            for pick in (env, selecting):
                with self.subTest(env=pick):
                    message, _ = self._exit_message(argv, cfg, pick)
                    self.assertEqual(message, "error: config name is empty")

    def test_prefer_config_blank_name_is_an_error_like_an_empty_one(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, default="mine")
            selecting = {**env, "QUIRE_CONFIG": "acme", "QUIRE_PROFILE": "/elsewhere"}
            argv = ["--prefer-config", "   ", str(source)]
            for pick in (env, selecting):
                with self.subTest(env=pick):
                    message, errors = self._exit_message(argv, cfg, pick)
                    self.assertEqual(message, "error: config name is empty")
                    self.assertEqual(errors, "")

    def test_config_name_is_trimmed_before_lookup(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp)
            jobs, errors = self._run_main(["--config", " acme ", str(source)], cfg, env)
        self.assertEqual(errors, "")
        self.assertEqual(jobs[0].css, [cfg.profiles["acme"] / "style.css"])

    def test_prefer_config_name_is_trimmed_before_lookup(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp)
            argv = ["--prefer-config", " acme ", str(source)]
            jobs, errors = self._run_main(argv, cfg, env)
        self.assertEqual(errors, "")
        self.assertEqual(jobs[0].css, [cfg.profiles["acme"] / "style.css"])

    def test_trimmed_unknown_config_name_is_reported_trimmed(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            argv = ["--config", " acme ", str(source)]
            message, _ = self._exit_message(argv, cfg, env)
        self.assertIn("error: unknown config 'acme' (known: (none))", message)
        self.assertIn("--name acme", message)
        self.assertNotIn("' acme", message)

    def test_trimmed_unregistered_prefer_name_is_warned_trimmed(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            argv = ["--prefer-config", " acme ", str(source)]
            jobs, errors = self._run_main(argv, cfg, env)
        self.assertEqual(jobs[0].css, [quire.PLAIN_CSS])
        self.assertIn("warning: preferred config 'acme' is not registered", errors)
        self.assertIn("quire install <profile-dir> --name acme", errors)
        self.assertNotIn("' acme", errors)

    def test_trimmed_prefer_name_in_letterhead_error_is_trimmed(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            argv = ["--prefer-config", " acme ", "--letterhead", str(source)]
            message, _ = self._exit_message(argv, cfg, env)
        self.assertIn("preferred config 'acme' is not registered", message)
        self.assertNotIn("' acme", message)

    def test_prefer_config_unregistered_letterhead_uses_the_fallback_profile(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False, default="mine")
            (cfg.profiles["mine"] / "profile.toml").write_text(
                '[variants.default]\ncss = ["style.css"]\n'
                '[variants.letterhead]\ncss = ["style.css"]\n',
                encoding="utf-8",
            )
            via_default, _ = self._run_main(
                ["--prefer-config", "acme", "--letterhead", str(source)], cfg, env
            )
            via_env, _ = self._run_main(
                ["--prefer-config", "acme", "--letterhead", str(source)],
                cfg, {**env, "QUIRE_CONFIG": "mine", "QUIRE_PROFILE": ""},
            )
        for jobs in (via_default, via_env):
            self.assertEqual(jobs[0].css, [cfg.profiles["mine"] / "style.css"])

    def test_prefer_config_unregistered_with_letterhead_fails(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp, registered=False)
            with patch.dict(os.environ, env), redirect_stderr(io.StringIO()), \
                    self.assertRaisesRegex(SystemExit, "preferred config 'acme' is not registered"):
                quire.main(
                    ["--prefer-config", "acme", "--letterhead", str(source)], user_config=cfg
                )

    def test_prefer_config_registered_with_missing_profile_toml_still_fails(self) -> None:
        with temporary_directory() as tmp:
            cfg, source, env = self._prefer(tmp)
            (cfg.profiles["acme"] / "profile.toml").unlink()
            with patch.dict(os.environ, env), \
                    self.assertRaisesRegex(SystemExit, r"config 'acme' points to .*quire install"):
                quire.main(["--prefer-config", "acme", str(source)], user_config=cfg)

    def test_rename_updates_path_and_keeps_default(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            first = self._profile(root, "one")
            second = self._profile(root, "two")
            config_dir = root / "cfg"
            bin_dir = root / "bin"
            common = ["--name", "acme", "--bin-dir", str(bin_dir), "--config-dir", str(config_dir)]
            quire.main(["install", str(first), *common])
            (config_dir / "config.toml").write_text(
                (config_dir / "config.toml").read_text(encoding="utf-8").replace(
                    'default = ""', 'default = "other"'
                ),
                encoding="utf-8",
            )
            # other profile line, then update acme
            text = (config_dir / "config.toml").read_text(encoding="utf-8")
            text = text.replace("[profiles]\n", '[profiles]\nother = "/tmp/other"\n')
            (config_dir / "config.toml").write_text(text, encoding="utf-8")
            quire.main(["install", str(second), *common])
            saved = (config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn('default = "other"', saved)
        self.assertIn('other = "/tmp/other"', saved)
        self.assertIn(str(second.resolve()), saved)
        self.assertNotIn(str(first.resolve()), saved)

    def test_rejects_bad_names_and_skips_shortcut(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            profile = self._profile(root, "acme")
            config_dir = root / "cfg"
            bin_dir = root / "bin"
            for bad in ("quire", "1acme", "has space"):
                with self.assertRaises(SystemExit):
                    quire.main(
                        [
                            "install",
                            str(profile),
                            "--name",
                            bad,
                            "--bin-dir",
                            str(bin_dir),
                            "--config-dir",
                            str(config_dir),
                        ]
                    )
            quire.main(
                [
                    "install",
                    str(profile),
                    "--no-command",
                    "--bin-dir",
                    str(bin_dir),
                    "--config-dir",
                    str(config_dir),
                ]
            )
            self.assertFalse((bin_dir / "acme-pdf").exists())
            self.assertTrue((config_dir / "config.toml").is_file())


class FrontMatterTests(unittest.TestCase):
    keys = ["confidential"]
    metadata = {"confidential": "true"}

    def test_leading_rules_are_body(self) -> None:
        for text in ("---\n\nIntro\n\n---\n\nMore\n", "----\n\nIntro\n\n---\n\nMore\n"):
            with self.subTest(text=text):
                self.assertEqual(quire.split_front_matter(text), ("", text))
                ensured = quire.ensure_front_matter(text, self.keys, self.metadata)
                self.assertEqual(ensured, "---\nconfidential: true\n---\n\n" + text)
                prefixed = quire.apply_includes(text, [], ["<p>banner</p>"])
                self.assertTrue(prefixed.lstrip("\n").startswith("<p>banner</p>"))

    def test_dots_close_front_matter(self) -> None:
        text = "---\ntitle: Plan\n...\n\nBody\n"
        self.assertEqual(quire.split_front_matter(text), ("---\ntitle: Plan\n...", "\n\nBody\n"))
        self.assertEqual(
            quire.ensure_front_matter(text, self.keys, self.metadata),
            "---\ntitle: Plan\nconfidential: true\n...\n\nBody\n",
        )

    def test_byte_order_mark_precedes_front_matter(self) -> None:
        text = "﻿---\ntitle: Plan\n---\nBody\n"
        self.assertEqual(quire.split_front_matter(text), ("﻿---\ntitle: Plan\n---", "\nBody\n"))
        self.assertEqual(
            quire.ensure_front_matter(text, self.keys, self.metadata),
            "﻿---\ntitle: Plan\nconfidential: true\n---\nBody\n",
        )
        self.assertEqual(
            quire.ensure_front_matter("﻿Body\n", self.keys, self.metadata),
            "﻿---\nconfidential: true\n---\n\nBody\n",
        )

    def test_closing_line_must_be_a_delimiter(self) -> None:
        text = "---\ntitle: Plan\n---- not a close\n---\nBody\n"
        self.assertEqual(quire.split_front_matter(text)[1], "\nBody\n")


class VerbatimTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def plan(self, text: str, name: str = "form.txt", **kwargs) -> quire.Job:
        (self.root / name).write_text(text, encoding="utf-8")
        kwargs.setdefault("profile", None)
        return quire.plan_job(name, cwd=self.root, **kwargs)

    def test_text_target_is_one_fenced_block_in_a_verbatim_div(self) -> None:
        text = "Name    Qty\n  # not a heading\n\tTabbed  *x*\n"
        job = self.plan(text)
        self.assertEqual(
            job.markdown, "::: quire-verbatim\n```\n" + text + "```\n:::\n"
        )
        self.assertEqual(job.output, self.root / "form.pdf")
        self.assertEqual(job.css, [quire.PLAIN_CSS])

    def test_missing_final_newline_still_closes_the_fence(self) -> None:
        self.assertEqual(
            self.plan("a\nb").markdown, "::: quire-verbatim\n```\na\nb\n```\n:::\n"
        )

    def test_fence_outgrows_backtick_runs_in_the_text(self) -> None:
        for text, fence in (("x\n```\ny\n", "````"), ("a `````` b\n", "`" * 7),
                            ("`` one\n", "```")):
            with self.subTest(text=text):
                job = self.plan(text)
                self.assertEqual(
                    job.markdown, f"::: quire-verbatim\n{fence}\n{text}{fence}\n:::\n"
                )

    def test_leading_rule_is_not_front_matter(self) -> None:
        text = "---\ntitle: Form\n---\nBody\n"
        job = self.plan(text)
        self.assertEqual(quire.split_front_matter(job.markdown)[0], "")
        self.assertIn(text, job.markdown)
        job = self.plan(text, confidential=True)
        front, body = quire.split_front_matter(job.markdown)
        self.assertEqual(front.count("title"), 0)
        self.assertIn("confidential: true", front)
        self.assertIn(text, body)

    def test_includes_and_variant_suffix_still_apply(self) -> None:
        (self.root / "intro.html").write_text("<p>Intro</p>", encoding="utf-8")
        job = self.plan(
            "Hello\n", confidential=True, include_before=[Path("intro.html")],
            format="html",
        )
        self.assertEqual(job.output, self.root / "form-confidential.html")
        body = quire.split_front_matter(job.markdown)[1]
        self.assertLess(body.index("confidential-banner"), body.index("Intro"))
        self.assertLess(body.index("Intro"), body.index("::: quire-verbatim"))

    def test_profile_include_conditions_see_the_text(self) -> None:
        profile = self.root / "profile"
        profile.mkdir()
        (profile / "style.css").write_text("body {}", encoding="utf-8")
        (profile / "mark.html").write_text("<p>Mark</p>", encoding="utf-8")
        (profile / "profile.toml").write_text(
            '[variants.default]\ncss = ["style.css"]\n[[variants.default.includes]]\n'
            'file = "mark.html"\nonly_if_body_contains = ["FORM-7"]\n', encoding="utf-8")
        loaded = quire.load_profile(profile)
        self.assertIn("<p>Mark</p>", self.plan("FORM-7\n", profile=loaded).markdown)
        self.assertNotIn("<p>Mark</p>", self.plan("other\n", profile=loaded).markdown)

    def test_other_suffixes_are_still_rejected(self) -> None:
        for name in ("form.text", "form.pdf", "form"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    SystemExit, "target must be a .md, .markdown, or .txt file"
                ):
                    self.plan("x\n", name)

    def test_uppercase_suffix_is_accepted(self) -> None:
        self.assertEqual(self.plan("x\n", "FORM.TXT").output, self.root / "FORM.pdf")

    def test_missing_target_error_does_not_say_markdown(self) -> None:
        with self.assertRaisesRegex(SystemExit, r"^error: file not found: gone\.txt$"):
            quire.plan_job("gone.txt", profile=None, cwd=self.root)

    def test_builtin_with_text_source_is_wrapped(self) -> None:
        profile = self.root / "profile"
        profile.mkdir()
        (profile / "style.css").write_text("body {}", encoding="utf-8")
        (profile / "sheet.txt").write_text("a  b\n", encoding="utf-8")
        (profile / "profile.toml").write_text(
            '[variants.default]\ncss = ["style.css"]\n[builtins.sheet]\n'
            'source = "sheet.txt"\noutput = "sheet.pdf"\nvariant = "default"\n',
            encoding="utf-8")
        job = quire.plan_job("sheet", profile=quire.load_profile(profile), cwd=self.root)
        self.assertEqual(job.markdown, "::: quire-verbatim\n```\na  b\n```\n:::\n")

    def test_empty_text_file_plans(self) -> None:
        job = self.plan("")
        self.assertEqual(job.markdown, "::: quire-verbatim\n```\n\n```\n:::\n")

    def test_plain_stylesheets_keep_verbatim_lines_unwrapped(self) -> None:
        for path in (quire.PLAIN_CSS, quire.PLAIN_CONFIDENTIAL_CSS):
            with self.subTest(css=path.name):
                css = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
                self.assertIn(
                    ".quire-verbatim pre { white-space: pre; font-size: 9pt; }", css
                )

    def test_insertion_point_includes_prepend_for_text(self) -> None:
        profile = self.root / "profile"
        profile.mkdir()
        (profile / "style.css").write_text("body {}", encoding="utf-8")
        (profile / "mark.html").write_text("<p>Mark</p>", encoding="utf-8")
        (profile / "profile.toml").write_text(
            '[variants.default]\ncss = ["style.css"]\n[[variants.default.includes]]\n'
            'file = "mark.html"\nafter_marker = "<div"\nclose_tag = "</div>"\n',
            encoding="utf-8")
        loaded = quire.load_profile(profile)
        text = "<div>\nrow\n</div>\n"
        job = self.plan(text, profile=loaded)
        self.assertEqual(
            job.markdown, "\n<p>Mark</p>\n\n::: quire-verbatim\n```\n" + text + "```\n:::\n"
        )
        (self.root / "doc.md").write_text(text, encoding="utf-8")
        md = quire.plan_job("doc.md", profile=loaded, cwd=self.root)
        self.assertEqual(md.markdown, "<div>\nrow\n</div>\n\n<p>Mark</p>\n\n")

    def test_mermaid_fence_in_text_does_not_trigger_the_preflight(self) -> None:
        job = self.plan("```mermaid\nflowchart LR\n A-->B\n```\n")
        with patch("quire_mermaid.shutil.which", side_effect=AssertionError("checked")):
            quire.check_mermaid([job])


class MultipleTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("one", "two"):
            (self.root / f"{name}.md").write_text("Hello\n", encoding="utf-8")

    def build(self, *argv: str) -> list:
        with patch("quire.render") as render:
            quire.main([*argv, "--no-config"], user_config=quire.UserConfig(default="", profiles={}))
        return [call.args[0] for call in render.call_args_list]

    def test_each_target_renders_in_order(self) -> None:
        jobs = self.build(str(self.root / "one.md"), str(self.root / "two.md"), "--format", "html")
        self.assertEqual([job.output for job in jobs],
                         [self.root / "one.html", self.root / "two.html"])

    def test_conflicts_fail_before_any_render(self) -> None:
        one, two = str(self.root / "one.md"), str(self.root / "two.md")
        for argv, message in (
            ((one, two, "-o", "out.pdf"), "single target"),
            ((one, one), "both write"),
            ((one, str(self.root / "missing.md")), "not found"),
        ):
            with self.subTest(argv=argv):
                with patch("quire.render") as render, self.assertRaisesRegex(SystemExit, message):
                    quire.main([*argv, "--no-config"],
                               user_config=quire.UserConfig(default="", profiles={}))
                render.assert_not_called()

    FENCE = "```mermaid\nflowchart LR\n A-->B\n```\n"

    def mermaid_build(self, *argv: str) -> list:
        """Build with the renderer hidden; return the written outputs."""
        written = []

        def fake_render(job: quire.Job) -> None:
            job.output.write_text("out", encoding="utf-8")
            written.append(job.output)

        with patch("quire.render", side_effect=fake_render), \
                patch("quire_mermaid.shutil.which", return_value=None):
            quire.main([*argv, "--no-config", "--format", "html"],
                       user_config=quire.UserConfig(default="", profiles={}))
        return written

    def mermaid_targets(self) -> list[str]:
        (self.root / "two.md").write_text("Hi\n\n" + self.FENCE, encoding="utf-8")
        return [str(self.root / "one.md"), str(self.root / "two.md")]

    def test_missing_mermaid_renderer_fails_before_any_render(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.mermaid_build(*self.mermaid_targets())
        self.assertFalse(list(self.root.glob("*.html")))
        message = str(caught.exception)
        self.assertIn(f"{self.root / 'two.md'}: line 3: Mermaid tooling missing", message)
        self.assertIn("python3 install.py --with-mermaid", message)
        self.assertIn("--no-mermaid", message)

    def test_mermaid_check_names_the_fence_source(self) -> None:
        fragment = self.root / "frag.md"
        fragment.write_text("Intro\n\n" + self.FENCE, encoding="utf-8")
        front = "---\ntitle: T\n---\n\n" + self.FENCE
        for name, body, extra, expected in (
            ("fragment", "Hello\n", ["--include-before", str(fragment)], f"{fragment}: line 3:"),
            ("front matter", front, [], "two.md: line 5:"),
        ):
            with self.subTest(name):
                (self.root / "two.md").write_text(body, encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, re.escape(expected)):
                    self.mermaid_build(str(self.root / "one.md"), str(self.root / "two.md"), *extra)
                self.assertFalse(list(self.root.glob("*.html")))

    def test_mermaid_check_is_skipped_without_need(self) -> None:
        with patch("quire_mermaid.shutil.which", side_effect=AssertionError("checked")):
            jobs = self.build(*self.mermaid_targets(), "--format", "html", "--no-mermaid")
            self.assertEqual(len(jobs), 2)
            (self.root / "two.md").write_text("Hello\n", encoding="utf-8")
            both = [str(self.root / "one.md"), str(self.root / "two.md")]
            self.assertEqual(len(self.build(*both)), 2)

    def test_mermaid_check_ignores_inapplicable_includes(self) -> None:
        profile = self.root / "profile"
        profile.mkdir()
        (profile / "style.css").write_text("body {}", encoding="utf-8")
        (profile / "diagram.md").write_text(self.FENCE, encoding="utf-8")
        (profile / "profile.toml").write_text(
            '[variants.default]\ncss = ["style.css"]\n[[variants.default.includes]]\n'
            'file = "diagram.md"\nskip_if_body_contains = ["no-diagram"]\n', encoding="utf-8")
        target = self.root / "one.md"
        argv = [str(target), "--profile", str(profile)]
        target.write_text("no-diagram\n", encoding="utf-8")
        self.assertEqual(len(self.mermaid_build(*argv)), 1)
        target.write_text("Hello\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "diagram.md: line 1: Mermaid tooling missing"):
            self.mermaid_build(*argv)

    def test_mermaid_check_ignores_indented_code(self) -> None:
        indented = "Text\n\n    " + self.FENCE.replace("\n", "\n    ", 3).rstrip(" ")
        target = self.root / "one.md"
        target.write_text(indented, encoding="utf-8")
        self.assertEqual(len(self.mermaid_build(str(target))), 1)
        fence = self.FENCE
        for name, body in (
            ("list", "1. Item\n\n    " + fence),
            ("definition list", "Term\n:   Definition\n\n    " + fence),
            ("letter marker", "a.  Item\n\n    " + fence),
            ("parenthesis marker", "(1) Item\n\n    " + fence),
            ("example marker", "(@) Item\n\n    " + fence),
            ("footnote", "Text[^1]\n\n[^1]: Note\n\n    " + fence),
            ("tab after quote", ">\t" + fence),
        ):
            with self.subTest(name):
                target.write_text(body, encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, "one.md: line \\d: Mermaid tooling"):
                    self.mermaid_build(str(target))


class FontTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.profile_dir = self.root / "brand"
        self.profile_dir.mkdir()
        (self.profile_dir / "a.css").write_text("body { color: #222; }", encoding="utf-8")
        (self.profile_dir / "memo.md").write_text("Memo\n", encoding="utf-8")
        (self.root / "one.md").write_text("Hello\n", encoding="utf-8")
        (self.root / "two.md").write_text("Hello\n", encoding="utf-8")

    def profile(self, top: str = "", variant: str = "", letterhead: str = "") -> quire.Profile:
        (self.profile_dir / "profile.toml").write_text(
            f'{top}\n[variants.default]\ncss = ["a.css"]\n{variant}\n'
            '[variants.confidential]\ncss = ["a.css"]\n'
            f'[variants.letterhead]\ncss = ["a.css"]\n{letterhead}\n'
            '[builtins.memo]\nsource = "memo.md"\noutput = "memo.pdf"\n'
            'variant = "letterhead"\n',
            encoding="utf-8",
        )
        return quire.load_profile(self.profile_dir)

    def plan(self, profile, *, installed=None, name="one.md", format="pdf", **kwargs):
        stub = patch("quire.installed_font_families", return_value=installed)
        with stub as lookup:
            target = str(self.root / name) if name.endswith(".md") else name  # else built-in
            job = quire.plan_job(target, profile=profile, format=format, **kwargs)
        return job, lookup

    def run_main(self, *argv: str, cfg: quire.UserConfig | None = None) -> None:
        cfg = cfg or quire.UserConfig(default="", profiles={})
        quire.main([str(self.root / "one.md"), str(self.root / "two.md"), *argv], user_config=cfg)

    def test_fonts_parse_at_profile_and_variant_level(self) -> None:
        profile = self.profile('fonts = ["A", "B"]', 'fonts = ["C"]')
        self.assertEqual(profile.fonts, ["A", "B"])
        self.assertEqual(profile.variants["default"].fonts, ["C"])
        self.assertIsNone(profile.variants["confidential"].fonts)
        self.assertEqual(self.profile().fonts, [])

    def test_font_names_are_stripped_at_load(self) -> None:
        profile = self.profile('fonts = [" A  "]', 'fonts = ["\tB"]')
        self.assertEqual(profile.fonts, ["A"])
        self.assertEqual(profile.variants["default"].fonts, ["B"])

    def test_bad_fonts_are_errors(self) -> None:
        for top, variant in (
            ('fonts = "A"', ""), ("fonts = [1]", ""), ("", 'fonts = "A"'),
            ("", "fonts = [1, 2]"), ('fonts = [""]', ""),
        ):
            with self.subTest(top=top, variant=variant):
                with self.assertRaisesRegex(SystemExit, "fonts"):
                    self.profile(top, variant)

    def test_missing_font_names_font_and_profile(self) -> None:
        profile = self.profile('fonts = ["Brand Sans", "Present"]')
        with self.assertRaises(SystemExit) as caught:
            self.plan(profile, installed={"Present"})
        self.assertEqual(
            str(caught.exception),
            'error: profile brand needs font "Brand Sans", which is not installed',
        )
        self.assertFalse(list(self.root.glob("*.pdf")))

    def test_present_fonts_pass_case_insensitively(self) -> None:
        profile = self.profile('fonts = ["brand sans", ".SF Georgian Rounded", "Sign-Painter"]')
        job, _ = self.plan(
            profile, installed={"Brand Sans", ".sf georgian rounded", "SIGN-Painter"}
        )
        self.assertEqual(job.source, self.root / "one.md")

    def test_variant_fonts_replace_profile_fonts(self) -> None:
        profile = self.profile('fonts = ["Missing"]', 'fonts = ["Present"]')
        self.plan(profile, installed={"Present"})
        with self.assertRaisesRegex(SystemExit, '"Missing"'):
            self.plan(profile, installed={"Present"}, confidential=True)
        profile = self.profile('fonts = ["Missing"]', "fonts = []")
        self.plan(profile, installed=set())

    def test_letterhead_and_builtin_variants_use_their_own_fonts(self) -> None:
        profile = self.profile('fonts = ["Missing"]', letterhead='fonts = ["Present"]')
        self.plan(profile, installed={"Present"}, letterhead=True)
        job, _ = self.plan(profile, installed={"Present"}, name="memo")
        self.assertEqual(job.source, self.profile_dir / "memo.md")
        for kwargs in ({"letterhead": True}, {"name": "memo"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(SystemExit, '"Present"'):
                    self.plan(profile, installed={"Missing"}, **kwargs)
        profile = self.profile('fonts = ["Missing"]')
        with self.assertRaisesRegex(SystemExit, '"Missing"'):
            self.plan(profile, installed=set(), name="memo")

    def test_css_overrides_still_check(self) -> None:
        profile = self.profile('fonts = ["Missing"]')
        other = self.root / "b.css"
        other.write_text("body {}", encoding="utf-8")
        for kwargs in ({"css": [other]}, {"no_css": True}, {"add_css": [other]}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(SystemExit, '"Missing"'):
                    self.plan(profile, installed={"Other"}, **kwargs)

    def test_html_output_is_not_checked(self) -> None:
        profile = self.profile('fonts = ["Missing"]')
        _, lookup = self.plan(profile, installed=set(), format="html")
        lookup.assert_not_called()
        with self.assertRaisesRegex(SystemExit, '"Missing"'):
            self.plan(profile, installed=set(), format="pdf")
        with patch("quire.installed_font_families") as lookup, patch("quire.render"):
            self.run_main("--profile", str(self.profile_dir), "--format", "html")
        lookup.assert_not_called()

    def test_unavailable_lookup_warns_once_and_continues(self) -> None:
        self.profile('fonts = ["Missing"]')
        stderr = io.StringIO()
        error = quire.FontLookupError("fc-list is unavailable")
        with patch("quire.installed_font_families", side_effect=error) as lookup, \
                redirect_stderr(stderr), patch("quire.render"):
            self.run_main("--profile", str(self.profile_dir))
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(
            stderr.getvalue(), "warning: fc-list is unavailable; profile fonts were not checked\n"
        )

    def test_timeout_has_its_own_warning(self) -> None:
        self.profile('fonts = ["Missing"]')
        stderr = io.StringIO()
        with patch("quire.subprocess.run", side_effect=subprocess.TimeoutExpired("fc-list", 60)), \
                redirect_stderr(stderr), patch("quire.render"):
            self.run_main("--profile", str(self.profile_dir))
        self.assertEqual(
            stderr.getvalue(),
            "warning: fc-list timed out after 60s; profile fonts were not checked\n",
        )

    def test_batch_looks_up_once_and_fails_before_any_render(self) -> None:
        self.profile('fonts = ["Missing"]')
        with patch("quire.installed_font_families", return_value={"Other"}) as lookup, \
                patch("quire.render") as render, \
                self.assertRaisesRegex(SystemExit, '"Missing"'):
            self.run_main("--profile", str(self.profile_dir))
        self.assertEqual(lookup.call_count, 1)
        render.assert_not_called()

    def font_error_name(self, *argv: str, cfg: quire.UserConfig, env=None) -> str:
        """Run main with no fonts installed; return the profile name in the error."""
        clean = {"QUIRE_CONFIG": "", "QUIRE_PROFILE": "", **(env or {})}
        with patch.dict("os.environ", clean), \
                patch("quire.installed_font_families", return_value=set()), \
                self.assertRaisesRegex(SystemExit, "error: profile (\\S+) needs") as caught:
            self.run_main(*argv, cfg=cfg)
        return re.match("error: profile (\\S+) needs", str(caught.exception)).group(1)

    def test_error_names_the_config_actually_used(self) -> None:
        self.profile('fonts = ["Missing"]')
        here = self.profile_dir
        cfg = quire.UserConfig(default="", profiles={"acme": here})
        both = quire.UserConfig(default="zzz", profiles={"aaa": here, "zzz": here})
        for label, argv, config, env, name in (
            ("--config", ["--config", "acme"], cfg, None, "acme"),
            ("--config alias", ["--config", "zzz"], both, None, "zzz"),
            ("--prefer-config", ["--prefer-config", "acme"], cfg, None, "acme"),
            ("QUIRE_CONFIG", [], cfg, {"QUIRE_CONFIG": "acme"}, "acme"),
            ("user default alias", [], both, None, "zzz"),
            ("--profile path", ["--profile", str(here)], cfg, None, "brand"),
            ("QUIRE_PROFILE", [], cfg, {"QUIRE_PROFILE": str(here)}, "brand"),
        ):
            with self.subTest(label):
                self.assertEqual(self.font_error_name(*argv, cfg=config, env=env), name)

    def test_prefer_warning_comes_before_font_warning(self) -> None:
        self.profile('fonts = ["Missing"]')
        stderr = io.StringIO()
        env = {"QUIRE_CONFIG": "", "QUIRE_PROFILE": str(self.profile_dir)}
        error = quire.FontLookupError("fc-list is unavailable")
        with patch.dict("os.environ", env), redirect_stderr(stderr), patch("quire.render"), \
                patch("quire.installed_font_families", side_effect=error):
            self.run_main("--prefer-config", "nope")
        lines = stderr.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("warning: preferred config 'nope'"))
        self.assertEqual(
            lines[-1], "warning: fc-list is unavailable; profile fonts were not checked"
        )

    def test_profile_without_fonts_never_looks_up(self) -> None:
        _, lookup = self.plan(self.profile(), installed=None)
        lookup.assert_not_called()

    def run_fc_list(self, **kwargs):
        with patch("quire.subprocess.run", **kwargs) as run:
            return run, quire.installed_font_families()

    def test_lookup_parses_fc_list(self) -> None:
        # With --format each family is one unescaped line, repeated for each face.
        out = (
            "SignPainter\nSignPainter-HouseScript\nSignPainter\n"
            ".SF Georgian Rounded\nFoo, Bar: Baz\\Qux\n\n  Spaced  \n"
        )
        done = subprocess.CompletedProcess([], 0, out, "")
        run, families = self.run_fc_list(return_value=done)
        self.assertEqual(
            families,
            {"SignPainter", "SignPainter-HouseScript", ".SF Georgian Rounded",
             "Foo, Bar: Baz\\Qux", "Spaced"},
        )
        self.assertEqual(
            run.call_args.args[0][1:], ["--format", "%{[]family{%{family}\\n}}"]
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")

    def test_lookup_failures_raise(self) -> None:
        for kwargs, message in (
            ({"side_effect": FileNotFoundError()}, "fc-list is unavailable"),
            ({"side_effect": subprocess.TimeoutExpired("fc-list", 1)},
             "fc-list timed out after 60s"),
            ({"return_value": subprocess.CompletedProcess([], 1, "Real Font\n", "")},
             "fc-list failed"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(quire.FontLookupError, message):
                    self.run_fc_list(**kwargs)


class VersionTests(unittest.TestCase):
    def test_version_prints_without_reading_config(self) -> None:
        buf = io.StringIO()
        with patch.dict("os.environ", {"QUIRE_PROG": "", "QUIRE_CONFIG": "", "QUIRE_PROFILE": ""}), \
                patch.object(quire, "load_user_config", side_effect=AssertionError("config")), \
                redirect_stdout(buf):
            quire.main(["--version"])
        self.assertEqual(buf.getvalue(), "quire 1.1.1\n")


if __name__ == "__main__":
    unittest.main()
