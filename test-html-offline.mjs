// Explicit browser acceptance check, using the already installed runtime.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer';

const cache = path.join(path.dirname(fileURLToPath(import.meta.url)), '.cache');
await fs.mkdir(cache, { recursive: true });
const profile = await fs.mkdtemp(path.join(cache, 'html-browser-'));
let browser;
try {
  browser = await puppeteer.launch({ headless: true, userDataDir: profile });
  const page = await browser.newPage();
  const requests = [];
  await page.setRequestInterception(true);
  page.on('request', async request => {
    if (/^(data:|about:)/.test(request.url())) await request.continue();
    else {
      requests.push(request.url());
      await request.abort('blockedbyclient');
    }
  });
  await page.setContent(await fs.readFile(path.resolve(process.argv[2]), 'utf8'), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  const result = await page.evaluate(() => ({
    diagrams: document.querySelectorAll('.quire-diagram > svg').length,
    broken: [...document.images].filter(image => !image.complete || !image.naturalWidth).length,
    overflow: [...document.querySelectorAll('.quire-diagram')].some(diagram =>
      diagram.getBoundingClientRect().right > document.documentElement.clientWidth + 1),
  }));
  if (requests.length || result.broken || result.overflow || !result.diagrams) {
    throw new Error(JSON.stringify({ requests, ...result }));
  }
} finally {
  try {
    await browser?.close();
  } finally {
    await fs.rm(profile, { recursive: true, force: true });
  }
}
