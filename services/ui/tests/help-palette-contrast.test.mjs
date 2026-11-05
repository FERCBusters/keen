// The Help site carries its own copy of the design-system palettes in
// help/design-tokens.css, applied by help/docs-theme.js from the theme a
// person chose in Keen. Its text clears WCAG AA, 4.5:1, on the surfaces
// help/docs.css paints, in every palette and in both colour modes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = async (name) =>
  (await readFile(new URL(`../public/help/${name}`, import.meta.url), 'utf8')).replace(
    /\/\*[\s\S]*?\*\//g,
    '',
  );
const tokenCss = await read('design-tokens.css');
const docsCss = await read('docs.css');

const PALETTES = ['ink', 'sand', 'ocean', 'forest', 'sunset', 'rose', 'slate', 'teal', 'purple'];
const AA = 4.5;

function declarations(css, selector) {
  const escaped = selector.replace(/[[\]'=().]/g, (c) => `\\${c}`);
  const found = {};
  for (const [, body] of css.matchAll(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`, 'g'))) {
    for (const declaration of body.split(';')) {
      const colon = declaration.indexOf(':');
      const name = declaration.slice(0, colon).trim();
      if (colon > 0 && name.startsWith('--')) found[name] = declaration.slice(colon + 1).trim();
    }
  }
  return found;
}

const channels = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const toHex = (rgb) => `#${rgb.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')}`;
// color-mix(in srgb, a share, b)
const mix = (a, share, b) =>
  toHex(channels(a).map((v, i) => v * share + channels(b)[i] * (1 - share)));

function resolver(tokens) {
  const resolve = (value) => {
    let match;
    if ((match = value.match(/^var\((--[\w-]+)(?:,\s*(.+))?\)$/))) {
      if (match[1] in tokens) return resolve(tokens[match[1]]);
      assert.ok(match[2], `${match[1]} is declared`);
      return resolve(match[2]);
    }
    if ((match = value.match(/^color-mix\(in srgb,\s*(.+?)\s+(\d+)%,\s*(.+)\)$/)))
      return mix(resolve(match[1]), Number(match[2]) / 100, resolve(match[3]));
    if (value === 'white') return '#ffffff';
    if (/^#[0-9a-f]{3}$/i.test(value)) return `#${[...value.slice(1)].map((c) => c + c).join('')}`;
    assert.match(value, /^#[0-9a-f]{6}$/i, `a colour the check can read: ${value}`);
    return value.toLowerCase();
  };
  return (name) => resolve(`var(${name})`);
}

function theme(palette, mode, css = tokenCss) {
  const tokens = { ...declarations(css, ':root'), ...declarations(docsCss, ':root') };
  if (palette !== 'sand')
    Object.assign(tokens, declarations(css, `:root[data-theme='${palette}']`));
  if (mode === 'dark') {
    Object.assign(tokens, declarations(css, ":root[data-bs-theme='dark']"));
    Object.assign(
      tokens,
      declarations(css, `:root[data-bs-theme='dark'][data-theme='${palette}']`),
    );
  }
  return resolver(tokens);
}

function luminance(hex) {
  const [r, g, b] = channels(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a, b) {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

// The callout inks docs.css tints its notes and cautions with.
const CALLOUTS = {
  light: [
    ['#0a6f82', 0.1],
    ['#b7791f', 0.12],
  ],
  dark: [
    ['#5fc9dd', 0.1],
    ['#f0b547', 0.12],
  ],
};

function failures(css = tokenCss) {
  const found = [];
  for (const mode of ['light', 'dark'])
    for (const palette of PALETTES) {
      const token = theme(palette, mode, css);
      const page = token('--bs-body-bg');
      const surfaces = [
        page,
        token('--docs-surface'),
        token('--bs-tertiary-bg'),
        token('--keen-accent-100'),
      ];
      if (mode === 'light') surfaces.push('#ffffff');
      const callouts = CALLOUTS[mode].map(([ink, share]) => mix(ink, share, page));
      const pairings = [
        ...surfaces.flatMap((bg) => [
          ['link', token('--bs-link-color'), bg],
          ['hovered link', token('--bs-link-hover-color'), bg],
          ['body text', token('--bs-body-color'), bg],
          ['muted text', token('--keen-muted'), bg],
        ]),
        ...callouts.map((bg) => ['callout text', token('--bs-body-color'), bg]),
      ];
      const worst = new Map();
      for (const [what, text, bg] of pairings) {
        const ratio = contrast(text, bg);
        if (ratio < AA && ratio < (worst.get(what)?.ratio ?? Infinity))
          worst.set(what, { ratio, text, bg });
      }
      for (const [what, { ratio, text, bg }] of worst)
        found.push(`${palette} ${mode}: ${what} ${text} on ${bg} is ${ratio.toFixed(2)}:1`);
    }
  return found;
}

test('the Help site text clears 4.5:1 in every palette and colour mode', () => {
  assert.deepEqual(failures(), []);
});

test('the check finds a Help palette that falls short', () => {
  // Sunset as it was: a bright orange as link text on the light canvas.
  const earlier = tokenCss.replace(
    /(:root\[data-theme='sunset'\] \{[^}]*--keen-accent-700: )#[0-9a-f]{6}/,
    '$1#ea580c',
  );
  assert.notEqual(earlier, tokenCss);
  assert.deepEqual(
    failures(earlier).filter((line) => line.startsWith('sunset light: link ')),
    ['sunset light: link #ea580c on #f4eee1 is 3.08:1'],
  );
});
