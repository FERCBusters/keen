// The design system's theme boot script is a classic script that sets the saved
// theme on <html> before the first paint; the page modules run later. Each page
// must load it in <head>, ahead of its stylesheets and scripts, without defer or
// async, or the page shows the default palette first.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';

const publicRoot = new URL('../public/', import.meta.url);
const BOOT = '/vendor/mosp-design-system/theme-boot.js';

test('every page applies the saved theme before its stylesheets and scripts', async () => {
  const pages = (await readdir(publicRoot)).filter((name) => name.endsWith('.html')).sort();
  assert.ok(pages.length >= 40, `expected the application pages, found ${pages.length}`);
  for (const name of pages) {
    const html = await readFile(new URL(name, publicRoot), 'utf8');
    const head = html.match(/<head\b[^>]*>([\s\S]*?)<\/head>/i);
    assert.ok(head, `${name} has a <head>`);
    const tags = [...head[1].matchAll(/<(script|link)\b([^>]*)>/gi)];
    const boot = tags.findIndex(([, , attrs]) => attrs.includes(`src="${BOOT}"`));
    assert.equal(boot, 0, `${name} loads ${BOOT} before any other script or stylesheet`);
    assert.equal(tags[0][1].toLowerCase(), 'script');
    assert.doesNotMatch(tags[0][2], /\b(defer|async|type)\b/, `${name} runs it as it parses`);
    assert.equal(html.split(BOOT).length, 2, `${name} loads it once`);
  }
});

test('the vendor step copies the theme boot script beside the design system bundle', async () => {
  const script = await readFile(new URL('../scripts/vendor-assets.mjs', import.meta.url), 'utf8');
  assert.match(
    script,
    /nm\('@fercbusters', 'mosp-design-system', 'dist', 'theme-boot\.js'\),\s*out\('mosp-design-system', 'theme-boot\.js'\)/,
  );
});
