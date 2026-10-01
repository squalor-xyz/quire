#!/usr/bin/env python3
"""Stdlib tests for quire profile selection, config lookup, and install text."""

from __future__ import annotations

import io
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import install
import quire

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
        with self.assertRaises(SystemExit):
            quire.plan_job(str(path), profile=None, letterhead=True, cwd=self.root)

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


if __name__ == "__main__":
    unittest.main()
