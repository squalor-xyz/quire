"""Explicit real-tool acceptance checks; run separately from test_quire.py."""

from __future__ import annotations

import io
import base64
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import quire
from quire_mermaid import MermaidRenderer, validate_svg

ROOT = Path(__file__).parent.resolve()


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in ("pandoc", "node", "pdftotext", "pdftoppm") if not shutil.which(tool)]
        if not (ROOT / "node_modules/@mermaid-js/mermaid-cli/package.json").is_file():
            missing.append("local Mermaid runtime")
        if not (ROOT / ".venv/bin/weasyprint").is_file():
            missing.append("local WeasyPrint")
        if missing:
            raise unittest.SkipTest("missing acceptance tools: " + ", ".join(missing))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        cache = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.root / "cache")})
        cache.start()
        self.addCleanup(cache.stop)
        self.source = self.root / "examples.md"
        self.source.write_text((ROOT / "tests/fixtures/diagrams.md").read_text(), encoding="utf-8")

    def build(self, target=None, **kwargs):
        output, errors = io.StringIO(), io.StringIO()
        profile = kwargs.pop("profile", None)
        job = quire.plan_job(str(target or self.source), profile=profile, cwd=self.root, **kwargs)
        with redirect_stdout(output), redirect_stderr(errors):
            quire.render(job)
        self.assertEqual(output.getvalue(), f"wrote {job.output}\n")
        if not job.verbose:
            self.assertEqual(errors.getvalue(), "")
        return job, errors.getvalue()

    def pdf_text(self, path):
        return subprocess.check_output(["pdftotext", "-layout", str(path), "-"], text=True)

    def test_fixture_html_pdf_cache_and_labels(self):
        calls = []
        original = MermaidRenderer._call
        def recorded(renderer, args, payload):
            if payload is not None:
                calls.append(payload)
            return original(renderer, args, payload)
        with patch.object(MermaidRenderer, "_call", recorded):
            html, _ = self.build(format="html")
            self.assertEqual(len(calls), 12)
            calls.clear()
            pdf, verbose = self.build(format="pdf", verbose=True)
            self.assertEqual(calls, [])
            self.assertEqual(verbose.count('cache hit'), 12)
        content = html.output.read_text()
        self.assertEqual(content.count('data-quire-diagram="'), 12)
        self.assertNotIn('class="mermaid"', content)
        self.assertNotIn('foreignObject', content)
        self.assertNotIn('textLength', content)
        self.assertNotIn('&lt;small&gt;', content)
        self.assertIn('text-anchor: middle', content)
        self.assertIn('font-size: 12.8px', content)
        text = self.pdf_text(pdf.output)
        for label in ("First line", "Second line", "Cylinder", "PERSON", "DOCUMENT", "Reply"):
            self.assertIn(label, text)
        self.assertIn("Small detail", re.sub(r"\s+", " ", text))
        pages = text.split('\f')
        self.assertTrue(any('Example 12' in page and 'Cylinder' in page for page in pages))
        self.assertNotIn("flowchart", text)
        self.assertNotIn("erDiagram", text)
        subprocess.run(["node", str(ROOT / "test-html-offline.mjs"), str(html.output)], check=True,
                       cwd=ROOT, env={**os.environ, "PUPPETEER_CACHE_DIR": str(ROOT / ".cache/puppeteer")},
                       capture_output=True, text=True)
        subprocess.run(["pdftoppm", "-scale-to", "1000", "-png", str(pdf.output), str(self.root / "page")],
                       check=True, capture_output=True)
        self.assertTrue(list(self.root.glob('page-*.png')))
        if os.environ.get('QUIRE_RENDER_ARTIFACTS'):
            destination = Path(os.environ['QUIRE_RENDER_ARTIFACTS']).resolve()
            self.assertIn(ROOT, destination.parents)
            destination.mkdir(parents=True, exist_ok=True)
            for artifact in (html.output, pdf.output, *self.root.glob('page-*.png')):
                shutil.copyfile(artifact, destination / artifact.name)

    def test_tall_diagram_stays_with_its_heading(self):
        nodes = '\n'.join(f'  N{i} --> N{i + 1}' for i in range(1, 18))
        self.source.write_text(
            '## Tall layout\n\nOne short paragraph.\n\n```mermaid\nflowchart TD\n'
            + nodes + '\n```\n', encoding='utf-8')
        pdf, _ = self.build()
        pages = [page for page in self.pdf_text(pdf.output).split('\f') if page.strip()]
        self.assertEqual(len(pages), 1)
        self.assertIn('Tall layout', pages[0])
        self.assertIn('N1', pages[0])
        self.assertIn('N18', pages[0])

    def test_font_change_rerenders(self):
        cache = self.root / 'font-cache'
        source = 'flowchart LR\n A[One]-->B[Two]'
        first = MermaidRenderer(ROOT, {'theme': 'neutral', 'fontFamily': 'Helvetica'}, '', True, cache)
        second = MermaidRenderer(ROOT, {'theme': 'neutral', 'fontFamily': 'Times'}, '', True, cache)
        with redirect_stderr(io.StringIO()) as original:
            first.render(source, 1)
        with redirect_stderr(io.StringIO()) as changed:
            second.render(source, 1)
        self.assertIn('rendering', original.getvalue())
        self.assertIn('rendering', changed.getvalue())

    def test_custom_pages_and_all_variants(self):
        (self.root / "page.css").write_text(
            '@page {size: A4; margin:2cm} body{font-family:sans-serif;font-size:11pt}'
            '.confidential-banner{position:running(confidential)}'
            '@page {@top-center{content:element(confidential)}}', encoding="utf-8",
        )
        (self.root / "notice.html").write_text('<div class="confidential-banner">Confidential</div>')
        (self.root / "header.html").write_text('<div class="generic-header">Example letterhead</div>')
        tables = ['[mermaid]', 'theme="base"', 'font_family="sans-serif"',
                  'theme_variables={primaryColor="#dddddd"}']
        for variant in quire.VARIANT_NAMES.values():
            tables.extend([f'[variants.{variant}]', 'css=["page.css"]'])
            includes = []
            if 'letterhead' in variant:
                includes.append('{file="header.html"}')
            if 'confidential' in variant:
                includes.append('{file="notice.html"}')
            tables.append('includes=[' + ','.join(includes) + ']')
        (self.root / "profile.toml").write_text('\n'.join(tables))
        profile = quire.load_profile(self.root)
        # One source covers page-height fitting without repeating the full fixture.
        self.source.write_text('```mermaid\nflowchart TD\n' +
                               '\n'.join(f'N{i}-->N{i+1}' for i in range(20)) + '\n```\n')
        for css in ('@page {size:letter;margin:0.75in}', '@page {size:A4;margin:2cm}',
                    '@page {size:5in 6in;margin:0.6in}'):
            (self.root / 'page.css').write_text(css + 'body{font-family:sans-serif}')
            for flags in ({}, {"letterhead": True}, {"confidential": True},
                          {"letterhead": True, "confidential": True}):
                with self.subTest(css=css, flags=flags):
                    self.build(profile=profile, **flags)
                    html, _ = self.build(profile=profile, format='html', **flags)
                    self.assertIn('data-quire-diagram=', html.output.read_text())

    def test_broken_block_has_original_line_and_preserves_output(self):
        self.source.write_text('---\ntitle: Broken\n---\n\n<pre>\n```mermaid\nflowchart TD\n A[broken\n```\n</pre>\n\n'
                               '```mermaid\nflowchart TD\n A[broken\n```\n')
        output = self.root / "examples.html"
        output.write_text("previous result")
        with self.assertRaisesRegex(SystemExit, r'examples.md: Mermaid block 1, line 12:') as caught:
            self.build(format='html', confidential=True, output=output)
        self.assertIn('Parse error', str(caught.exception))
        self.assertEqual(output.read_text(), "previous result")

    def test_embedded_fonts_images_imports_and_duplicate_diagrams(self):
        styles = self.root / 'styles'
        styles.mkdir()
        font = ROOT / 'node_modules/@fontsource/open-sans/files/open-sans-latin-400-normal.woff2'
        self.assertTrue(font.is_file())
        shutil.copyfile(font, styles / 'font.woff2')
        (styles / 'fonts.css').write_text('@font-face{font-family:FixtureFont;src:url(font.woff2)}')
        (styles / 'document.css').write_text('@import "fonts.css"; @page{size:letter;margin:0.75in}'
                                            'body{font-family:FixtureFont,sans-serif}')
        (self.root / 'pixel.png').write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        (self.root / 'profile.toml').write_text('[mermaid]\nfont_family="FixtureFont"\n'
                                               '[variants.default]\ncss=["styles/document.css"]\n')
        source = '```mermaid\nflowchart LR\n A[Embedded font]-->B[Result]\n```\n'
        self.source.write_text('![Pixel](pixel.png)\n\n' + source + '\n' + source)
        profile = quire.load_profile(self.root)
        html, _ = self.build(profile=profile, format='html')
        contents = html.output.read_text()
        identifiers = re.findall(r'(?<![\w-])id="([^"]+)"', contents)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertIn('data:image/png;base64,', contents)
        self.assertIn('data:text/css;base64,', contents)
        subprocess.run(['node', str(ROOT / 'test-html-offline.mjs'), str(html.output)],
                       check=True, capture_output=True, env={**os.environ,
                       'PUPPETEER_CACHE_DIR': str(ROOT / '.cache/puppeteer')})
        pdf, _ = self.build(profile=profile)
        self.assertEqual(self.pdf_text(pdf.output).count('Embedded font'), 2)

    def test_public_profile_selection_in_both_formats(self):
        (self.root / 'profile.toml').write_text('[variants.default]\n')
        self.source.write_text('```mermaid\nflowchart LR\n A[Selection]-->B[Result]\n```\n')
        config = quire.UserConfig(default='example', profiles={'example': self.root})
        with patch.dict(os.environ, {'QUIRE_CONFIG': '', 'QUIRE_PROFILE': ''}):
            for selection in (['--profile', str(self.root)], ['--config', 'example'], ['--no-config']):
                for format in ('html', 'pdf'):
                    output = self.root / f'selected.{format}'
                    with self.subTest(selection=selection, format=format), redirect_stdout(io.StringIO()):
                        quire.main([*selection, str(self.source), '--format', format, '-o', str(output)],
                                   user_config=config)
                    self.assertTrue(output.is_file())
                    if format == 'html':
                        self.assertIn('data-quire-diagram=', output.read_text())

    def test_no_mermaid_and_plain_text_regression(self):
        with patch('quire_mermaid.MermaidRenderer._call', side_effect=AssertionError('unexpected renderer')):
            html, _ = self.build(format='html', no_mermaid=True)
            self.assertRegex(html.output.read_text(), r'<pre[^>]*class="[^"]*\bmermaid\b')
            self.source.write_text('# Notes\n\nHello **world**.\n\n- One\n- Two\n')
            pdf, _ = self.build()
        baseline = self.root / 'baseline.pdf'
        subprocess.run(['pandoc', str(self.source), '-f', 'markdown', '-t', 'html5',
                        '-o', str(baseline), f'--pdf-engine={ROOT / ".venv/bin/weasyprint"}',
                        '--standalone', f'--css={ROOT / "plain.css"}', '--metadata', 'lang=en'],
                       env=quire.pandoc_env(str(ROOT / '.venv/bin/weasyprint')),
                       capture_output=True, check=True)
        self.assertEqual(self.pdf_text(pdf.output), self.pdf_text(baseline))

    def test_deterministic_svg_and_missing_tool_path(self):
        renderer = MermaidRenderer(ROOT, {"theme":"neutral", "fontFamily":"sans-serif"}, '', False,
                                   self.root / 'svg-cache')
        first = renderer.render('flowchart LR\n A[First]-->B[Second]', 1)
        for file in (self.root / 'svg-cache').glob('*.svg'):
            file.unlink()
        self.assertEqual(first, renderer.render('flowchart LR\n A[First]-->B[Second]', 1))
        validate_svg(first)
        lookup = shutil.which
        with patch('quire_mermaid.shutil.which', side_effect=lambda name: None if name == 'node' else lookup(name)), \
                self.assertRaisesRegex(SystemExit, 'with-mermaid'):
            self.build(format='html')


class WeasyPrintAPITests(unittest.TestCase):
    def test_pdf_fitter_page_box_api(self):
        python = ROOT / ".venv/bin/python"
        if not python.is_file():
            self.skipTest("local WeasyPrint")
        probe = """
from weasyprint import HTML
import weasyprint
parts = []
for part in weasyprint.__version__.split("."):
    digits = ""
    for char in part:
        if char.isdigit():
            digits += char
        else:
            break
    if not digits:
        break
    parts.append(int(digits))
version = tuple(parts)
if version < (62,) or version >= (71,):
    raise SystemExit(f"WeasyPrint {weasyprint.__version__} is outside >=62,<71")
document = HTML(string="<p>x</p>").render()
box = document.pages[0]._page_box
needed = (
    "descendants", "width", "height", "margin_top", "margin_bottom",
    "padding_top", "padding_bottom", "border_top_width", "border_bottom_width",
    "content_box_x", "content_box_y",
)
missing = [name for name in needed if not hasattr(box, name)]
if missing:
    raise SystemExit("missing page-box attributes: " + ", ".join(missing))
if not callable(box.content_box_x) or not callable(box.content_box_y):
    raise SystemExit("content_box methods are missing")
"""
        result = subprocess.run([str(python), "-c", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()
