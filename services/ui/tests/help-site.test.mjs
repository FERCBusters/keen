// The Help site's classic scripts. docs-theme.js applies the saved palette as
// the page parses; search-index.js is the section index generated with the
// Help pages; docs.js searches it, folds the chapter menu on small screens and
// switches colour mode. Each runs as the pages load it, in the page's global.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { classicPage } from './support/classic-page.mjs';

const helpRoot = new URL('../public/help/', import.meta.url);
const read = (name) => readFile(new URL(name, helpRoot), 'utf8');
const generatedPages = JSON.parse(await read('generated-pages.json'));
const parsed = new Map();
async function helpDocument(name) {
  if (!parsed.has(name)) parsed.set(name, new JSDOM(await read(name)).window.document);
  return parsed.get(name);
}
const SCRIPTS = ['help/docs-theme.js', 'help/search-index.js', 'help/docs.js'];
const PALETTES = ['ink', 'sand', 'ocean', 'forest', 'sunset', 'rose', 'slate', 'teal', 'purple'];

// jsdom has no media queries; answer max-width queries for a viewport width.
function viewport(window, width) {
  window.matchMedia = (query) => {
    const max = /^\(max-width: (\d+)px\)$/.exec(query);
    return { media: query, matches: !!max && width <= Number(max[1]) };
  };
}

// Browsers refuse storage access when site data is blocked.
function blockStorage(window) {
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    get() {
      throw new window.DOMException('The operation is insecure.', 'SecurityError');
    },
  });
}

async function helpPage(
  t,
  name,
  { width = 1280, saved = null, index, scripts = SCRIPTS, setup } = {},
) {
  const ui = await classicPage({
    html: await read(name),
    url: 'https://keen.example/help/' + name,
    // `index` stands in for search-index.js as it arrives over the network.
    scripts: index ? scripts.filter((path) => path !== 'help/search-index.js') : scripts,
    setup(window) {
      viewport(window, width);
      if (saved !== null) window.localStorage.setItem('keen_theme', saved);
      if (index) window.KEEN_DOC_INDEX = index;
      setup?.(window);
    },
  });
  t.after(ui.close);
  return ui;
}

function search(ui, text) {
  const input = ui.document.getElementById('search');
  input.value = text;
  input.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  const results = ui.document.getElementById('results');
  return {
    results,
    notes: [...results.querySelectorAll('p')].map((p) => p.textContent),
    links: [...results.querySelectorAll('a')].map((a) => [a.getAttribute('href'), a.textContent]),
  };
}

test('every Help page loads the theme as it parses and the search scripts deferred', async () => {
  const onDisk = (await readdir(helpRoot)).filter((name) => name.endsWith('.html')).sort();
  assert.deepEqual([...generatedPages].sort(), onDisk);
  for (const name of generatedPages) {
    const document = await helpDocument(name);
    assert.deepEqual(
      [...document.querySelectorAll('script')].map((script) => [
        script.parentElement.tagName,
        script.getAttribute('src'),
        script.hasAttribute('defer'),
      ]),
      [
        ['HEAD', 'docs-theme.js', false],
        ['HEAD', 'search-index.js', true],
        ['HEAD', 'docs.js', true],
      ],
      name,
    );
    // docs.js reads these without checking for them.
    for (const id of ['search', 'results', 'docs-theme-toggle'])
      assert.equal(document.querySelectorAll('#' + id).length, 1, `${name} #${id}`);
  }
});

test('the generated search index has one entry for each Help section, linking to its heading', async () => {
  const ui = await classicPage({ scripts: ['help/search-index.js'] });
  try {
    const sections = [];
    for (const name of generatedPages) {
      const document = await helpDocument(name);
      const book = document.querySelector('main .eyebrow')?.textContent;
      for (const heading of document.querySelectorAll('main h2[id], main h3[id]'))
        sections.push({ url: name + '#' + heading.id, title: heading.textContent, book });
    }
    const byUrl = (a, b) => a.url.localeCompare(b.url);
    // Copied out of the page's realm, so the comparison sees plain values.
    const index = [...ui.window.KEEN_DOC_INDEX];
    assert.ok(sections.length > 200, `expected the Help sections, found ${sections.length}`);
    assert.deepEqual(
      index.map(({ url, title, book }) => ({ url, title, book })).sort(byUrl),
      sections.sort(byUrl),
    );
    assert.ok(index.every((entry) => typeof entry.text === 'string'));
  } finally {
    ui.close();
  }
});

test('searching the generated index lists the matching Help sections as links', async (t) => {
  const ui = await helpPage(t, 'using-start.html');
  const { notes, links } = search(ui, 'LDAP');
  assert.deepEqual(notes, ['Matching sections:']);
  assert.ok(
    links.some(
      ([href, text]) =>
        href === 'administrator-sso-ldap.html#ldap-directory-auth' &&
        text === 'LDAP directory authentication',
    ),
    JSON.stringify(links),
  );
  assert.equal(
    ui.document.querySelector('#results a').href.startsWith('https://keen.example/help/'),
    true,
  );
});

const INDEX = [
  {
    title: 'LDAP sign-in',
    url: 'administrator-sso-ldap.html#ldap',
    text: ' Bind with a service account. ',
  },
  { title: 'Restore tests', url: 'using-measures.html#restore', text: ' Plan a restore. ' },
  {
    title: 'Record evidence',
    url: 'using-events.html#evidence',
    text: ' A restore test becomes evidence. ',
  },
  ...Array.from({ length: 13 }, (_, i) => ({
    title: `Checklist ${i + 1}`,
    url: `using-operations.html#checklist-${i + 1}`,
    text: ' Routine review. ',
  })),
];

test('search matches every word in a title or text, ignoring case and spaces', async (t) => {
  const ui = await helpPage(t, 'using-start.html', { index: INDEX });
  const ldap = [['administrator-sso-ldap.html#ldap', 'LDAP sign-in']];
  assert.deepEqual(search(ui, '  ldap  ').links, ldap);
  assert.deepEqual(search(ui, 'LDAP').links, ldap);
  // Two characters are enough to search.
  assert.deepEqual(search(ui, 'ld').links, ldap);
  // Words can come from the title and the text, in either case.
  assert.deepEqual(search(ui, 'ldap BIND service').links, ldap);
  // Every word must match: "restore" alone also finds the planning section.
  assert.deepEqual(search(ui, 'restore').links, [
    ['using-measures.html#restore', 'Restore tests'],
    ['using-events.html#evidence', 'Record evidence'],
  ]);
  assert.deepEqual(search(ui, 'restore evidence').links, [
    ['using-events.html#evidence', 'Record evidence'],
  ]);
  const found = search(ui, 'restore evidence');
  assert.deepEqual(found.notes, ['Matching sections:']);
});

test('search shows at most twelve sections, in index order', async (t) => {
  const ui = await helpPage(t, 'using-start.html', { index: INDEX });
  const { links } = search(ui, 'routine review');
  assert.deepEqual(
    links.map(([href]) => href),
    Array.from({ length: 12 }, (_, i) => `using-operations.html#checklist-${i + 1}`),
  );
});

test('short and unmatched queries clear earlier results', async (t) => {
  const ui = await helpPage(t, 'using-start.html', { index: INDEX });
  assert.equal(search(ui, 'ldap').links.length, 1);
  const none = search(ui, 'kerberos');
  assert.deepEqual(none.notes, ['No matching sections. Try fewer words.']);
  assert.deepEqual(none.links, []);
  // One character, or one with spaces around it, is too short to search.
  for (const text of ['l', ' l ', '']) {
    search(ui, 'ldap');
    assert.equal(search(ui, text).results.childNodes.length, 0, JSON.stringify(text));
  }
});

test('section titles and queries with markup stay text', async (t) => {
  const hostile = '<img src=x onerror="alert(1)">Heading</a><script>alert(2)</script>';
  const ui = await helpPage(t, 'using-start.html', {
    index: [{ title: hostile, url: 'using-start.html#hostile', text: ' markup example ' }],
  });
  assert.deepEqual(search(ui, 'markup').links, [['using-start.html#hostile', hostile]]);
  const query = search(ui, '<script>alert(3)</script>');
  assert.deepEqual(query.notes, ['No matching sections. Try fewer words.']);
  assert.equal(ui.document.querySelector('#results img, #results script'), null);
});

test('search reports no matches when the index failed to load', async (t) => {
  const ui = await helpPage(t, 'using-start.html', {
    scripts: ['help/docs-theme.js', 'help/docs.js'],
  });
  assert.equal(ui.window.KEEN_DOC_INDEX, undefined);
  const { notes, links } = search(ui, 'ldap');
  assert.deepEqual(notes, ['No matching sections. Try fewer words.']);
  assert.deepEqual(links, []);
});

test('small screens fold the chapter menu and wider ones keep it open', async (t) => {
  for (const [width, open] of [
    [375, false],
    [850, false],
    [851, true],
    [1280, true],
  ]) {
    const ui = await helpPage(t, 'using-start.html', { width });
    assert.equal(ui.document.querySelector('.chapter-menu').open, open, `${width}px`);
  }
  // The handbook index has no chapter menu; its search still works.
  const ui = await helpPage(t, 'index.html', { width: 375, index: INDEX });
  assert.equal(ui.document.querySelector('.chapter-menu'), null);
  assert.deepEqual(search(ui, 'ldap').links, [
    ['administrator-sso-ldap.html#ldap', 'LDAP sign-in'],
  ]);
});

test('the colour mode button switches mode and saves it with the palette', async (t) => {
  const ui = await helpPage(t, 'using-start.html', { saved: 'ocean-dark' });
  const root = ui.document.documentElement;
  const toggle = ui.document.getElementById('docs-theme-toggle');
  assert.deepEqual([root.dataset.theme, root.dataset.bsTheme], ['ocean', 'dark']);
  assert.equal(toggle.textContent, 'Light mode');
  toggle.click();
  assert.equal(root.dataset.bsTheme, 'light');
  assert.equal(ui.window.localStorage.getItem('keen_theme'), 'ocean');
  assert.equal(toggle.textContent, 'Dark mode');
  toggle.click();
  assert.equal(root.dataset.bsTheme, 'dark');
  assert.equal(ui.window.localStorage.getItem('keen_theme'), 'ocean-dark');
  assert.equal(toggle.textContent, 'Light mode');

  const fresh = await helpPage(t, 'using-start.html');
  assert.deepEqual(
    [fresh.document.documentElement.dataset.theme, fresh.document.documentElement.dataset.bsTheme],
    ['ink', 'light'],
  );
  assert.equal(fresh.document.getElementById('docs-theme-toggle').textContent, 'Dark mode');
  fresh.document.getElementById('docs-theme-toggle').click();
  assert.equal(fresh.window.localStorage.getItem('keen_theme'), 'ink-dark');
});

test('with storage blocked the Help pages use the default palette and still switch mode', async (t) => {
  const ui = await helpPage(t, 'using-start.html', { setup: blockStorage });
  const root = ui.document.documentElement;
  const toggle = ui.document.getElementById('docs-theme-toggle');
  assert.deepEqual([root.dataset.theme, root.dataset.bsTheme], ['ink', 'light']);
  toggle.click();
  assert.equal(root.dataset.bsTheme, 'dark');
  assert.equal(toggle.textContent, 'Light mode');
});

test('the saved theme applies only a known palette, and dark mode only with one', async (t) => {
  const cases = [
    ...PALETTES.flatMap((palette) => [
      [palette, palette, 'light'],
      [palette + '-dark', palette, 'dark'],
    ]),
    ['neon', 'ink', 'light'],
    ['neon-dark', 'ink', 'light'],
    ['-dark', 'ink', 'light'],
    ['dark', 'ink', 'light'],
    ['OCEAN-dark', 'ink', 'light'],
    ['"><img src=x onerror="alert(1)">-dark', 'ink', 'light'],
  ];
  for (const [saved, theme, mode] of cases) {
    const ui = await classicPage({
      html: '<!doctype html><html><head></head><body></body></html>',
      url: 'https://keen.example/help/index.html',
      scripts: ['help/docs-theme.js'],
      setup: (window) => window.localStorage.setItem('keen_theme', saved),
    });
    try {
      const root = ui.document.documentElement;
      assert.deepEqual([root.dataset.theme, root.dataset.bsTheme], [theme, mode], saved);
      assert.equal(ui.document.querySelector('img'), null);
    } finally {
      ui.close();
    }
  }
});
