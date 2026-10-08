"""Tool-independent tests for the shared pipeline and offline resource handling."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import install
import quire
from quire_html import Resources
from quire_mermaid import MermaidRenderer, annotated_fences, fence_locations, prefix_svg, render_blocks, validate_svg
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


if __name__ == "__main__":
    unittest.main()
