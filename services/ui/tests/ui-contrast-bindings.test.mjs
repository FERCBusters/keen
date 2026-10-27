// Compiled-Bootstrap colour bindings in the app stylesheet, and live-region
// roles on the toast-populated status strips. The colour values here are the
// ones verified for WCAG AA contrast (see the comment block near the top of
// public/styles.css): white text on the compiled danger red #dc3545 measures
// 4.53:1, below the 4.5:1 small status text needs; the bound red #cd2a3a
// measures 5.27:1. The raw .text-danger and .text-secondary utilities fall to
// 4.17:1 and 4.32:1 on the tinted canvas and lower on the dark panels.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';

const publicRoot = new URL('../public/', import.meta.url);
const readPublic = (name) => readFile(new URL(name, publicRoot), 'utf8');

test('the compiled danger palette is bound to a red that passes 4.5:1 with white', async () => {
  const css = await readPublic('styles.css');
  assert.match(css, /--bs-danger: #cd2a3a;/);
  assert.match(css, /--bs-danger-rgb: 205, 42, 58;/);
});

test('the .text-danger and .text-secondary utilities resolve to accessible tokens', async () => {
  const css = await readPublic('styles.css');
  assert.match(css, /\.text-danger \{\s*color: var\(--bs-danger-text-emphasis\) !important;/);
  assert.match(css, /\.text-secondary \{\s*color: var\(--keen-muted\) !important;/);
});

test('app-owned danger literals reuse the bound danger token', async () => {
  const [css, planning] = await Promise.all([readPublic('styles.css'), readPublic('planning.css')]);
  // The score pill, the ISMS applicability matrix cells and the SWOT
  // quadrant border carry status colour; none may keep the failing literal.
  assert.match(css, /\.score-pill\.score-high \{\s*background: var\(--bs-danger\);/);
  assert.match(
    css,
    /\.isms-app-matrix-table \.isms-app-cell-high \{\s*--bs-table-bg: var\(--bs-danger\);/,
  );
  assert.match(planning, /\.quadrant-threat \{\s*border-top: 4px solid var\(--bs-danger\);/);
});

test('alert strips hidden until a status arrives carry a live role', async () => {
  const names = (await readdir(new URL('.', publicRoot)))
    .filter((name) => name.endsWith('.html'))
    .sort();
  const statusStripIds =
    /^(status|.*Status|selectedAssetSummary|suggestedControlsNote|quizNotice)$/;
  const staticNotices = new Set([
    // Rendered once with their own content, never populated with a status.
    'groupDetailEmpty',
    'myQuizzesUnlinked',
    'ec-draft-notice',
    'ec-source-scope-help',
  ]);
  const liveRole = /role="(status|alert)"|aria-live=/;
  let checked = 0;
  for (const name of names) {
    const html = await readFile(new URL(name, publicRoot), 'utf8');
    const divRe = /<div\b([^>]*)>/g;
    let attrs;
    while ((attrs = divRe.exec(html))) {
      if (!/class="[^"]*\balert\b[^"]*"/.test(attrs[1])) continue;
      const id = (attrs[1].match(/id="([^"]*)"/) || [])[1] || '';
      const isStatusStrip = statusStripIds.test(id) || /style="display:\s*none"/.test(attrs[1]);
      if (!isStatusStrip || staticNotices.has(id)) continue;
      checked++;
      assert.match(
        attrs[1],
        liveRole,
        `${name} #${id} is populated after page load and needs role="status"`,
      );
    }
  }
  assert.ok(checked >= 40, `expected the page-shell status strips, found ${checked}`);
});

test('the selection bars announce their updated count', async () => {
  for (const name of ['events.html', 'audit.html']) {
    const html = await readPublic(name);
    const bar = html.match(/<div\s+id="(selectionBar|evidenceSelectionBar)"[^>]*>/);
    assert.ok(bar, `${name} selection bar`);
    assert.match(bar[0], /aria-live="polite"/);
  }
});
