// Offline TeX to SVG using the MathJax version installed under math/.
// No browser or network. Reads {"items": [{"tex", "display"}]} on stdin and
// writes {"results": [{"svg"} | {"error"}]} on stdout, one entry per item.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const runtime = path.join(root, 'math');
const runtimeRequire = createRequire(path.join(runtime, 'package.json'));

async function version() {
  const manifest = runtimeRequire.resolve('mathjax/package.json');
  return JSON.parse(await fs.readFile(manifest, 'utf8')).version;
}

async function fontVersion() {
  const manifest = path.join(runtime, 'node_modules/@mathjax/mathjax-newcm-font/package.json');
  return JSON.parse(await fs.readFile(manifest, 'utf8')).version;
}

async function readInput() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

// Commands that define macros. A definition would leak into the later
// expressions of the run while results are cached per expression, so they are
// removed and fail like any undefined control sequence.
const DEFINERS = {
  'Newcommand-macros': ['newcommand', 'renewcommand', 'newenvironment',
    'renewenvironment', 'def', 'let'],
  'AMSmath-macros': ['DeclareMathOperator'],
};

async function start() {
  const entry = pathToFileURL(runtimeRequire.resolve('mathjax/node-main.mjs'));
  const { init } = await import(entry);
  const MathJax = await init({
    loader: { load: ['input/tex', 'output/svg', 'adaptors/liteDOM'] },
    tex: {
      // Undefined macros must fail rather than render as red source text.
      // Removing newcommand alone does not remove \def and friends (see
      // DEFINERS); \require is dropped so nothing is loaded at run time.
      packages: { '[-]': ['noundefined', 'newcommand', 'configmacros', 'require'] },
      formatError: (_jax, error) => { throw error; },
    },
    svg: { fontCache: 'local', linebreaks: { inline: false } },
  });
  const { MapHandler } = MathJax._.input.tex.MapHandler;
  for (const [mapName, names] of Object.entries(DEFINERS)) {
    const map = MapHandler.getMap(mapName);
    if (!map) throw new Error(`MathJax map ${mapName} not found`);
    for (const name of names) map.remove(name);
  }
  return MathJax;
}

// MathJax writes the TeX source into data-latex attributes without escaping
// markup characters, which makes the SVG ill-formed XML. Drop those attributes,
// and any other attribute value that could still carry markup.
function scrub(adaptor, node) {
  for (const { name, value } of adaptor.allAttributes(node)) {
    if (name.startsWith('data-latex') || /[<>]/.test(value)) adaptor.removeAttribute(node, name);
  }
  for (const child of adaptor.childNodes(node)) {
    if (adaptor.kind(child) !== '#text') scrub(adaptor, child);
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args[0] === '--fingerprint') {
    process.stdout.write(`mathjax-${await version()}+newcm-font-${await fontVersion()}\n`);
    return;
  }
  const { items } = await readInput();
  const MathJax = await start();
  const results = [];
  for (const { tex, display } of items) {
    try {
      // A fresh input jax and document per expression: nothing defined by one
      // (including by autoloaded packages) can reach the next.
      MathJax.startup.getComponents();
      const node = await MathJax.tex2svgPromise(tex, { display: Boolean(display) });
      const adaptor = MathJax.startup.adaptor;
      scrub(adaptor, node);
      results.push({ svg: adaptor.innerHTML(node) });
    } catch (error) {
      const message = String(error && error.message ? error.message : error);
      results.push({ error: message.split('\n')[0] });
    }
  }
  process.stdout.write(JSON.stringify({ results }));
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(1);
});
