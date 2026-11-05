// Compiled-Bootstrap colour bindings in the app stylesheet, and live-region
// roles on the toast-populated status strips. The colour values here are the
// ones verified for WCAG AA contrast (see the comment block near the top of
// public/styles.css): white text on the compiled danger red #dc3545 measures
// 4.53:1, with almost no margin over the 4.5:1 small status text needs; the
// bound red #cd2a3a measures 5.27:1. The raw .text-danger and .text-secondary
// utilities fall to 4.17:1 and 4.32:1 on the tinted canvas and lower on the
// dark panels.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';

const publicRoot = new URL('../public/', import.meta.url);
const readPublic = (name) => readFile(new URL(name, publicRoot), 'utf8');

// WCAG 2 contrast ratio between two #rrggbb colours.
function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const value = parseInt(hex.slice(i, i + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a, b) {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

// Every surface a button sits on, measured in Chromium with Bootstrap 5.3.8
// and the pinned design system: the page canvas, cards with their headers and
// footers, list groups, tables, modals, dropdowns, .bg-light, .bg-white,
// tertiary panels and the six alert colours, in all nine themes.
const SURFACES = {
  light: [
    '#f6efe2',
    '#f8f9fa',
    '#ffffff',
    '#efe9dc',
    '#f8d7da',
    '#d1e7dd',
    '#e2e3e5',
    '#fcfcfd',
    '#fff3cd',
    '#cff4fc',
    '#f1f6fc',
    '#eaeff5',
    '#f4f8ff',
    '#edf1f8',
    '#f3fbf6',
    '#ecf4ef',
    '#fff7f2',
    '#f8f0eb',
    '#fff5fa',
    '#f8eef3',
    '#f6f8fb',
    '#eff1f4',
    '#f3fbfa',
    '#ecf4f3',
    '#f7f4fb',
    '#f0edf4',
  ],
  dark: [
    '#191611',
    '#262117',
    '#322c20',
    '#2b3035',
    '#2c0b0e',
    '#051b11',
    '#161719',
    '#343a40',
    '#332701',
    '#032830',
    '#141820',
    '#202631',
    '#29313e',
  ],
};
// Bootstrap 5.3.8 compiles these into its outline buttons, and sets these
// emphasis tokens for each colour mode.
const COMPILED = {
  success: '#198754',
  danger: '#dc3545',
  warning: '#ffc107',
  info: '#0dcaf0',
  dark: '#212529',
};
const EMPHASIS = {
  light: { success: '#0a3622', danger: '#58151c', warning: '#664d03', info: '#055160' },
  dark: { success: '#75b798', danger: '#ea868f', warning: '#ffda6a', info: '#6edff6' },
};
// The design system's body text in the dark themes: the Sand family's, which
// the other palettes keep, and Ink's.
const DARK_BODY_TEXT = ['#f2ede2', '#edf0f6'];

// The colours each outline button's text and border take in each mode, read
// from a stylesheet the way the browser resolves them.
function outlineColours(css) {
  const declarations = (selector) =>
    [...css.matchAll(new RegExp(`(?:^|\\n)${selector} \\{([^}]*)\\}`, 'g'))]
      .map((m) => m[1])
      .join('\n');
  const declared = (block, property) =>
    (block.match(new RegExp(`${property}:\\s*([^;]+);`)) || [])[1]?.trim();
  const resolve = (value, mode) => {
    const token = value.match(/^var\((--[a-z-]+)\)$/)?.[1];
    if (!token) return [value];
    const emphasis = token.match(/^--bs-([a-z]+)-text-emphasis$/);
    if (emphasis) return [EMPHASIS[mode][emphasis[1]]];
    if (token === '--bs-body-color' && mode === 'dark') return DARK_BODY_TEXT;
    const root = declarations(':root');
    const own =
      mode === 'dark'
        ? declared(declarations(":root\\[data-bs-theme='dark'\\]"), token)
        : undefined;
    const bound = own || declared(root, token);
    assert.ok(bound, `${token} is declared for the ${mode} themes`);
    return resolve(bound, mode);
  };
  const out = { light: {}, dark: {} };
  for (const mode of ['light', 'dark']) {
    for (const colour of Object.keys(COMPILED)) {
      const selector = `\\.btn-outline-${colour}`;
      const block = [
        declarations(selector),
        mode === 'dark' ? declarations(`:root\\[data-bs-theme='dark'\\] ${selector}`) : '',
      ].join('\n');
      const text = declared(block, '--bs-btn-color');
      assert.equal(
        declared(block, '--bs-btn-border-color'),
        text,
        `.btn-outline-${colour} keeps its border the colour of its text`,
      );
      out[mode][colour] = text ? resolve(text, mode) : [COMPILED[colour]];
    }
  }
  return out;
}

function outlineFailures(css) {
  const failures = [];
  for (const [mode, colours] of Object.entries(outlineColours(css))) {
    for (const [colour, values] of Object.entries(colours)) {
      for (const value of values) {
        const worst = Math.min(...SURFACES[mode].map((surface) => contrast(value, surface)));
        if (worst < 4.5) failures.push(`${mode} .btn-outline-${colour} ${worst.toFixed(2)}:1`);
      }
    }
  }
  return failures;
}

test('outline status buttons clear 4.5:1 on every surface in every theme', async () => {
  const css = await readPublic('styles.css');
  assert.deepEqual(outlineFailures(css), []);
  // Bootstrap's compiled palette fails the same check, so the check would
  // catch a binding that went missing.
  assert.deepEqual(outlineFailures(''), [
    'light .btn-outline-success 3.39:1',
    'light .btn-outline-danger 3.39:1',
    'light .btn-outline-warning 1.22:1',
    'light .btn-outline-info 1.47:1',
    'dark .btn-outline-success 2.54:1',
    'dark .btn-outline-danger 2.54:1',
    'dark .btn-outline-dark 1.01:1',
  ]);
});

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
