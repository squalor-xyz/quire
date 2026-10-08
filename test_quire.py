#!/usr/bin/env python3
"""Stdlib tests for quire profile selection, config lookup, and install text."""

from __future__ import annotations

import io
import stat
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


class VersionTests(unittest.TestCase):
    def test_version_prints_without_reading_config(self) -> None:
        buf = io.StringIO()
        with patch.dict("os.environ", {"QUIRE_PROG": "", "QUIRE_CONFIG": "", "QUIRE_PROFILE": ""}), \
                patch.object(quire, "load_user_config", side_effect=AssertionError("config")), \
                redirect_stdout(buf):
            quire.main(["--version"])
        self.assertEqual(buf.getvalue(), "quire 1.0.2\n")


if __name__ == "__main__":
    unittest.main()
