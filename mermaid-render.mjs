// Offline Mermaid rendering using the Mermaid version installed with mermaid-cli.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import * as puppeteerModule from 'puppeteer';

const root = path.dirname(fileURLToPath(import.meta.url));
const modules = path.join(root, 'node_modules');
const cliRequire = createRequire(import.meta.resolve('@mermaid-js/mermaid-cli'));
// Use the browser bundle, as mmdc does, rather than the unbundled Node entry.
const mermaidEntry = cliRequire.resolve('mermaid/dist/mermaid.esm.min.mjs');
const puppeteer = puppeteerModule.default;

async function version(relative) {
  return JSON.parse(await fs.readFile(path.join(modules, relative, 'package.json'), 'utf8')).version;
}

async function normalizeLabels(page) {
  return page.evaluate(() => {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.querySelector('#diagram > svg');
    if (!svg) throw new Error('Mermaid did not produce SVG');
    // WeasyPrint's SVG renderer does not resolve all of Mermaid's CSS variables.
    // Freeze browser-computed paint/font values into presentation styles.
    const properties = ['fill', 'stroke', 'stroke-width', 'stroke-dasharray',
      'stroke-dashoffset', 'stroke-linecap', 'stroke-linejoin', 'fill-opacity',
      'stroke-opacity', 'opacity', 'font-family', 'font-size', 'font-weight',
      'font-style', 'text-anchor', 'dominant-baseline', 'color', 'stop-color', 'stop-opacity'];
    for (const element of [svg, ...svg.querySelectorAll('*')]) {
      if (element.namespaceURI !== ns || ['style', 'defs'].includes(element.localName)) continue;
      const computed = getComputedStyle(element);
      const resolved = properties.map(property => [property, computed.getPropertyValue(property)]);
      for (const [property, value] of resolved) {
        if (value) element.style.setProperty(property, value);
        if (value && property.startsWith('stop-')) element.setAttribute(property, value);
      }
    }
    // <small> lines were marked before Mermaid measured them, at the normal font
    // size. Freeze each line's baseline in pixels, then shrink the marked lines.
    // Leaving dy in em would resolve it against the smaller font and pull that
    // line onto the one above it. Nested word tspans stay unpositioned so they
    // inherit the line.
    const em = (value) => {
      const number = parseFloat(value);
      return Number.isFinite(number) ? number : 0;
    };
    const stripMarker = (element) => {
      let marked = false;
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const node of nodes) {
        if (!node.textContent.includes('\u200B')) continue;
        marked = true;
        node.textContent = node.textContent.replaceAll('\u200B', '');
      }
      return marked;
    };
    for (const text of svg.querySelectorAll('text')) {
      for (const row of [...text.children].filter(element =>
        element.localName === 'tspan' && element.classList.contains('text-outer-tspan'))) {
        const measured = parseFloat(row.style.fontSize) || 16;
        const baseline = (em(row.getAttribute('y')) + em(row.getAttribute('dy'))) * measured;
        const marked = stripMarker(row);
        if (marked) {
          const size = measured * 0.8;
          row.style.fontSize = `${size}px`;
          for (const inner of row.querySelectorAll('tspan')) inner.style.fontSize = `${size}px`;
        }
        row.setAttribute('y', String(baseline));
        row.setAttribute('dy', '0');
      }
    }
    for (const rect of svg.querySelectorAll('.edgeLabel rect.background')) {
      rect.style.opacity = '1';
      rect.setAttribute('opacity', '1');
    }
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    for (const foreign of [...svg.querySelectorAll('foreignObject')]) {
      const group = document.createElementNS(ns, 'g');
      const inverse = foreign.parentElement.getScreenCTM().inverse();
      const toLocal = (x, y) => new DOMPoint(x, y).matrixTransform(inverse);
      const bounds = foreign.getBoundingClientRect();
      const minimum = toLocal(bounds.left, bounds.top);
      const maximum = toLocal(bounds.right, bounds.bottom);
      // HTML backgrounds otherwise disappear with foreignObject, putting edge
      // lines through their labels. Preserve each painted HTML rectangle.
      for (const element of [foreign, ...foreign.querySelectorAll('*')]) {
        const background = getComputedStyle(element).backgroundColor;
        if (!background || background === 'transparent' || /rgba\([^)]*,\s*0\)$/.test(background)) continue;
        for (const rect of element.getClientRects()) {
          if (!rect.width || !rect.height) continue;
          const start = toLocal(rect.left, rect.top);
          const end = toLocal(rect.right, rect.bottom);
          const painted = document.createElementNS(ns, 'rect');
          painted.setAttribute('x', start.x);
          painted.setAttribute('y', start.y);
          painted.setAttribute('width', end.x - start.x);
          painted.setAttribute('height', end.y - start.y);
          painted.style.fill = background;
          painted.style.stroke = 'none';
          group.append(painted);
        }
      }
      const walker = document.createTreeWalker(foreign, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (!node.textContent.trim()) continue;
        const style = getComputedStyle(node.parentElement);
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const metrics = context.measureText('Hg');
        const ascent = metrics.fontBoundingBoxAscent ?? parseFloat(style.fontSize) * 0.8;
        const descent = metrics.fontBoundingBoxDescent ?? parseFloat(style.fontSize) * 0.2;
        const runs = [];
        for (let offset = 0; offset < node.textContent.length;) {
          const character = String.fromCodePoint(node.textContent.codePointAt(offset));
          const range = document.createRange();
          range.setStart(node, offset);
          range.setEnd(node, offset + character.length);
          offset += character.length;
          const rect = range.getBoundingClientRect();
          if (!rect.width || !rect.height) continue;
          const previous = runs.at(-1);
          if (previous && Math.abs(previous.top - rect.top) < 0.5 && Math.abs(previous.right - rect.left) < 1) {
            previous.text += character;
            previous.right = rect.right;
          } else {
            runs.push({ text: character, left: rect.left, right: rect.right, top: rect.top, height: rect.height });
          }
        }
        for (const run of runs) {
          const baseline = run.top + (run.height - ascent - descent) / 2 + ascent;
          const start = toLocal(run.left, baseline);
          const end = toLocal(run.right, baseline);
          const text = document.createElementNS(ns, 'text');
          text.setAttribute('x', start.x);
          text.setAttribute('y', start.y);
          text.setAttribute('textLength', Math.max(0.01, end.x - start.x));
          text.setAttribute('lengthAdjust', 'spacingAndGlyphs');
          text.setAttribute('xml:space', 'preserve');
          // Inline declarations beat Mermaid's diagram-wide text styles.
          text.style.fontFamily = style.fontFamily;
          text.style.fontSize = style.fontSize;
          text.style.fontWeight = style.fontWeight;
          text.style.fontStyle = style.fontStyle;
          text.style.fill = style.color;
          text.style.stroke = 'none';
          text.style.dominantBaseline = 'auto';
          text.textContent = run.text;
          group.append(text);
        }
      }
      foreign.replaceWith(group);
      if (group.children.length) {
        const box = group.getBBox();
        const width = maximum.x - minimum.x;
        const height = maximum.y - minimum.y;
        if (width <= 0 || height <= 0 || box.width <= 0 || box.height <= 0) {
          throw new Error('Label has invalid measured bounds');
        }
        // Account for Chromium's SVG/HTML baseline differences without clipping.
        const scale = Math.min(1, width / box.width, height / box.height);
        const x = minimum.x + (width - box.width * scale) / 2 - box.x * scale;
        const y = minimum.y + (height - box.height * scale) / 2 - box.y * scale;
        group.setAttribute('transform', `translate(${x},${y}) scale(${scale})`);
      }
    }
    for (const element of svg.querySelectorAll('script, foreignObject')) {
      throw new Error(`Unsupported SVG element ${element.tagName}`);
    }
    return new XMLSerializer().serializeToString(svg);
  });
}

async function main() {
  if (process.argv.includes('--fingerprint')) {
    console.log(JSON.stringify({
      cli: await version('@mermaid-js/mermaid-cli'),
      mermaid: await version('mermaid'),
      puppeteer: await version('puppeteer'),
      browser: puppeteerModule.PUPPETEER_REVISIONS,
      platform: process.platform,
      arch: process.arch,
    }));
    return;
  }
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const cache = path.join(root, '.cache');
  await fs.mkdir(cache, { recursive: true });
  const profile = await fs.mkdtemp(path.join(cache, 'mermaid-browser-'));
  let browser;
  try {
    browser = await puppeteer.launch({ headless: true, userDataDir: profile });
    const page = await browser.newPage();
    await page.setViewport({ width: 1600, height: 1200, deviceScaleFactor: 1 });
    await page.setRequestInterception(true);
    const blocked = [];
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    // Synthetic URLs are fulfilled from local files. No HTTP connection occurs.
    page.on('request', async request => {
      try {
        const url = new URL(request.url());
        if (url.protocol === 'data:' || url.protocol === 'about:') {
          await request.continue();
        } else if (url.origin === 'https://quire.invalid' && url.pathname === '/') {
          const entry = path.relative(modules, mermaidEntry).split(path.sep).join('/');
          await request.respond({ contentType: 'text/html', body:
            `<!doctype html><html><head><link rel="icon" href="data:,"></head><body><div id="diagram"></div><script type="module">` +
            `import mermaid from '/modules/${entry}'; window.quireMermaid = mermaid;</script></body></html>` });
        } else if (url.origin === 'https://quire.invalid' && url.pathname.startsWith('/modules/')) {
          const local = path.resolve(modules, decodeURIComponent(url.pathname.slice('/modules/'.length)));
          if (!local.startsWith(modules + path.sep) || !/\.m?js$/.test(local)) {
            throw new Error('Invalid local module request');
          }
          await request.respond({ contentType: 'text/javascript', body: await fs.readFile(local) });
        } else {
          blocked.push(request.url());
          await request.abort('blockedbyclient');
        }
      } catch (error) {
        blocked.push(String(error));
        if (!request.isInterceptResolutionHandled()) await request.abort('failed');
      }
    });
    await page.goto('https://quire.invalid/', { waitUntil: 'load' });
    try {
      await page.waitForFunction(() => !!window.quireMermaid, { timeout: 10000 });
    } catch (error) {
      throw new Error(pageErrors.join('\n') || blocked.join('\n') || error.message);
    }
    if (input.fontCSS.trim()) await page.addStyleTag({ content: input.fontCSS });
    await page.evaluate(async ({ source, config, seed }) => {
      const mermaid = window.quireMermaid;
      const prepared = source.replace(/<small>([\s\S]*?)<\/small>/gi, (_, inner) => inner
        .split(/<br\s*\/?>/i)
        .map(line => `\u200B${line}`)
        .join('<br/>'));
      mermaid.initialize({
        ...config,
        htmlLabels: false,
        flowchart: { ...(config.flowchart || {}), wrappingWidth: 100000 },
        securityLevel: 'strict',
        deterministicIds: true,
        deterministicIDSeed: seed,
        secure: ['secure', 'securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges',
          'deterministicIds', 'deterministicIDSeed', 'theme', 'fontFamily', 'themeVariables',
          'htmlLabels', 'flowchart'],
        startOnLoad: false,
      });
      const family = config.themeVariables?.fontFamily || config.fontFamily || 'sans-serif';
      await document.fonts.load(`16px ${family}`, prepared);
      await document.fonts.ready;
      const { svg } = await mermaid.render(`diagram-${seed.slice(0, 16)}`, prepared);
      document.querySelector('#diagram').innerHTML = svg;
      await document.fonts.ready;
    }, input);
    if (blocked.length) throw new Error(`Offline diagram cannot load: ${blocked.join(', ')}`);
    const svg = await normalizeLabels(page);
    if (blocked.length) throw new Error(`Offline diagram cannot load: ${blocked.join(', ')}`);
    process.stdout.write(svg);
  } finally {
    try {
      await browser?.close();
    } finally {
      await fs.rm(profile, { recursive: true, force: true });
    }
  }
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
