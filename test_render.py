"""Tool-independent tests for the shared pipeline and offline resource handling."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import install
import quire
import quire_math
from quire_html import Resources
from quire_common import cache_directory, prefix_svg, validate_svg
from quire_mermaid import MermaidRenderer, annotated_fences, fence_locations, render_blocks
from quire_pdf import diagram_height_ratio

ROOT = Path(__file__).parent
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 60" id="chart">'
       '<style>#chart .edge {stroke:#fff;marker-end:url(#arrow)}</style>'
       '<defs><marker id="arrow" /></defs><text id="label" x="5" y="20">Hello</text>'
       '<use href="#label" aria-labelledby="label" /></svg>')


class RenderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "notes.md"
        self.source.write_text("# Notes\n\nHello\n", encoding="utf-8")

    def job(self, **kwargs):
        return quire.plan_job(str(self.source), profile=None, cwd=self.root, **kwargs)

    def test_formats_and_explicit_paths(self):
        for output, explicit, expected in (
            (None, None, "pdf"), (None, "html", "html"),
            (Path("notes.HTML"), None, "html"), (Path("custom.pdf"), "html", "html"),
            (Path("custom.html"), "pdf", "pdf"), (Path("custom.other"), None, "pdf"),
        ):
            with self.subTest(output=output, explicit=explicit):
                job = self.job(output=output, format=explicit, confidential=True)
                self.assertEqual(job.format, expected)
                self.assertEqual(job.output, self.root / (str(output) if output else f"notes-confidential.{expected}"))

    def test_profile_settings_and_builtin_html_output(self):
        (self.root / "profile.toml").write_text(
            '[mermaid]\ntheme="base"\nfont_family="sans-serif"\n'
            'theme_variables={primaryColor="#eeeeee",lineColor="#111111"}\n'
            '[variants.default]\n'
            '[variants.default.mermaid]\ntheme_variables={lineColor="#222222"}\n'
            '[builtins.sample]\nsource="notes.md"\noutput="out/result.pdf"\n', encoding="utf-8",
        )
        profile = quire.load_profile(self.root)
        job = quire.plan_job("sample", profile=profile, cwd=self.root, format="html")
        self.assertEqual(job.output, self.root / "out/result.html")
        self.assertEqual(job.mermaid, {"theme": "base", "fontFamily": "sans-serif",
                                     "themeVariables": {"primaryColor": "#eeeeee", "lineColor": "#222222"}})

    def test_profile_settings_reject_invalid_values(self):
        for value in ("wrong", {"theme": "missing"}, {"font_family": 12},
                      {"theme_variables": []}, {"unknown": True}):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                quire._load_mermaid(value)

    def test_diagram_height_ratio_reads_the_stylesheet(self):
        self.assertEqual(diagram_height_ratio("<style>p { color: red }</style>"), 0.65)
        declared = "<style>.quire-diagram > svg { max-width: 100%; max-height: 50%; }</style>"
        self.assertEqual(diagram_height_ratio(declared), 0.5)
        self.assertEqual(diagram_height_ratio(declared + "<style>.quire-diagram svg{max-height:40%}</style>"), 0.4)

    def test_cli_flags(self):
        parser = quire._parser(None, "test", "quire")
        args = parser.parse_args([str(self.source), "--format", "html", "--no-mermaid", "-v"])
        self.assertEqual(args.format, "html")
        self.assertTrue(args.no_mermaid)
        self.assertTrue(args.verbose)

    def test_success_is_quiet_and_html_needs_no_weasyprint(self):
        job = self.job(format="html")
        ast = {"meta": {}, "blocks": [], "pandoc-api-version": [1, 23]}
        results = [subprocess.CompletedProcess([], 0, json.dumps(ast), ""),
                   subprocess.CompletedProcess([], 0, "<html><head></head><body>Hello</body></html>", "")]
        output, errors = io.StringIO(), io.StringIO()
        with patch("quire.which_pandoc", return_value="pandoc"), \
                patch("quire.resolve_weasyprint") as weasy, \
                patch("quire.subprocess.run", side_effect=results), \
                redirect_stdout(output), redirect_stderr(errors):
            quire.render(job)
        weasy.assert_not_called()
        self.assertEqual(output.getvalue(), f"wrote {job.output}\n")
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("Content-Security-Policy", job.output.read_text())

    def build_html(self, blocks, meta=None, head="", **kwargs):
        """Run render with a stubbed Pandoc; return (HTML, AST sent to the writer, stderr)."""
        job = self.job(format="html", **kwargs)
        ast = {"meta": meta or {}, "blocks": blocks, "pandoc-api-version": [1, 23]}
        page = f"<html><head>{head}</head><body>x</body></html>"
        results = [subprocess.CompletedProcess([], 0, json.dumps(ast), ""),
                   subprocess.CompletedProcess([], 0, page, "")]
        errors = io.StringIO()
        with patch("quire.which_pandoc", return_value="pandoc"), \
                patch("quire.subprocess.run", side_effect=results) as run, \
                redirect_stdout(io.StringIO()), redirect_stderr(errors):
            quire.render(job)
        sent = json.loads(run.call_args_list[1].kwargs["input"])
        return job.output.read_text(), sent["blocks"], errors.getvalue(), sent["meta"]

    def test_page_break_tex_blocks_become_html(self):
        blocks = [{"t": "RawBlock", "c": ["tex", command]}
                  for command in ("\\newpage", "\\pagebreak", "\\clearpage")]
        html, sent, errors, _ = self.build_html(blocks)
        for block in sent:
            self.assertEqual(block, {"t": "RawBlock", "c": ["html", '<div class="quire-page-break"></div>']})
        self.assertEqual(errors, "")

    def test_page_break_tex_inline_becomes_html(self):
        inline = {"t": "RawInline", "c": ["tex", "\\pagebreak "]}
        html, sent, errors, _ = self.build_html(
            [{"t": "Para", "c": [{"t": "Str", "c": "a"}, inline, {"t": "Str", "c": "b"}]}])
        self.assertEqual(sent[0]["c"][1], {"t": "RawInline", "c": [
            "html", '<span class="quire-page-break"></span>']})
        self.assertEqual(errors, "")

    def test_other_raw_tex_is_dropped_with_one_warning(self):
        blocks = [{"t": "RawBlock", "c": ["latex", "\\vspace{1cm}"]},
                  {"t": "Para", "c": [{"t": "RawInline", "c": ["tex", "\\textbf{x}"]}]},
                  {"t": "RawBlock", "c": ["html", "<hr>"]}]
        html, sent, errors, _ = self.build_html(blocks)
        self.assertEqual(sent, [blocks[2]])
        self.assertEqual(errors.count("warning"), 1)
        self.assertIn(str(self.source), errors)
        self.assertIn("\\vspace", errors)

    def test_macro_definitions_are_dropped_silently(self):
        definitions = (
            "\\newcommand{\\R}{\\mathbb{R}}", "\\newcommand*{\\norm}[1]{\\left\\|#1\\right\\|}",
            "\\renewcommand\\vec[2][x]{\\mathbf{#2}_{#1}}", "\\providecommand{\\e}{\\mathrm{e}}",
            "\\def\\a#1#2{#1+#2}", "\\gdef\\b{\\{\\}}", "\\DeclareMathOperator{\\tr}{tr}",
            "\\DeclareMathOperator*{\\argmax}{arg\\,max}", "\\newcommand{\\R}{\\}}",
            "\\newcommand{\\R}{\\mathbb{R}} % reals\n\\def\\a{1}\n\n\\DeclareMathOperator{\\tr}{tr}",
            "% only a comment",
        )
        for text in definitions:
            with self.subTest(text=text):
                blocks = [{"t": "RawBlock", "c": ["tex", text]},
                          {"t": "Para", "c": [{"t": "RawInline", "c": ["tex", text]}]},
                          {"t": "RawBlock", "c": ["html", "<hr>"]}]
                html, sent, errors, _ = self.build_html(blocks)
                self.assertEqual(sent, [blocks[2]])
                self.assertEqual(errors, "")

    def test_latex_raw_attribute_definitions_still_warn(self):
        # Pandoc does not expand macros in a {=latex} raw attribute, so these are not
        # definitions it consumed; only the "tex" format is exempt.
        text = "\\newcommand{\\R}{\\mathbb{R}}"
        for blocks in ([{"t": "RawBlock", "c": ["latex", text]}],
                       [{"t": "Para", "c": [{"t": "RawInline", "c": ["latex", text]}]}]):
            with self.subTest(blocks=blocks):
                html, sent, errors, _ = self.build_html(blocks)
                self.assertEqual(errors.count("warning"), 1)
                self.assertIn("including \\newcommand)", errors)

    def test_page_break_after_definitions_is_kept(self):
        text = "\\newcommand{\\R}{\\mathbb{R}}\n\\newpage % break"
        html, sent, errors, _ = self.build_html([{"t": "RawBlock", "c": ["tex", text]}])
        self.assertEqual(sent, [{"t": "RawBlock", "c": ["html", '<div class="quire-page-break"></div>']}])
        self.assertEqual(errors, "")

    def test_macro_definitions_in_metadata_are_dropped_silently(self):
        raw = {"t": "RawInline", "c": ["tex", "\\newcommand{\\R}{\\mathbb{R}}"]}
        meta = {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "T"}, raw]}}
        html, sent, errors, sent_meta = self.build_html([], meta=meta)
        self.assertEqual(sent_meta["title"]["c"], [{"t": "Str", "c": "T"}])
        self.assertEqual(errors, "")

    def test_definitions_mixed_with_other_tex_still_warn(self):
        for text, command in (
            ("\\newcommand{\\R}{\\mathbb{R}}\n\\vspace{1cm}", "\\vspace"),
            ("\\vspace{1cm}\n\\def\\a{1}", "\\vspace"),
            ("\\def\\a{1}\n\\textbf{x}\n\\def\\b{2}", "\\textbf"),
            ("\\newcommand{\\R}{\\mathbb{R}} trailing words", "trailing words"),
            ("\\newcommand{\\R}{x} \\% y", "\\%"),
        ):
            with self.subTest(text=text):
                html, sent, errors, _ = self.build_html([{"t": "RawBlock", "c": ["tex", text]}])
                self.assertEqual(sent, [])
                self.assertEqual(errors.count("warning"), 1)
                self.assertIn(f"including {command})", errors)

    def test_malformed_or_other_definitions_still_warn(self):
        for text in ("\\newcommand{\\R}{\\mathbb{R}", "\\newcommand{\\R}", "\\newenvironment{x}{a}{b}",
                     "\\let\\a\\b", "\\newcommandx{\\R}{r}", "\\newcommand{\\R\\S}{x}", "\\def\\a", "\\usepackage{amsmath}"):
            with self.subTest(text=text):
                html, sent, errors, _ = self.build_html([{"t": "RawBlock", "c": ["tex", text]}])
                self.assertEqual(errors.count("warning"), 1)

    def raw_forms(self, text, kind="tex"):
        """The same raw TeX as a block and as an inline, for subTest loops."""
        return ([{"t": "RawBlock", "c": [kind, text]}],
                [{"t": "Para", "c": [{"t": "RawInline", "c": [kind, text]}]}])

    def test_layout_command_definitions_still_warn(self):
        names = ("baselinestretch", "arraystretch", "familydefault", "rmdefault",
                 "sfdefault", "ttdefault", "linespread")
        for name in names:
            forms = (
                f"\\renewcommand{{\\{name}}}{{1.5}}", f"\\renewcommand\\{name}{{1.5}}",
                f"\\renewcommand*{{\\{name}}}{{1.5}}", f"\\renewcommand*  \\{name}{{1.5}}",
                f"\\renewcommand{{ \\{name} }}{{1.5}}", f"\\newcommand{{\\{name}}}{{1.5}}",
                f"\\providecommand\\{name}{{1.5}}", f"\\def\\{name}{{1.5}}",
                f"\\gdef\\{name}{{1.5}}", f"\\def \\{name}{{1.5}}",
                f"\\DeclareMathOperator{{\\{name}}}{{x}}",
                f"\\DeclareMathOperator*\\{name}{{x}}",
                f"\\renewcommand\n{{\\{name}}}{{1.5}}", f"\\renewcommand\n\\{name}{{1.5}}",
                f"\\renewcommand{{\\{name}}}[1]{{#1}}", f"\\renewcommand{{\\{name}}}[1][2]{{#1}}",
                f"\\def\\{name}#1{{#1}}", f"\\def\\{name} #1#2{{#1}}",
            )
            for text in forms:
                for blocks in self.raw_forms(text):
                    with self.subTest(text=text, blocks=blocks):
                        html, sent, errors, _ = self.build_html(blocks)
                        self.assertEqual(errors.count("warning"), 1)
                        head = re.match(r"\\[A-Za-z]+", text).group(0)
                        self.assertIn(f"including {head})", errors)

    def test_layout_definition_warning_names_the_definition_head(self):
        for text, command in (
            ("\\renewcommand{\\baselinestretch}{1.5}", "\\renewcommand"),
            ("\\def\\arraystretch{1.2}", "\\def"),
            ("\\DeclareMathOperator{\\linespread}{x}", "\\DeclareMathOperator"),
        ):
            with self.subTest(text=text):
                html, sent, errors, _ = self.build_html([{"t": "RawBlock", "c": ["tex", text]}])
                self.assertEqual(sent, [])
                self.assertIn(f"including {command})", errors)

    def test_mixed_ordinary_and_layout_definitions_warn_on_the_first_remaining(self):
        for text, command in (
            ("\\newcommand{\\R}{\\mathbb{R}}\n\\renewcommand{\\baselinestretch}{1.5}", "\\renewcommand"),
            ("\\renewcommand{\\baselinestretch}{1.5}\n\\def\\a{1}\n\\newcommand{\\R}{x}", "\\renewcommand"),
            ("\\def\\a{1}\n\\def\\familydefault{\\sfdefault}\n\\def\\b{2}", "\\def"),
            ("\\newcommand{\\R}{x}\n\\def\\arraystretch{2}\n\\vspace{1cm}", "\\def"),
        ):
            for blocks in self.raw_forms(text):
                with self.subTest(text=text, blocks=blocks):
                    html, sent, errors, _ = self.build_html(blocks)
                    self.assertEqual(errors.count("warning"), 1)
                    self.assertIn(f"including {command})", errors)

    def test_only_the_layout_definition_is_kept_in_a_mixed_block(self):
        text = "\\newcommand{\\R}{x}\n\\textbf{y}\n\\def\\a{1}"
        self.assertEqual(quire.without_definitions(text), "\\textbf{y}")
        text = "\\newcommand{\\R}{x}\n\\renewcommand{\\baselinestretch}{1.5}\n\\def\\a{1}"
        self.assertEqual(quire.without_definitions(text), "\\renewcommand{\\baselinestretch}{1.5}")

    def test_similar_names_and_layout_names_in_bodies_stay_silent(self):
        for text in (
            "\\renewcommand{\\baselinestretchx}{1.5}", "\\renewcommand\\rmdefaults{x}",
            "\\def\\linespreadfoo{1}", "\\newcommand{\\arraystretch@}{1}",
            "\\newcommand{\\baselinestretches}{1}", "\\DeclareMathOperator{\\ttdefaultx}{x}",
            "\\newcommand{\\a}{\\baselinestretch}", "\\def\\a{\\renewcommand\\arraystretch{2}}",
            "\\newcommand{\\a}[1]{\\linespread{#1}}", "\\providecommand{\\a}{\\def\\familydefault{x}}",
            "\\DeclareMathOperator{\\a}{\\rmdefault}", "\\gdef\\a{\\sfdefault}",
        ):
            for blocks in self.raw_forms(text):
                with self.subTest(text=text, blocks=blocks):
                    html, sent, errors, _ = self.build_html(blocks)
                    self.assertEqual(errors, "")

    def test_layout_commands_still_warn(self):
        for text, command in (
            ("\\setlength{\\parindent}{0pt}", "\\setlength"),
            ("\\geometry{margin=1in}", "\\geometry"),
            ("\\linespread{1.3}", "\\linespread"),
            ("\\setlength{\\parskip}{1em}\n\\def\\a{1}", "\\setlength"),
        ):
            for blocks in self.raw_forms(text):
                with self.subTest(text=text, blocks=blocks):
                    html, sent, errors, _ = self.build_html(blocks)
                    self.assertEqual(errors.count("warning"), 1)
                    self.assertIn(f"including {command})", errors)

    def test_raw_tex_in_metadata_is_dropped_with_warning(self):
        raw = {"t": "RawInline", "c": ["tex", "\\usepackage{x}"]}
        meta = {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "T"}, raw]}}
        html, sent, errors, sent_meta = self.build_html([], meta=meta)
        self.assertEqual(sent_meta["title"]["c"], [{"t": "Str", "c": "T"}])
        self.assertIn("\\usepackage", errors)

    def test_page_break_suffixes_are_accepted(self):
        blocks = [{"t": "RawBlock", "c": ["tex", command]}
                  for command in ("\\pagebreak[4]", "\\newpage{}", "\\clearpage[]{}")]
        html, sent, errors, _ = self.build_html(blocks)
        self.assertEqual({block["c"][0] for block in sent}, {"html"})
        self.assertEqual(errors, "")

    def test_paragraphs_emptied_by_dropped_tex_are_removed(self):
        raw = {"t": "RawInline", "c": ["tex", "\\textbf{x}"]}
        blocks = [{"t": "Para", "c": [raw]}, {"t": "Plain", "c": [raw]},
                  {"t": "BulletList", "c": [[{"t": "Plain", "c": [raw]}]]}]
        html, sent, errors, _ = self.build_html(blocks)
        self.assertEqual(sent, [{"t": "BulletList", "c": [[]]}])

    def test_page_break_rule_precedes_stylesheets(self):
        html, sent, errors, _ = self.build_html([], head="<style>p{}</style>")
        self.assertLess(html.index(".quire-page-break {"), html.index("p{}"))

    def test_page_break_rule_is_present_without_css(self):
        html, sent, errors, _ = self.build_html([], no_css=True)
        self.assertIn(".quire-page-break { break-after:page; page-break-after:always; }", html)
        self.assertIn("span.quire-page-break { display:block;", html)

    def test_worker_interpreter_matches_weasyprint(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        python = bin_dir / "python3.12"
        python.write_text("", encoding="utf-8")
        script = bin_dir / "weasyprint"
        engine = self.root / "engine"
        venv_bin = engine / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("", encoding="utf-8")
        with patch("quire.ENGINE_DIR", engine):
            # A PATH script names its interpreter, even when a venv exists.
            script.write_text(f"#!{python}\nimport weasyprint\n", encoding="utf-8")
            self.assertEqual(quire.weasyprint_python(str(script)), python)
            script.write_text("#!/usr/bin/env -S python3.12 -u\n", encoding="utf-8")
            with patch("quire.shutil.which", return_value=str(python)):
                self.assertEqual(quire.weasyprint_python(str(script)), python)
            self.assertEqual(quire.weasyprint_python(str(venv_bin / "weasyprint")), venv_bin / "python")
            # Without a usable #! line, keep the sibling and current-interpreter fallbacks.
            script.write_text("#!/missing/python\n", encoding="utf-8")
            self.assertEqual(quire.weasyprint_python(str(script)), Path(quire.sys.executable))
            (bin_dir / "python").write_text("", encoding="utf-8")
            self.assertEqual(quire.weasyprint_python(str(script)), bin_dir / "python")

    def test_failure_preserves_output_and_verbose_uses_stderr(self):
        job = self.job(format="html", verbose=True)
        job.output.write_text("previous output", encoding="utf-8")
        errors = io.StringIO()
        with patch("quire.which_pandoc", return_value="pandoc"), \
                patch("quire.subprocess.run", side_effect=subprocess.CalledProcessError(1, [], stderr="bad input")), \
                redirect_stderr(errors), self.assertRaisesRegex(SystemExit, "bad input"):
            quire.render(job)
        self.assertEqual(job.output.read_text(), "previous output")
        self.assertIn("running:", errors.getvalue())

    def test_mermaid_installer_is_explicit_and_project_local(self):
        with patch("install.shutil.which", side_effect=lambda name: f"/tools/{name}"), \
                patch("install.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "v22.13.0\n")) as run:
            install.ensure_mermaid()
        command = run.call_args_list[1].args[0]
        self.assertEqual(command[1:3], ["ci", "--prefix"])
        self.assertEqual(run.call_args.kwargs["env"]["PUPPETEER_CACHE_DIR"], str(ROOT / ".cache/puppeteer"))
        self.assertTrue(run.call_args.args[0][1].endswith('puppeteer/install.mjs'))
        with patch("install.install") as mocked:
            install.main(["--with-mermaid", "--skip-venv", "--python", str(self.root / "python"),
                          "--bin-dir", str(self.root / "bin"), "--config-dir", str(self.root / "config")])
        self.assertTrue(mocked.call_args.kwargs["with_mermaid"])
        with patch("install.shutil.which", return_value=None), self.assertRaisesRegex(SystemExit, "Node"):
            install.ensure_mermaid()


class HTMLResourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.resources = Resources([self.root])
        self.image = self.root / "pixel.png"
        self.image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))

    def test_images_css_imports_and_fonts_are_embedded_from_their_origins(self):
        assets = self.root / "styles"
        assets.mkdir()
        (assets / "font.woff2").write_bytes(b"test font")
        (assets / "child.css").write_text('@font-face {font-family:Example;src:url(font.woff2)}')
        (assets / "main.css").write_text('@import "child.css" screen; body {background:url(../pixel.png)}')
        output = self.resources.document('<link rel="stylesheet" href="styles/main.css"><img src="pixel.png">')
        self.assertIn("<style>@import url(\"data:text/css;base64,", output)
        self.assertIn("data:image/png;base64,", output)
        self.assertNotIn('<link', output)
        embedded = self.resources.css((assets / "child.css").read_text(), assets)
        self.assertIn("data:font/woff2;base64,", embedded)

    def test_css_strings_comments_and_local_svg_references(self):
        css = '/* url(https://example.invalid) */ p:before {content:"url(https://example.invalid)"} path {fill:url(#paint)}'
        self.assertEqual(self.resources.css(css), css.replace('url(#paint)', 'url("#paint")'))

    def test_remote_and_missing_assets_fail_without_fetching(self):
        for content in ('<img src="https://example.invalid/a.png">',
                        '<style>@import "https://example.invalid/a.css";</style>',
                        '<style>p{background:u\\72l(https://example.invalid/a.png)}</style>',
                        '<img src="missing.png">', '<script>fetch("https://example.invalid")</script>',
                        '<iframe src="local.html"></iframe>', '<body onload="alert(1)">'):
            with self.subTest(content=content), self.assertRaises(SystemExit):
                self.resources.document(content)

    def test_data_svg_resources_are_checked_and_links_remain_links(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/a.png" /></svg>'
        uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
        with self.assertRaisesRegex(SystemExit, "offline"):
            self.resources.document(f'<img src="{uri}">')
        link = '<a href="https://example.invalid">Link</a>'
        self.assertEqual(self.resources.document(link), link)

    def test_circular_css_import_and_srcset(self):
        (self.root / "a.css").write_text('@import "b.css";')
        (self.root / "b.css").write_text('@import "a.css";')
        with self.assertRaisesRegex(SystemExit, "circular"):
            self.resources.embed("a.css")
        output = self.resources.document('<img srcset="pixel.png 1x, pixel.png 2x">')
        self.assertEqual(output.count('data:image/png;base64,'), 2)
        self.assertIn(' 2x', output)

    def test_image_set_and_svg_fragments_survive_reembedding(self):
        (self.root / 'icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                                          '<path id="shape" d="M0 0h10v10H0z"/></svg>')
        embedded = self.resources.embed('icon.svg#shape')
        self.assertTrue(self.resources.embed(embedded).endswith('#shape'))
        css = 'p{background:image-set("pixel.png" 1x, url(pixel.png) 2x)}'
        self.assertEqual(self.resources.css(css).count('data:image/png;base64,'), 2)
        with self.assertRaisesRegex(SystemExit, 'offline'):
            self.resources.css('p{background:image-set("https://example.invalid/a.png" 1x)}')

    def test_script_urls_are_rejected_after_whitespace_is_removed(self):
        samples = (
            '<a href="javascript:alert(1)">x</a>',
            '<a href=" javascript:alert(1)">x</a>',
            '<a href="java\nscript:alert(1)">x</a>',
            '<svg><a xlink:href="javascript:alert(1)">x</a></svg>',
            '<form action="javascript:alert(1)"></form>',
            '<button formaction="vb\tscript:alert(1)">x</button>',
            '<image href=" javascript:alert(1)"/>',
        )
        for content in samples:
            with self.subTest(content=content), self.assertRaisesRegex(SystemExit, "JavaScript"):
                self.resources.document(content)


class MermaidTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def block(self, source):
        return {"t": "CodeBlock", "c": [["", ["mermaid"], []], source]}

    def test_fence_locations_skip_examples_and_preserve_container_lines(self):
        text = ('---\ntitle: Example\n---\n\n````text\n```mermaid\nnot a diagram\n```\n````\n'
                '\n> ~~~{.mermaid}\n> flowchart TD\n>  A-->B\n> ~~~\n'
                '\n- Item\n\n  ```mermaid\n  erDiagram\n   A ||--o{ B : owns\n  ```\n')
        self.assertEqual(fence_locations(text), [('flowchart TD\n A-->B', 11), ('erDiagram\n A ||--o{ B : owns', 18)])

    def test_unclosed_leading_rule_is_not_front_matter(self):
        text = "---\n\n```mermaid\nflowchart TD\n  A-->B\n```\n"
        self.assertEqual(fence_locations(text), [("flowchart TD\n  A-->B", 3)])

    def test_ast_locations_survive_profile_prefixes_and_errors_name_source(self):
        source = "flowchart TD\n A-->B"
        original = f"# Notes\n\n```mermaid\n{source}\n```\n"
        job = quire.Job(self.root / "notes.md", self.root / "out.html", "prefix\n" + original,
                        [], {}, str(self.root), self.root, original_markdown=original)
        renderer = Mock()
        renderer.render.side_effect = ValueError("bad diagram")
        with self.assertRaisesRegex(SystemExit, r"notes.md: Mermaid block 1, line 3: bad diagram"):
            render_blocks({"blocks": [self.block(source)]}, job, "", renderer_factory=lambda *args: renderer)

    def test_annotations_preserve_containers_attributes_and_crlf_lines(self):
        original = '- ```mermaid\r\n  flowchart LR\r\n   A-->B\r\n  ```\r\n\r\n> ~~~{#id .mermaid}\r\n> erDiagram\r\n> ~~~\r\n'
        result = annotated_fences(original)
        self.assertIn('- ```{.mermaid data-quire-source-line="1"}\r\n', result)
        self.assertIn('> ~~~{#id .mermaid data-quire-source-line="6"}\r\n', result)
        self.assertEqual(fence_locations(original), [('flowchart LR\n A-->B', 1), ('erDiagram', 6)])

    def test_no_mermaid_and_no_diagrams_do_not_construct_renderer(self):
        job = quire.Job(self.root / "notes.md", self.root / "out.html", "", [], {}, str(self.root), self.root)
        factory = Mock()
        self.assertFalse(render_blocks({"blocks": []}, job, "", factory))
        job.no_mermaid = True
        self.assertFalse(render_blocks({"blocks": [self.block("bad")]}, job, "", factory))
        factory.assert_not_called()

    def test_inline_svg_has_independent_ids_and_preserves_colors(self):
        first, second = prefix_svg(SVG, "first-"), prefix_svg(SVG, "second-")
        self.assertIn('id="first-chart"', first)
        self.assertIn('url(#first-arrow)', first)
        self.assertIn('href="#second-label"', second)
        self.assertIn('aria-labelledby="second-label"', second)
        self.assertIn('stroke:#fff', first)
        self.assertNotIn('id="chart"', first)
        for invalid in ('<pre>bad</pre>', '<svg viewBox="0 0 0 0"/>',
                        '<svg viewBox="0 0 10 10"><foreignObject/></svg>'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_svg(invalid)

    def test_cache_directory_honors_xdg_and_names_the_subdirectory(self):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.root)}):
            self.assertEqual(cache_directory("math"), self.root / "quire" / "math")
        with patch.dict(os.environ, {}, clear=False) as env:
            env.pop("XDG_CACHE_HOME", None)
            with patch("quire_common.Path.home", return_value=self.root):
                self.assertEqual(
                    cache_directory("mermaid"), self.root / ".cache/quire/mermaid"
                )

    def test_cache_hits_invalid_entries_and_theme_changes(self):
        engine = self.root / "engine"
        manifest = engine / "node_modules/@mermaid-js/mermaid-cli/package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{}')
        (engine / 'mermaid-render.mjs').write_text('// test worker')
        calls = []
        def worker(renderer, args, payload):
            if payload is not None:
                calls.append(payload)
                return SVG
            return "pinned-runtime"
        with patch("quire_mermaid.shutil.which", return_value="node"), patch.object(MermaidRenderer, "_call", worker):
            renderer = MermaidRenderer(engine, {"theme": "neutral"}, "", False, self.root / "cache")
            first = renderer.render("flowchart TD\n A-->B", 1)
            second = renderer.render("flowchart TD\n A-->B", 2)
            self.assertEqual(len(calls), 1)
            self.assertIn('quire-m1-chart', first)
            self.assertIn('quire-m2-chart', second)
            next((self.root / "cache").glob('*.svg')).write_text('broken')
            renderer.render("flowchart TD\n A-->B", 1)
            self.assertEqual(len(calls), 2)
            renderer.config = {"theme": "dark"}
            renderer.render("flowchart TD\n A-->B", 1)
            self.assertEqual(len(calls), 3)
        self.assertFalse(any(path.name.startswith('tmp') for path in (self.root / "cache").iterdir()))

    def test_missing_renderer_and_failure_are_not_cached(self):
        with patch("quire_mermaid.shutil.which", return_value=None), self.assertRaisesRegex(SystemExit, "with-mermaid"):
            MermaidRenderer(self.root, {}, "", False, self.root / "cache")
        manifest = self.root / 'node_modules/@mermaid-js/mermaid-cli/package.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{}')
        (self.root / 'mermaid-render.mjs').write_text('// test worker')
        with patch('quire_mermaid.shutil.which', return_value='node'), \
                patch.object(MermaidRenderer, '_call', return_value='runtime'):
            renderer = MermaidRenderer(self.root, {}, '', False, self.root / 'cache')
        for failure in (SystemExit('syntax error'), subprocess.TimeoutExpired('node', 120)):
            with self.subTest(failure=failure), patch.object(renderer, '_call', side_effect=failure):
                with self.assertRaises(type(failure)):
                    renderer.render('broken diagram', 1)
                self.assertFalse((self.root / 'cache').exists())
        with patch.object(renderer, '_call', return_value='<svg/>'), self.assertRaises(ValueError):
            renderer.render('broken diagram', 1)
        self.assertFalse((self.root / 'cache').exists())


MATH_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="2.5ex" height="1.9ex" '
    'role="img" focusable="false" viewBox="0 -700 1100 800" '
    'style="vertical-align: -0.2ex;"><defs><path id="MJX-1-X" d="M0 0L9 9"/>'
    '</defs><g><use data-c="X" xlink:href="#MJX-1-X" '
    'xmlns:xlink="http://www.w3.org/1999/xlink"/></g></svg>'
)


def math_node(tex, display=False):
    kind = "DisplayMath" if display else "InlineMath"
    return {"t": "Math", "c": [{"t": kind}, tex]}


class StubMath:
    """Stands in for MathRenderer: records batches, fails on chosen sources."""

    def __init__(self, failing=()):
        self.failing = set(failing)
        self.batches = []

    def render_many(self, items):
        self.batches.append(list(items))
        return [
            (None, "Undefined control sequence") if tex in self.failing
            else (MATH_SVG, None)
            for tex, _display in items
        ]


class MathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = quire.Job(
            self.root / "notes.md", self.root / "out.html", "", [], {},
            str(self.root), self.root,
        )

    def engine(self):
        manifest = self.root / "engine/math/node_modules/mathjax/package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}")
        (self.root / "engine/mathjax-render.mjs").write_text("// test worker")
        return self.root / "engine"

    def renderer(self, fingerprint="mathjax-4.1.3"):
        calls = []

        def worker(renderer, args, payload):
            if payload is None:
                return fingerprint
            calls.append(payload)
            return json.dumps(
                {"results": [{"svg": MATH_SVG} for _ in payload["items"]]}
            )

        patcher = patch.object(quire_math.MathRenderer, "_call", worker)
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch("quire_math.shutil.which", return_value="node"):
            renderer = quire_math.MathRenderer(
                self.engine(), False, self.root / "cache"
            )
        return renderer, calls

    def test_math_nodes_become_inline_svg_with_source_as_text(self):
        ast = {
            "meta": {"title": {"t": "MetaInlines", "c": [math_node("m")]}},
            "blocks": [{"t": "Para", "c": [
                {"t": "Str", "c": "a"}, math_node("a<b & c"),
                math_node("\\frac{a}{b}", True),
            ]}],
        }
        stub = StubMath()
        self.assertTrue(quire_math.render_math(ast, self.job, lambda *a: stub))
        inline, display = ast["blocks"][0]["c"][1:]
        for node in (inline, display, ast["meta"]["title"]["c"][0]):
            self.assertEqual(node["t"], "RawInline")
            self.assertEqual(node["c"][0], "html")
            self.assertIn("<svg", node["c"][1])
        self.assertIn('class="quire-math"', inline["c"][1])
        self.assertIn('class="quire-math quire-math-display"', display["c"][1])
        self.assertIn('aria-label="a&lt;b &amp; c"', inline["c"][1])
        self.assertIn("<title>a&lt;b &amp; c</title>", inline["c"][1])
        self.assertIn('width="2.5ex"', inline["c"][1])
        self.assertIn("vertical-align", inline["c"][1])
        self.assertIn("quire-x2-", inline["c"][1])
        self.assertIn('id="quire-x3-MJX-1-X"', display["c"][1])
        self.assertNotIn('id="MJX-1-X"', inline["c"][1])

    def test_one_batch_per_document_deduplicates_sources(self):
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [
            math_node("x"), math_node("y"), math_node("x"), math_node("x", True),
        ]}]}
        stub = StubMath()
        quire_math.render_math(ast, self.job, lambda *a: stub)
        self.assertEqual(len(stub.batches), 1)
        self.assertEqual(
            sorted(stub.batches[0]), [("x", False), ("x", True), ("y", False)]
        )

    def test_cache_is_keyed_by_source_display_and_renderer_version(self):
        renderer, calls = self.renderer()
        first = renderer.render_many([("x^2", False), ("x^2", True)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]["items"]), 2)
        self.assertEqual([item[1] for item in first], [None, None])
        renderer.render_many([("x^2", False), ("x^2", True)])
        self.assertEqual(len(calls), 1)
        renderer.render_many([("x^3", False), ("x^2", False)])
        self.assertEqual([c["tex"] for c in calls[1]["items"]], ["x^3"])
        # A different MathJax version must not reuse the entries.
        renderer.fingerprint = "mathjax-9.9.9"
        renderer.render_many([("x^2", False)])
        self.assertEqual(len(calls), 3)
        # A corrupt entry is rendered again.
        for path in (self.root / "cache").glob("*.svg"):
            path.write_text("broken")
        renderer.render_many([("x^2", False)])
        self.assertEqual(len(calls), 4)
        names = [path.name for path in (self.root / "cache").iterdir()]
        self.assertFalse(any(name.startswith("tmp") for name in names))

    def test_tex_errors_are_not_cached_and_nothing_is_written(self):
        renderer, calls = self.renderer()

        def failing(self_, args, payload):
            return json.dumps({"results": [{"error": "Undefined control sequence"}]})

        with patch.object(quire_math.MathRenderer, "_call", failing):
            result = renderer.render_many([("\\foo", False)])
        self.assertEqual(result, [(None, "Undefined control sequence")])
        self.assertFalse((self.root / "cache").exists())

    def test_malformed_worker_replies_raise_value_error(self):
        renderer, _ = self.renderer()
        ok = {"svg": MATH_SVG}
        for reply in (
            json.dumps({"results": []}), "not json", "[]", '"x"', "null",
            json.dumps({"results": [1]}), json.dumps({"results": [{}]}),
            json.dumps({"results": [{"svg": 3}]}),
            json.dumps({"results": [{**ok, "error": "both"}]}),
            json.dumps({"results": "x"}),
        ):
            with self.subTest(reply=reply), patch.object(
                quire_math.MathRenderer, "_call", lambda *a: reply
            ), self.assertRaises(ValueError):
                renderer.render_many([("z", False)])
        self.assertFalse((self.root / "cache").exists())
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [math_node("z")]}]}
        with patch.object(
            quire_math.MathRenderer, "_call", lambda *a: "[]"
        ), self.assertRaisesRegex(SystemExit, r"notes\.md: document contains math"):
            quire_math.render_math(ast, self.job, lambda *a: renderer)

    def test_invalid_svg_is_an_error_for_that_expression_only(self):
        renderer, _ = self.renderer()
        reply = json.dumps({"results": [{"svg": "<pre>x</pre>"}, {"svg": MATH_SVG}]})
        with patch.object(quire_math.MathRenderer, "_call", lambda *a: reply):
            first, second = renderer.render_many([("a<b", False), ("ok", False)])
        self.assertIsNone(first[0])
        self.assertIn("rejected", first[1])
        self.assertEqual(second, (MATH_SVG, None))
        self.assertEqual(len(list((self.root / "cache").glob("*.svg"))), 1)
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [math_node("a<b")]}]}
        single = json.dumps({"results": [{"svg": "<pre>x</pre>"}]})
        with patch.object(
            quire_math.MathRenderer, "_call", lambda *a: single
        ), self.assertRaises(SystemExit) as caught:
            quire_math.render_math(ast, self.job, lambda *a: renderer)
        self.assertIn(f"error: {self.root / 'notes.md'}: TeX error in `a<b`",
                      str(caught.exception))

    def test_html_without_runtime_warns_and_keeps_mathml(self):
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [math_node("x")]}]}
        self.job.format = "html"
        errors = io.StringIO()
        probed = []

        def missing(engine):
            probed.append(engine)
            return False

        factory = Mock()
        with redirect_stderr(errors):
            self.assertFalse(
                quire_math.render_math(ast, self.job, factory, available=missing)
            )
        self.assertEqual(probed, [quire_math.ENGINE_DIR])
        factory.assert_not_called()
        self.assertEqual(ast["blocks"][0]["c"][0]["t"], "Math")
        warning = errors.getvalue()
        self.assertEqual(warning.count("warning"), 1)
        for text in (str(self.root / "notes.md"), "--with-math", "--no-math"):
            self.assertIn(text, warning)
        self.job.format = "pdf"
        with patch("quire_math.ENGINE_DIR", self.root), \
                patch("quire_math.shutil.which", return_value="node"), \
                self.assertRaisesRegex(SystemExit, "with-math"):
            quire_math.render_math(ast, self.job)

    def test_title_math_becomes_plain_text_pagetitle(self):
        title = {"t": "MetaInlines", "c": [
            {"t": "Str", "c": "Energy"}, {"t": "Space"}, math_node("E=mc^2"),
            {"t": "Space"}, {"t": "Emph", "c": [{"t": "Str", "c": "now"}]},
        ]}
        tests = RenderTests()
        tests.root = self.root
        tests.source = self.root / "notes.md"
        tests.source.write_text("x\n", encoding="utf-8")
        for meta, expected in (
            ({"title": title}, "pagetitle=Energy E=mc^2 now"),
            ({"title": title, "pagetitle": {"t": "MetaString", "c": "T"}}, None),
            ({"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "Plain"}]}}, None),
        ):
            job = tests.job(format="html")
            ast = {"meta": meta, "blocks": [], "pandoc-api-version": [1, 23]}
            results = [
                subprocess.CompletedProcess([], 0, json.dumps(ast), ""),
                subprocess.CompletedProcess([], 0, "<html><head></head></html>", ""),
            ]
            with self.subTest(expected=expected), \
                    patch("quire.which_pandoc", return_value="pandoc"), \
                    patch("quire_math.MathRenderer", lambda *a: StubMath()), \
                    patch("quire_math.renderer_available", return_value=True), \
                    patch("quire.subprocess.run", side_effect=results) as run, \
                    redirect_stdout(io.StringIO()):
                quire.render(job)
            command = run.call_args_list[1].args[0]
            pagetitles = [word for word in command if word.startswith("pagetitle=")]
            self.assertEqual(pagetitles, [expected] if expected else [])

    def test_locate_prefers_the_delimited_expression(self):
        part = self.root / "part.md"
        self.job.original_markdown = "A word with the letter a in it\n"
        self.job.include_sources = [(part, "Use $a$ here\n")]
        self.assertEqual(quire_math.locate(self.job, "a"), part)
        self.job.include_sources = []
        self.assertEqual(quire_math.locate(self.job, "a"), self.job.source)

    def test_tex_error_names_source_file_and_expression(self):
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [
            math_node("a+b"), math_node("\\foo{x}"),
        ]}]}
        stub = StubMath(failing={"\\foo{x}"})
        with self.assertRaises(SystemExit) as caught:
            quire_math.render_math(ast, self.job, lambda *a: stub)
        message = str(caught.exception)
        self.assertIn(str(self.root / "notes.md"), message)
        self.assertIn("\\foo{x}", message)
        self.assertIn("Undefined control sequence", message)
        self.assertNotIn("a+b", message)

    def test_tex_error_names_the_include_that_holds_the_expression(self):
        part = self.root / "part.md"
        self.job.original_markdown = "Body $a$\n"
        self.job.include_sources = [(part, "Cover $\\bad$\n")]
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [math_node("\\bad")]}]}
        stub = StubMath(failing={"\\bad"})
        with self.assertRaisesRegex(SystemExit, "part.md"):
            quire_math.render_math(ast, self.job, lambda *a: stub)

    def test_no_math_and_no_math_nodes_do_not_construct_renderer(self):
        factory = Mock()
        self.assertFalse(quire_math.render_math({"meta": {}, "blocks": []},
                                                self.job, factory))
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [math_node("x")]}]}
        self.job.no_math = True
        self.assertFalse(quire_math.render_math(ast, self.job, factory))
        self.assertEqual(ast["blocks"][0]["c"][0]["t"], "Math")
        factory.assert_not_called()

    def test_missing_runtime_gives_installation_hint_with_file(self):
        with patch("quire_math.shutil.which", return_value="node"), \
                self.assertRaisesRegex(SystemExit, "with-math") as caught:
            quire_math.MathRenderer(self.root, False, self.root / "cache")
        self.assertIn("--no-math", str(caught.exception))
        with patch("quire_math.shutil.which", return_value=None), \
                self.assertRaisesRegex(SystemExit, "with-math"):
            quire_math.require_renderer(self.engine())
        ast = {"meta": {}, "blocks": [{"t": "Para", "c": [math_node("x")]}]}
        with patch("quire_math.ENGINE_DIR", self.root), \
                patch("quire_math.shutil.which", return_value="node"), \
                self.assertRaisesRegex(SystemExit, r"(?s)notes\.md.*with-math"):
            quire_math.render_math(ast, self.job)

    def test_render_inlines_math_and_sizes_it_with_the_text(self):
        render_tests = RenderTests()
        render_tests.root = self.root
        render_tests.source = self.root / "notes.md"
        render_tests.source.write_text("Hi $x$\n", encoding="utf-8")
        blocks = [{"t": "Para", "c": [math_node("x")]}]
        stub = StubMath()
        with patch("quire_math.MathRenderer", lambda *a: stub), \
                patch("quire_math.renderer_available", return_value=True):
            html, sent, errors, _ = render_tests.build_html(blocks)
        self.assertEqual(sent[0]["c"][0]["t"], "RawInline")
        self.assertRegex(html, r"\.quire-math-display\s*\{[^}]*display:\s*block")
        self.assertRegex(
            html, r"\.quire-math-display\s*\{[^}]*text-align:\s*center"
        )
        self.assertEqual(errors, "")
        with patch("quire_math.MathRenderer", side_effect=AssertionError):
            html, sent, errors, _ = render_tests.build_html(blocks, no_math=True)
        self.assertEqual(sent[0]["c"][0]["t"], "Math")
        self.assertNotIn("quire-math", html)

    def test_cli_flag_and_job_field(self):
        parser = quire._parser(None, "test", "quire")
        source = self.root / "notes.md"
        source.write_text("x\n", encoding="utf-8")
        self.assertFalse(parser.parse_args([str(source)]).no_math)
        self.assertTrue(parser.parse_args([str(source), "--no-math"]).no_math)
        job = quire.plan_job(str(source), profile=None, cwd=self.root, no_math=True)
        self.assertTrue(job.no_math)
        cfg = quire.UserConfig(default="", profiles={})
        for flags, expected in (([], False), (["--no-math"], True)):
            with self.subTest(flags=flags), patch("quire.render") as render:
                quire.main(["--no-config", str(source), *flags], user_config=cfg)
            self.assertEqual(render.call_args.args[0].no_math, expected)

    def test_math_installer_is_separate_and_installs_no_browser(self):
        node = subprocess.CompletedProcess([], 0, "v22.13.0\n")
        with patch("install.shutil.which", side_effect=lambda n: f"/tools/{n}"), \
                patch("install.subprocess.run", return_value=node) as run:
            install.ensure_math()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[1][1:4], ["ci", "--prefix", str(ROOT / "math")])
        self.assertFalse(any("puppeteer" in " ".join(c) for c in commands))
        with patch("install.install") as mocked:
            install.main(["--with-math", "--skip-venv", "--python",
                          str(self.root / "python"), "--bin-dir",
                          str(self.root / "bin"), "--config-dir",
                          str(self.root / "config")])
        self.assertTrue(mocked.call_args.kwargs["with_math"])
        self.assertFalse(mocked.call_args.kwargs["with_mermaid"])
        with patch("install.shutil.which", return_value=None), \
                self.assertRaisesRegex(SystemExit, "with-math requires Node"):
            install.ensure_math()


if __name__ == "__main__":
    unittest.main()
