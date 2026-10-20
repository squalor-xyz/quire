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
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import quire
import quire_math
from quire_common import validate_svg
from quire_mermaid import MermaidRenderer

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
        svgs = []
        original = MermaidRenderer._call
        def recorded(renderer, args, payload):
            result = original(renderer, args, payload)
            if payload is not None:
                calls.append(payload)
                svgs.append(result)
            return result
        with patch.object(MermaidRenderer, "_call", recorded):
            html, _ = self.build(format="html")
            self.assertEqual(len(calls), 13)
            self.assert_label_geometry(svgs)
            calls.clear()
            pdf, verbose = self.build(format="pdf", verbose=True)
            self.assertEqual(calls, [])
            self.assertEqual(verbose.count('cache hit'), 13)
        content = html.output.read_text()
        self.assertEqual(content.count('data-quire-diagram="'), 13)
        self.assertNotIn('class="mermaid"', content)
        self.assertNotIn('foreignObject', content)
        self.assertNotIn('textLength', content)
        self.assertNotIn('&lt;small&gt;', content)
        self.assertIn('text-anchor: middle', content)
        self.assertIn('font-size: 12.8px', content)
        self.assertNotIn('\u200b', content)
        text = self.pdf_text(pdf.output)
        for label in ("First line", "Second line", "Cylinder", "PERSON", "DOCUMENT", "Reply",
                      "SchemaResolver", "ImportAsync(path)", "measurements"):
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

    def font_size(self, element):
        match = re.search(r"font-size:\s*([0-9.]+)px", element.get("style") or "")
        return float(match.group(1)) if match else None

    def assert_label_geometry(self, diagrams):
        svg_ns = "{http://www.w3.org/2000/svg}"
        joined = "\n".join(diagrams)
        self.assertIn(">SchemaResolver<", joined)
        self.assertIn(">ImportAsync(path)<", joined)
        self.assertNotIn("\u200b", joined)
        self.assertEqual(len(diagrams), 13)
        saw_small_gap = False
        saw_edge = False
        for diagram in diagrams:
            root = ET.fromstring(diagram)
            for text in root.iter(svg_ns + "text"):
                rows = [child for child in list(text)
                        if child.tag == svg_ns + "tspan"
                        and "text-outer-tspan" in (child.get("class") or "")]
                for previous, row in zip(rows, rows[1:]):
                    previous_y = float(previous.get("y"))
                    y = float(row.get("y"))
                    size = self.font_size(row)
                    previous_size = self.font_size(previous)
                    self.assertIsNotNone(size)
                    self.assertIsNotNone(previous_size)
                    # The next em-box starts at or below the previous baseline.
                    self.assertGreaterEqual(y - size, previous_y - 0.05)
                    self.assertGreaterEqual(
                        y - previous_y + 0.05, max(previous_size, size) * 1.1)
                    if abs(size - 12.8) < 0.01 or abs(previous_size - 12.8) < 0.01:
                        saw_small_gap = True
            for group in root.iter(svg_ns + "g"):
                if "edgeLabel" not in (group.get("class") or "").split():
                    continue
                saw_edge = True
                seen_text = False
                for element in group.iter():
                    if element.tag == svg_ns + "text":
                        seen_text = True
                    if (element.tag == svg_ns + "rect"
                            and "background" in (element.get("class") or "").split()):
                        self.assertFalse(seen_text)
                        self.assertRegex(element.get("style") or "", r"(?:^|;)\s*opacity:\s*1(?:\s|;|$)")
                        self.assertEqual(element.get("opacity"), "1")
        self.assertTrue(saw_small_gap)
        self.assertTrue(saw_edge)

    def test_table_row_stays_on_one_page(self):
        # Enough paragraphs that the tall row starts near the bottom of a page.
        # Without the row rule, ROWSTART and ROWEND land on different pages.
        filler = "\n\n".join(f"Filler paragraph {index} sits above the table." for index in range(1, 22))
        detail = "<br>".join(
            ["ROWSTART", *(f"middle line {index} of the measurements cell" for index in range(1, 12)), "ROWEND"])
        self.source.write_text(
            filler + "\n\n| Name | Detail |\n| --- | --- |\n"
            f"| measurements | {detail} |\n| after | trailer |\n",
            encoding="utf-8")
        pdf, _ = self.build(format="pdf")
        pages = self.pdf_text(pdf.output).split("\f")
        start = [index for index, page in enumerate(pages) if "ROWSTART" in page]
        end = [index for index, page in enumerate(pages) if "ROWEND" in page]
        header = [index for index, page in enumerate(pages) if "Name" in page and "Detail" in page]
        self.assertEqual(start, end)
        self.assertEqual(header, start)

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


class MathIntegrationTests(unittest.TestCase):
    """TeX math through the real MathJax runtime; no browser is involved."""

    @classmethod
    def setUpClass(cls):
        needed = ("pandoc", "node", "pdftotext", "pdftoppm")
        missing = [tool for tool in needed if not shutil.which(tool)]
        if not quire_math.renderer_available(ROOT):
            missing.append("local MathJax runtime")
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
        self.source = self.root / "equations.md"
        self.source.write_text(
            "# Equations\n\n"
            "The ratio $\\Gamma = \\frac{a_1^2}{b}$ and $x_i$ sit in a line.\n\n"
            "$$EVM = \\sqrt{\\frac{\\sum_k |e_k|^2}{\\sum_k |s_k|^2}}$$\n\n"
            "Same again: $x_i$. Price is $5 and $6.\n\n"
            "Markup characters: $a < b$, $\\text{<}$ and $a<b>c$.\n",
            encoding="utf-8",
        )

    def build(self, **kwargs):
        job = quire.plan_job(str(self.source), profile=None, cwd=self.root, **kwargs)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            quire.render(job)
        return job

    def ink_rows(self, pdf):
        """Page 1 as PGM: ink bounding boxes of the text and equation bands."""
        base = self.root / "page"
        subprocess.run(
            ["pdftoppm", "-r", "72", "-gray", "-f", "1", "-l", "1",
             str(pdf), str(base)],
            check=True, capture_output=True,
        )
        data = next(self.root.glob("page-*.pgm")).read_bytes()
        header = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", data)
        width, height = int(header[1]), int(header[2])
        pixels = data[header.end():]
        bands, current = [], None
        for row in range(height):
            line = pixels[row * width:(row + 1) * width]
            dark = [x for x, value in enumerate(line) if value < 128]
            if dark and current is None:
                current = [row, min(dark), max(dark)]
            elif dark:
                current[1] = min(current[1], dark[0])
                current[2] = max(current[2], dark[-1])
            elif current is not None:
                bands.append((current[0], row, current[1], current[2]))
                current = None
        return width, bands

    def test_inline_and_display_math_render_in_pdf_and_html(self):
        html = self.build(format="html")
        content = html.output.read_text()
        self.assertEqual(content.count('class="quire-math"'), 6)
        self.assertEqual(content.count('class="quire-math quire-math-display"'), 1)
        self.assertNotIn("<math", content)
        self.assertNotIn("data-latex", content)
        for source in ("a &lt; b", "\\text{&lt;}", "a&lt;b&gt;c"):
            self.assertIn(f'aria-label="{source}"', content)
        ET.fromstring(  # every SVG is well-formed XML
            "<root>" + "".join(re.findall(r"<svg.*?</svg>", content, re.S)) + "</root>"
        )
        self.assertIn('aria-label="x_i"', content)
        self.assertIn("<title>\\Gamma = \\frac{a_1^2}{b}</title>", content)
        self.assertNotIn("foreignObject", content)
        pdf = self.build(format="pdf")
        text = self.pdf_text(pdf.output)
        self.assertNotIn("\\frac", text)
        self.assertIn("Price is $5 and $6.", text)
        width, bands = self.ink_rows(pdf.output)
        equation = max(bands, key=lambda band: band[1] - band[0])
        centre = (equation[2] + equation[3]) / 2
        self.assertLess(abs(centre - width / 2), 8)

    def pdf_text(self, path):
        return subprocess.check_output(
            ["pdftotext", "-layout", str(path), "-"], text=True
        )

    def test_second_build_uses_the_cache_and_one_worker_run(self):
        calls = []
        original = quire_math.MathRenderer._call

        def recorded(renderer, args, payload):
            if payload is not None:
                calls.append(payload)
            return original(renderer, args, payload)

        with patch.object(quire_math.MathRenderer, "_call", recorded):
            self.build(format="html")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(calls[0]["items"]), 6)
            self.build(format="html")
            self.assertEqual(len(calls), 1)

    def test_tex_error_names_file_and_expression_and_keeps_output(self):
        self.source.write_text("# Bad\n\nHello $\\foo{x}$ there.\n", encoding="utf-8")
        output = self.root / "equations.html"
        output.write_text("previous", encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            self.build(format="html")
        self.assertIn(str(self.source), str(caught.exception))
        self.assertIn("\\foo{x}", str(caught.exception))
        self.assertEqual(output.read_text(), "previous")

    def write(self, text):
        self.source.write_text(text, encoding="utf-8")

    def test_pandoc_macros_work_but_math_internal_definitions_fail(self):
        self.write("\\newcommand{\\R}{\\mathbb{R}}\n\nIn $\\R^n$.\n")
        content = self.build(format="html").output.read_text()
        self.assertIn('aria-label="\\mathbb{R}^n"', content)
        for tex in ("\\def\\q{1}\\q", "\\newcommand{\\q}{1}\\q"):
            with self.subTest(tex=tex):
                self.write(f"Bad ${tex}$\n")
                with self.assertRaises(SystemExit) as caught:
                    self.build(format="html")
                message = str(caught.exception)
                self.assertIn(f"TeX error in `{tex}`", message)
                self.assertIn("Undefined control sequence", message)
        # A definition can no longer leak into a later expression.
        self.write("A $\\def\\q{1}$ and $\\q$.\n")
        with self.assertRaises(SystemExit) as caught:
            self.build(format="html")
        self.assertEqual(str(caught.exception).count("TeX error"), 2)

    def test_autoloaded_definitions_do_not_leak_between_expressions(self):
        pairs = (
            ("\\Newextarrow{\\xx}{5,5}{0x2192}\\xx{a}", "\\xx{a}"),
            ("\\definecolor{mine}{rgb}{1,0,0}\\color{mine}x", "\\color{mine}x"),
            ("\\newcolumntype{Q}{c}\\begin{array}{Q}1\\end{array}",
             "\\begin{array}{Q}1\\end{array}"),
        )
        for number, (define, use) in enumerate(pairs):
            with self.subTest(define=define):
                alone = quire_math.MathRenderer(
                    ROOT, False, self.root / f"alone{number}"
                ).render_many([(use, False)])
                batch = quire_math.MathRenderer(
                    ROOT, False, self.root / f"batch{number}"
                ).render_many([(define, False), (use, False)])
                self.assertEqual(batch[1], alone[0])
                if number == 0:  # alone it is an error, so a leak would show
                    self.assertIsNone(alone[0][0])

    def test_repeated_labels_do_not_collide_across_expressions(self):
        self.write("$$\\label{a} x$$\n\n$$\\label{a} y$$\n")
        content = self.build(format="html").output.read_text()
        self.assertEqual(content.count('class="quire-math quire-math-display"'), 2)

    def test_title_math_stays_in_the_page_title(self):
        self.write("---\ntitle: Energy $E=mc^2$\n---\n\nText\n")
        content = self.build(format="html").output.read_text()
        self.assertIn("<title>Energy E=mc^2</title>", content)

    def test_html_without_runtime_keeps_mathml_with_one_warning(self):
        job = quire.plan_job(
            str(self.source), profile=None, cwd=self.root, format="html"
        )
        errors = io.StringIO()
        with patch("quire_math.ENGINE_DIR", self.root), \
                redirect_stdout(io.StringIO()), redirect_stderr(errors):
            quire.render(job)
        content = job.output.read_text()
        self.assertIn("<math", content)
        self.assertNotIn("quire-math", content)
        self.assertEqual(errors.getvalue().count("warning:"), 1)
        self.assertIn("--with-math", errors.getvalue())
        pdf = quire.plan_job(
            str(self.source), profile=None, cwd=self.root, format="pdf"
        )
        with patch("quire_math.ENGINE_DIR", self.root), \
                self.assertRaisesRegex(SystemExit, "with-math"):
            quire.render(pdf)

    def test_fingerprint_covers_mathjax_and_its_font(self):
        renderer = quire_math.MathRenderer(ROOT, False, self.root / "cache")
        self.assertRegex(renderer.fingerprint, r"^mathjax-[\d.]+\+newcm-font-[\d.]+$")

    def test_long_inline_math_stays_one_svg(self):
        terms = " + ".join(f"a_{{{n}}}" for n in range(1, 40))
        self.source.write_text(f"Long ${terms}$ end.\n", encoding="utf-8")
        content = self.build(format="html").output.read_text()
        self.assertEqual(content.count("<svg"), 1)

    def test_no_math_keeps_pandoc_output(self):
        with patch("quire_math.MathRenderer", side_effect=AssertionError("unexpected")):
            html = self.build(format="html", no_math=True)
        content = html.output.read_text()
        self.assertIn("<math", content)
        self.assertNotIn("quire-math", content)

    def test_missing_node_gives_installation_hint(self):
        lookup = shutil.which
        hide_node = lambda name: None if name == "node" else lookup(name)  # noqa: E731
        with patch("quire_math.shutil.which", side_effect=hide_node), \
                self.assertRaisesRegex(SystemExit, "with-math"):
            self.build(format="pdf")


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
