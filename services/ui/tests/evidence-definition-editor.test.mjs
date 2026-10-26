// Evidence definitions through the assembled mapping editor: collection inputs,
// the BookStack picker, the definition library, revisions and historical runs.
// A small in-memory server stands in for the administrator API.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const hostile = '<img src=x onerror=alert(1)>';
const collectorId = (adapter, section, key) => JSON.stringify([adapter, section, String(key)]);
function collection(adapter, section, key, entry, connectionId) {
  return {
    adapter,
    section,
    key,
    collector: collectorId(adapter, section, key),
    entry,
    ...(connectionId ? { connection_id: connectionId } : {}),
    version: 1,
  };
}
function page(items, offset, size) {
  return {
    items: items.slice(offset, offset + size),
    next_offset: offset + size < items.length ? offset + size : null,
  };
}

async function editor(t, server = {}) {
  const state = {
    sources: ['keen-agent'],
    rules: [],
    collectors: [],
    connections: [],
    rulesVersion: 7,
    documents: {},
    settings: {},
    books: [],
    chapters: {},
    pages: {},
    catalogueSize: 2,
    failCatalogue: null,
    revisions: {},
    restored: {},
    backfills: {},
    jobs: {},
    saveBackfill: null,
    startBackfill: null,
    preview: { examined: 1, matched: 1, examples: [], nonmatches: [] },
    samples: [],
    suggestions: [],
    integrations: [],
    confirm: true,
    ...server,
  };
  const calls = [],
    writes = [],
    confirmations = [],
    delayed = [];
  let pending = 0;
  const tracked = (method, handler) => async (raw, body) => {
    pending++;
    try {
      const url = new URL(raw, 'https://keen.example');
      if (method === 'GET') calls.push(raw);
      // Page objects come from another realm; keep plain copies for comparison.
      else
        writes.push({
          method,
          path: url.pathname + url.search,
          body: body === undefined ? undefined : JSON.parse(JSON.stringify(body)),
        });
      return await handler(url, body);
    } finally {
      pending--;
    }
  };
  const definitions = () =>
    structuredClone({
      rules: state.rules,
      collectors: state.collectors,
      rules_version: state.rulesVersion,
      warnings: state.warnings,
    });
  const get = tracked('GET', async (url) => {
    const path = url.pathname,
      query = url.searchParams;
    if (path === '/api/v1/admin/evidence-event-samples')
      return { sources: [], items: structuredClone(state.samples) };
    if (path === '/api/v1/admin/source-catalogue')
      return { items: state.sources.map((adapter) => ({ adapter, enabled: true })) };
    if (path === '/api/v1/admin/integrations')
      return { collectors: structuredClone(state.integrations) };
    if (path === '/api/v1/admin/source-connections')
      return { connections: structuredClone(state.connections), types: [] };
    if (path === '/api/v1/admin/evidence-definitions') return definitions();
    if (path.startsWith('/api/v1/admin/managed-configurations/'))
      return structuredClone(
        state.documents[decodeURIComponent(path.split('/').pop())] || {
          document: {},
          version: 1,
        },
      );
    if (path.startsWith('/api/v1/admin/adapter-settings/'))
      return structuredClone(
        state.settings[decodeURIComponent(path.split('/').pop())] || { settings: {}, version: 1 },
      );
    if (path === '/api/v1/frameworks') return { items: [{ slug: 'A', name: 'Framework A' }] };
    if (path === '/api/v1/controls') return { items: [{ ref: 'A1', title: 'Control A' }] };
    if (path === '/api/v1/admin/bookstack/catalog') {
      const kind = query.get('kind'),
        offset = Number(query.get('offset')),
        book = query.get('book_id');
      if (state.failCatalogue === kind)
        throw new Error(
          'BookStack catalogue is unavailable; check the configured backend URL and API credentials',
        );
      const items =
        kind === 'books' ? state.books : (kind === 'chapters' ? state.chapters : state.pages)[book];
      return page(items || [], offset, state.catalogueSize);
    }
    const revisions = path.match(/^\/api\/v1\/admin\/mapping-rules\/([^/]+)\/revisions$/);
    if (revisions) return { items: state.revisions[decodeURIComponent(revisions[1])] || [] };
    if (path === '/api/v1/admin/rule-backfills')
      return { items: state.backfills[query.get('rule_id')] || [] };
    const job = path.match(/^\/api\/v1\/admin\/rule-backfills\/([^/]+)$/);
    if (job) {
      const sequence = state.jobs[decodeURIComponent(job[1])];
      return structuredClone(sequence.length > 1 ? sequence.shift() : sequence[0]);
    }
    throw new Error('Unexpected GET ' + url.pathname + url.search);
  });
  const post = tracked('POST', async (url, body) => {
    const path = url.pathname;
    if (path === '/api/v1/admin/mapping-rules/preview') return structuredClone(state.preview);
    if (path === '/api/v1/admin/control-suggestions') {
      if (state.failSuggestions) throw new Error(state.failSuggestions);
      return { items: structuredClone(state.suggestions) };
    }
    const restore = path.match(
      /^\/api\/v1\/admin\/mapping-rules\/([^/]+)\/revisions\/(\d+)\/restore$/,
    );
    if (restore) {
      const id = decodeURIComponent(restore[1]);
      state.rules = state.rules.map((rule) =>
        rule.id === id ? structuredClone(state.restored[restore[2]]) : rule,
      );
      state.rulesVersion++;
      return { rules_version: state.rulesVersion };
    }
    const backfill = path.match(/^\/api\/v1\/admin\/mapping-rules\/([^/]+)\/backfill$/);
    if (backfill) {
      if (state.failBackfill) throw new Error(state.failBackfill);
      return { backfill: structuredClone(state.startBackfill) };
    }
    const cancel = path.match(/^\/api\/v1\/admin\/rule-backfills\/([^/]+)\/cancel$/);
    if (cancel) {
      const sequence = state.jobs[decodeURIComponent(cancel[1])];
      sequence.splice(0, sequence.length, { ...sequence[0], status: 'cancelled' });
      return { status: 'cancelled' };
    }
    throw new Error('Unexpected POST ' + path);
  });
  const put = tracked('PUT', async (url, body) => {
    const path = url.pathname;
    if (path.startsWith('/api/v1/admin/evidence-definitions/')) {
      if (state.failSave) throw new Error(state.failSave);
      const rule = structuredClone(body.rule);
      state.rules = [...state.rules.filter((item) => item.id !== rule.id), rule];
      const item = state.collectors.find(
        (c) =>
          c.collector === rule.when.collector &&
          (c.connection_id || 'env:' + c.adapter) === (body.connection_id || 'env:' + c.adapter),
      );
      if (item) item.entry = structuredClone(body.entry);
      state.rulesVersion++;
      return {
        rules_version: state.rulesVersion,
        rule,
        backfill: structuredClone(state.saveBackfill),
      };
    }
    if (path.startsWith('/api/v1/admin/adapter-settings/')) {
      const adapter = decodeURIComponent(path.split('/').pop());
      const version = (state.settings[adapter]?.version || 1) + 1;
      state.settings[adapter] = { settings: structuredClone(body.settings), version };
      return { version };
    }
    throw new Error('Unexpected PUT ' + path);
  });
  const remove = tracked('DELETE', async (url) => {
    const rule = url.pathname.match(/^\/api\/v1\/admin\/mapping-rules\/([^/]+)$/);
    if (!rule || state.failDelete) throw new Error(state.failDelete || 'Unexpected DELETE');
    state.rules = state.rules.filter((item) => item.id !== decodeURIComponent(rule[1]));
    state.rulesVersion++;
    return {};
  });
  const ui = await loadUi('pages/evidence-config.js', {
    html,
    url: 'https://keen.example/admin.html' + (state.query || '') + '#evidence-config',
    setup: state.setup,
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (asked) => {
          confirmations.push(asked);
          return typeof state.confirm === 'function' ? state.confirm(asked) : state.confirm;
        },
      },
    },
    globals: {
      // Historical runs poll every five seconds; tests advance those polls.
      setTimeout: (fn, ms, ...args) => {
        if (ms < 5000) return setTimeout(fn, ms, ...args);
        delayed.push({ fn, ms });
        return -delayed.length;
      },
      clearTimeout: (id) => {
        if (typeof id === 'number' && id < 0) delayed[-id - 1].cancelled = true;
        else clearTimeout(id);
      },
      ...state.globals,
    },
    vendor: {
      apiGet: get,
      apiPost: post,
      apiPut: put,
      apiDelete: remove,
      getCurrentFramework: () => 'A',
    },
  });
  t.after(() => ui.close());
  const $ = (suffix) => ui.document.getElementById('ec-' + suffix);
  async function idle() {
    for (let quiet = 0, i = 0; i < 400; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1));
      quiet = pending ? 0 : quiet + 1;
      if (quiet === 3) return;
    }
    assert.fail('API requests did not settle');
  }
  async function choose(suffix, value) {
    const select = $(suffix);
    select.value = value;
    assert.equal(select.value, value, `${suffix} offers ${value}`);
    select.dispatchEvent(new ui.window.Event('change'));
    await idle();
  }
  async function click(suffix) {
    $(suffix).click();
    await idle();
  }
  // The steps an administrator takes from New mapping to a collection input.
  async function openInput(adapter, connection, section, entry) {
    await click('new-rule');
    await choose('definition-adapter', adapter);
    if (connection !== undefined) await choose('definition-connection', connection);
    if (section) await choose('definition-section', section);
    if (entry) await choose('definition-entry', entry);
  }
  async function publish({ description = 'Collected evidence', predefine = false } = {}) {
    $('description').value = description;
    $('control').value = 'A1';
    $('add-target').click();
    $('predefine').checked = predefine;
    if (!predefine) await click('preview');
    await click('save-rule');
    return writes.filter((write) => write.path.startsWith('/api/v1/admin/evidence-definitions/'));
  }
  async function runPolls() {
    const due = delayed.filter((timer) => timer.ms === 5000 && !timer.cancelled && !timer.ran);
    for (const timer of due) {
      timer.ran = true;
      timer.fn();
    }
    await idle();
    return due.length;
  }
  const field = (name) => $('definition-fields').querySelector(`[data-field="${name}"]`);
  const library = () =>
    [...$('rules').children].map((row) =>
      row.tagName === 'P'
        ? [row.textContent]
        : [row.querySelector('strong').textContent, row.querySelector('.small-muted').textContent],
    );
  await idle();
  assert.ok($('control').options.length > 1, 'the editor finished loading');
  return {
    ...ui,
    $,
    state,
    calls,
    writes,
    confirmations,
    idle,
    choose,
    click,
    openInput,
    publish,
    runPolls,
    field,
    library,
  };
}

test("the BookStack picker pages through books and groups each book's pages by chapter", async (t) => {
  const chosen = { id: 12, name: 'Access policy' };
  const ui = await editor(t, {
    sources: ['bookstack'],
    connections: [
      {
        id: 'bs-one',
        source: 'bookstack',
        name: 'Policies wiki',
        configuration: {},
        inputs: { selected_pages: [chosen] },
        version: 4,
      },
    ],
    collectors: [collection('bookstack', 'selected_pages', '12', chosen, 'bs-one')],
    books: [
      { id: 7, name: 'Policies', slug: 'policies' },
      { id: 8, name: hostile, slug: 'hostile' },
      { id: 9, name: '', slug: 'runbooks' },
    ],
    chapters: {
      7: [
        { id: 70, name: 'Access' },
        { id: 71, name: 'Operations' },
        { id: 72, name: 'Unused' },
      ],
    },
    pages: {
      7: [
        { id: 12, name: 'Access policy', slug: 'access-policy', chapter_id: 70, book_id: 7 },
        { id: 13, name: 'Runbook', slug: 'runbook', chapter_id: 71, book_id: 7 },
        { id: 14, name: 'Glossary', slug: 'glossary', chapter_id: null, book_id: 7 },
      ],
    },
  });
  await ui.openInput(
    'bookstack',
    'bs-one',
    'selected_pages',
    collectorId('bookstack', 'selected_pages', '12'),
  );

  const books = () => [...ui.$('bookstack-book').options].map((o) => [o.value, o.textContent]);
  const catalogue = (kind) => ui.calls.filter((call) => call.includes('kind=' + kind));
  assert.equal(ui.$('bookstack-picker').hidden, false);
  assert.equal(
    catalogue('books').at(-1),
    '/api/v1/admin/bookstack/catalog?kind=books&offset=0&connection_id=bs-one',
  );
  assert.deepEqual(books(), [
    ['', 'Choose a book'],
    ['7', 'Policies'],
    ['8', hostile],
  ]);
  assert.equal(ui.$('bookstack-book').querySelector('img'), null);
  assert.equal(ui.$('bookstack-more-books').hidden, false);
  await ui.click('bookstack-more-books');
  assert.equal(
    catalogue('books').at(-1),
    '/api/v1/admin/bookstack/catalog?kind=books&offset=2&connection_id=bs-one',
  );
  assert.deepEqual(books().at(-1), ['9', 'runbooks']);
  assert.equal(ui.$('bookstack-more-books').hidden, true);

  await ui.choose('bookstack-book', '7');
  assert.deepEqual(catalogue('chapters'), [
    '/api/v1/admin/bookstack/catalog?kind=chapters&offset=0&connection_id=bs-one&book_id=7',
    '/api/v1/admin/bookstack/catalog?kind=chapters&offset=2&connection_id=bs-one&book_id=7',
  ]);
  const groups = () =>
    [...ui.$('bookstack-page').querySelectorAll('optgroup')].map((group) => [
      group.label,
      [...group.children].map((option) => option.textContent),
    ]);
  assert.deepEqual(groups(), [
    ['Access', ['Access policy']],
    ['Operations', ['Runbook']],
  ]);
  assert.equal(ui.$('bookstack-status').textContent, '2 pages loaded from Policies.');
  assert.equal(ui.$('bookstack-more-pages').hidden, false);
  await ui.click('bookstack-more-pages');
  assert.equal(
    catalogue('pages').at(-1),
    '/api/v1/admin/bookstack/catalog?kind=pages&offset=2&connection_id=bs-one&book_id=7',
  );
  assert.deepEqual(groups(), [
    ['Access', ['Access policy']],
    ['Operations', ['Runbook']],
    ['Pages outside chapters', ['Glossary']],
  ]);
  assert.equal(ui.$('bookstack-status').textContent, '3 pages loaded from Policies.');
  assert.equal(ui.$('bookstack-more-pages').hidden, true);
  // Loading more pages reuses the chapters already listed for the book.
  assert.equal(catalogue('chapters').length, 2);
});

test('choosing a BookStack page records its book and slug with the saved input', async (t) => {
  const chosen = { id: 12, name: 'Access policy' };
  const ui = await editor(t, {
    sources: ['bookstack'],
    connections: [
      {
        id: 'bs-one',
        source: 'bookstack',
        name: 'Policies wiki',
        configuration: {},
        inputs: { selected_pages: [chosen] },
        version: 4,
      },
    ],
    collectors: [collection('bookstack', 'selected_pages', '12', chosen, 'bs-one')],
    books: [{ id: 7, name: 'Policies', slug: 'policies' }],
    chapters: { 7: [] },
    pages: { 7: [{ id: 12, name: 'Access policy', slug: 'access-policy', chapter_id: null }] },
  });
  await ui.openInput(
    'bookstack',
    'bs-one',
    'selected_pages',
    collectorId('bookstack', 'selected_pages', '12'),
  );
  const values = () =>
    ['id', 'book_id', 'book_slug', 'slug', 'name'].map((name) => ui.field(name).value);
  assert.deepEqual(values(), ['12', '', '', '', 'Access policy']);
  // Page identity comes from the picker; the fields only show it.
  assert.ok(['id', 'book_id', 'slug'].every((name) => ui.field(name).readOnly));

  await ui.choose('bookstack-book', '7');
  await ui.choose('bookstack-page', '12');
  assert.deepEqual(values(), ['12', '7', 'policies', 'access-policy', 'Access policy']);
  assert.equal(
    ui.$('bookstack-status').textContent,
    'Selected Policies → Access policy. Save to capture this page and map its evidence.',
  );

  const [saved] = await ui.publish({ description: 'Access policy is maintained', predefine: true });
  assert.equal(saved.path, '/api/v1/admin/evidence-definitions/bookstack_12');
  assert.deepEqual(
    { ...saved.body, rule: undefined },
    {
      adapter: 'bookstack',
      section: 'selected_pages',
      entry_key: '12',
      entry: {
        id: 12,
        book_id: 7,
        book_slug: 'policies',
        slug: 'access-policy',
        name: chosen.name,
      },
      connection_id: 'bs-one',
      rule: undefined,
      connector_version: 4,
      rules_version: 7,
      apply_existing: true,
    },
  );
  assert.deepEqual(saved.body.rule.when, {
    source: 'bookstack',
    connection_id: 'bs-one',
    collector: collectorId('bookstack', 'selected_pages', '12'),
  });
  // The saved definition reopens on the same input.
  assert.equal(ui.$('rule-id').value, 'bookstack_12');
  assert.equal(ui.$('rule-id').disabled, true);
  assert.equal(
    ui.$('selected-input').textContent,
    'Selected input: bookstack page “12” from Policies wiki. Choose another definition from the library to change inputs.',
  );
  assert.equal(ui.$('status').textContent, 'Mapping saved. Future evidence uses it immediately.');
});

test("a book's chapter listing stops after five catalogue pages", async (t) => {
  const chapters = Array.from({ length: 30 }, (_, i) => ({ id: 100 + i, name: 'Chapter ' + i }));
  const ui = await editor(t, {
    sources: ['bookstack'],
    collectors: [collection('bookstack', 'selected_pages', '5', { id: 5, name: 'Page' })],
    books: [{ id: 7, name: 'Policies', slug: 'policies' }],
    chapters: { 7: chapters },
    pages: { 7: [{ id: 5, name: 'Late page', chapter_id: 129 }] },
  });
  await ui.openInput('bookstack', 'env:bookstack', 'selected_pages');
  await ui.choose('bookstack-book', '7');

  const requested = ui.calls.filter((call) => call.includes('kind=chapters'));
  assert.deepEqual(
    requested.map((call) => new URL(call, 'https://keen.example').searchParams.get('offset')),
    ['0', '2', '4', '6', '8'],
  );
  // The page list still loads; pages in chapters beyond the bound stay listed.
  assert.deepEqual(
    [...ui.$('bookstack-page').querySelectorAll('optgroup')].map((group) => group.label),
    ['Pages outside chapters'],
  );
});

test('BookStack catalogue failures are reported in the picker', async (t) => {
  const ui = await editor(t, {
    sources: ['bookstack'],
    collectors: [collection('bookstack', 'selected_pages', '5', { id: 5, name: 'Page' })],
    books: [{ id: 7, name: 'Policies', slug: 'policies' }],
    chapters: { 7: [] },
    pages: { 7: [] },
    failCatalogue: 'books',
  });
  await ui.openInput('bookstack');
  assert.match(ui.$('bookstack-status').textContent, /^BookStack catalogue is unavailable/);

  ui.state.failCatalogue = 'pages';
  await ui.choose('definition-connection', 'env:bookstack');
  assert.equal(ui.$('bookstack-book').options.length, 2);
  await ui.choose('bookstack-book', '7');
  assert.match(ui.$('bookstack-status').textContent, /^BookStack catalogue is unavailable/);
  assert.equal(ui.$('status').hidden, true, 'catalogue problems stay within the picker');
});

test('collection fields become the saved input and keep settings the editor does not show', async (t) => {
  const url = 'https://status.example/feed.xml';
  const entry = {
    name: 'Status page',
    url,
    label: 'Status',
    system: 'status',
    enabled: false,
    max_items: 20,
    headers: { Accept: 'application/rss+xml' },
  };
  const ui = await editor(t, {
    sources: ['rss'],
    documents: { rss: { document: { feeds: [entry] }, version: 3 } },
    collectors: [collection('rss', 'feeds', url, entry)],
  });
  await ui.openInput('rss', 'env:rss', 'feeds', collectorId('rss', 'feeds', url));

  assert.equal(ui.field('enabled').type, 'checkbox');
  assert.equal(ui.field('enabled').checked, false);
  assert.equal(ui.field('max_items').type, 'number');
  assert.equal(ui.field('max_items').value, '20');
  assert.deepEqual(JSON.parse(ui.$('definition-extra').value), {
    headers: { Accept: 'application/rss+xml' },
  });

  ui.field('enabled').checked = true;
  ui.field('max_items').value = '25';
  ui.field('system').value = '  ';
  const [saved] = await ui.publish({ description: 'Status page incidents' });
  const preview = ui.writes.find((write) => write.path === '/api/v1/admin/mapping-rules/preview');
  assert.deepEqual(preview.body.rule.when, {
    source: 'rss',
    connection_id: 'env:rss',
    collector: collectorId('rss', 'feeds', url),
  });
  assert.equal(preview.body.version, 7);
  // Blank fields are left out; numbers and switches keep their types.
  assert.deepEqual(saved.body.entry, {
    headers: { Accept: 'application/rss+xml' },
    name: 'Status page',
    label: 'Status',
    url,
    enabled: true,
    max_items: 25,
  });
  assert.equal(saved.body.entry_key, url);
  assert.equal(saved.body.connector_version, 3);
  assert.equal(saved.body.connection_id, 'env:rss');

  // The reopened definition saves a switched-off input as switched off.
  ui.field('enabled').checked = false;
  const [, again] = await ui.publish({ description: 'Status page incidents' });
  assert.equal(again.path, saved.path);
  assert.equal(again.body.entry.enabled, false);
});

test('collection inputs refuse unusable settings before anything is sent', async (t) => {
  const url = 'https://status.example/feed.xml';
  const ui = await editor(t, {
    sources: ['rss'],
    collectors: [collection('rss', 'feeds', url, { name: 'Status page', url })],
  });
  await ui.openInput('rss', 'env:rss', 'feeds', collectorId('rss', 'feeds', url));
  ui.$('description').value = 'Status page incidents';

  for (const extra of ['[1]', 'null', '"text"']) {
    ui.$('definition-extra').value = extra;
    await ui.click('preview');
    assert.equal(
      ui.$('preview-results').textContent,
      'Additional collection settings must be a JSON object.',
      extra,
    );
  }
  ui.$('definition-extra').value = '{}';
  ui.field('url').value = '';
  await ui.click('preview');
  assert.equal(ui.$('preview-results').textContent, 'Give the collection item a name, URL or ID.');
  await ui.click('save-rule');
  assert.equal(ui.$('status').textContent, 'Give the collection item a name, URL or ID.');
  assert.deepEqual(ui.writes, []);
});

test('dotted collection fields are saved as nested settings', async (t) => {
  const entry = {
    name: 'auth',
    logql: '{job="auth"}',
    event: { system: 'idp', action: 'login', severity: 5 },
    limit: 500,
  };
  const ui = await editor(t, {
    sources: ['loki'],
    collectors: [collection('loki', 'queries', 'auth', entry)],
  });
  await ui.openInput('loki', 'env:loki', 'queries', collectorId('loki', 'queries', 'auth'));
  assert.equal(ui.field('event.system').value, 'idp');
  assert.equal(ui.field('event.severity').type, 'number');
  assert.equal(ui.field('logql').tagName, 'TEXTAREA');
  assert.deepEqual(JSON.parse(ui.$('definition-extra').value), { event: {}, limit: 500 });

  ui.field('event.outcome').value = 'success';
  const [saved] = await ui.publish({ description: 'Successful sign-ins' });
  assert.deepEqual(saved.body.entry, {
    name: 'auth',
    logql: '{job="auth"}',
    event: { system: 'idp', action: 'login', outcome: 'success', severity: 5 },
    limit: 500,
  });
});

test('repository inputs are identified by owner and name', async (t) => {
  const entry = { owner: 'octo', repo: 'keen', label: 'Keen' };
  const ui = await editor(t, {
    sources: ['github'],
    connections: [
      {
        id: 'gh-one',
        source: 'github',
        name: 'Engineering GitHub',
        configuration: {},
        inputs: { repos: [entry] },
        version: 2,
      },
    ],
    collectors: [collection('github', 'repos', 'octo/keen', entry, 'gh-one')],
  });
  await ui.openInput('github', 'gh-one', 'repos', collectorId('github', 'repos', 'octo/keen'));
  const [saved] = await ui.publish({ description: 'Repository changes' });

  assert.equal(saved.path, '/api/v1/admin/evidence-definitions/github_octo_keen');
  assert.equal(saved.body.rule.when.collector, collectorId('github', 'repos', 'octo/keen'));
  assert.equal(saved.body.entry_key, 'octo/keen');
  assert.equal(saved.body.connector_version, 2);
});

test('a mapping rule started from an Elasticsearch query opens on that query', async (t) => {
  const entry = { name: 'Authentication', index_pattern: 'logs-*', query: '"login failed"' };
  const input = collection('elasticsearch', 'queries', 'Authentication', entry, 'elastic-one');
  const ui = await editor(t, {
    sources: ['elasticsearch'],
    connections: [
      {
        id: 'elastic-one',
        source: 'elasticsearch',
        name: 'Production logs',
        configuration: {},
        inputs: { queries: [entry] },
        version: 2,
      },
    ],
    collectors: [input],
  });
  ui.window.dispatchEvent(
    new ui.window.CustomEvent('keen-collection-create', {
      detail: { source: 'elasticsearch', connection_id: 'elastic-one', input },
    }),
  );
  await ui.idle();

  assert.equal(ui.$('definition-section').value, 'queries');
  assert.equal(ui.$('definition-entry').value, input.collector);
  assert.equal(ui.field('index_pattern').value, 'logs-*');
  assert.equal(ui.$('input-options').open, true);
  const [saved] = await ui.publish({ description: 'Failed sign-ins' });
  assert.equal(saved.body.section, 'queries');
  assert.equal(saved.body.entry_key, 'Authentication');
  assert.deepEqual(saved.body.entry, entry);
  assert.equal(saved.body.connector_version, 2);
  assert.equal(saved.body.rule.when.collector, input.collector);
});

function rules(count) {
  return Array.from({ length: count }, (_, i) => ({
    id: 'rule-' + String(i + 1).padStart(2, '0'),
    description: 'Definition ' + String(i + 1).padStart(2, '0'),
    when: { source: 'keen-agent' },
    map_to: [{ framework: 'A', ref: 'A1' }],
  }));
}

test('the definition library pages, searches and filters by source', async (t) => {
  const entry = { owner: 'octo', repo: 'keen' };
  const library = [
    ...rules(22),
    { id: 'paused', description: hostile, enabled: false, when: { source: 'rss' }, map_to: [] },
    {
      id: 'repo-changes',
      when: {
        source: 'github',
        connection_id: 'gh-one',
        collector: collectorId('github', 'repos', 'octo/keen'),
      },
      map_to: [{ framework: 'A', ref: 'A1' }],
    },
    { id: 'retired-connection', when: { source: 'rss', connection_id: 'gone' }, map_to: [] },
  ];
  const ui = await editor(t, {
    rules: library,
    connections: [{ id: 'gh-one', source: 'github', name: 'Engineering GitHub', inputs: {} }],
    collectors: [collection('github', 'repos', 'octo/keen', entry, 'gh-one')],
  });
  assert.equal(ui.$('rule-count').textContent, '25 of 25 mapping rules · page 1 of 2');
  assert.equal(ui.library().length, 20);
  assert.equal(ui.$('previous-rules').hidden, true);
  assert.equal(ui.$('more-rules').hidden, false);

  ui.$('more-rules').click();
  assert.equal(ui.$('rule-count').textContent, '25 of 25 mapping rules · page 2 of 2');
  assert.deepEqual(ui.library().slice(2), [
    [hostile, 'Paused · All rss events · 0 framework targets'],
    ['repo-changes', 'github repository: octo/keen · 1 framework targets · Engineering GitHub'],
    ['retired-connection', 'All rss events · 0 framework targets · gone'],
  ]);
  assert.equal(ui.$('rules').querySelector('img'), null);
  assert.equal(ui.$('more-rules').hidden, true);
  ui.$('previous-rules').click();
  assert.equal(ui.$('rule-count').textContent, '25 of 25 mapping rules · page 1 of 2');

  // A search from a later page starts again at its first page.
  ui.$('more-rules').click();
  ui.$('rule-filter').value = 'definition';
  ui.$('rule-filter').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.$('rule-count').textContent, '22 of 25 mapping rules · page 1 of 2');

  // Searches also reach collection keys.
  ui.$('more-rules').click();
  ui.$('rule-filter').value = 'OCTO/keen';
  ui.$('rule-filter').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.$('rule-count').textContent, '1 of 25 mapping rules · page 1 of 1');
  assert.deepEqual(
    ui.library().map(([title]) => title),
    ['repo-changes'],
  );
  ui.$('rule-filter').value = 'no such definition';
  ui.$('rule-filter').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(ui.library(), [['No evidence definitions match that search.']]);

  ui.$('rule-filter').value = '';
  await ui.choose('source-filter', 'rss');
  assert.deepEqual(
    ui.library().map(([title]) => title),
    [hostile, 'retired-connection'],
  );
  assert.deepEqual(
    [...ui.$('source-filter').options].map((option) => option.value),
    ['', 'github', 'keen-agent', 'rss'],
  );
});

test('an empty library invites the first definition', async (t) => {
  const ui = await editor(t);
  assert.deepEqual(ui.library(), [['No evidence definitions yet. Create one to get started.']]);
  assert.equal(ui.$('rule-count').textContent, '0 of 0 mapping rules · page 1 of 1');
});

test('deleting a definition asks first and keeps its collection', async (t) => {
  const ui = await editor(t, { rules: rules(2), confirm: false });
  const deleteButton = () =>
    [...ui.$('rules').querySelectorAll('button')].find(
      (button) => button.textContent === 'Delete definition',
    );
  deleteButton().click();
  await ui.idle();
  assert.deepEqual(
    ui.confirmations.map((c) => c.message),
    ['Delete evidence definition Definition 01? Collection and existing evidence are retained.'],
  );
  assert.deepEqual(ui.writes, []);

  ui.state.confirm = true;
  deleteButton().click();
  await ui.idle();
  assert.deepEqual(
    ui.writes.map((write) => [write.method, write.path]),
    [['DELETE', '/api/v1/admin/mapping-rules/rule-01?version=7']],
  );
  assert.deepEqual(
    ui.library().map(([title]) => title),
    ['Definition 02'],
  );
  assert.equal(
    ui.$('status').textContent,
    'Evidence definition deleted. Collection and existing evidence retained.',
  );

  ui.state.failDelete = 'Rules changed in another session. Reload before saving.';
  deleteButton().click();
  await ui.idle();
  assert.equal(ui.$('status').textContent, ui.state.failDelete);
  assert.equal(ui.$('status').getAttribute('role'), 'alert');
});

test('the editor deletes only saved definitions', async (t) => {
  const ui = await editor(t, { rules: rules(1) });
  await ui.click('new-rule');
  await ui.click('delete-rule');
  assert.equal(
    ui.$('status').textContent,
    'This definition has not been saved. Remove its input under Sources to stop collecting it.',
  );
  assert.deepEqual(ui.confirmations, []);

  ui.$('rules').querySelector('button').click();
  await ui.idle();
  await ui.click('delete-rule');
  assert.deepEqual(
    ui.confirmations.map((c) => c.message),
    ['Delete evidence definition rule-01? Collected evidence and existing mappings are retained.'],
  );
  assert.deepEqual(
    ui.writes.map((write) => [write.method, write.path]),
    [['DELETE', '/api/v1/admin/mapping-rules/rule-01?version=7']],
  );
  assert.equal(
    ui.$('status').textContent,
    'Definition removed. Existing evidence and mappings are retained.',
  );
  assert.equal(ui.$('library').hidden, false);
});

test('opening a definition restores its source, connection and input', async (t) => {
  const entry = { owner: 'octo', repo: 'keen', label: 'Keen' };
  const ui = await editor(t, {
    sources: ['github', 'keen-agent'],
    rules: [
      {
        id: 'repo-changes',
        description: 'Repository changes',
        when: {
          source: 'github',
          connection_id: 'gh-one',
          collector: collectorId('github', 'repos', 'octo/keen'),
          action_regex: '^push$',
        },
        map_to: [{ framework: 'A', ref: 'A1', kind: 'control' }],
        confidence: 0.6,
        enabled: false,
      },
      {
        id: 'webhook-reviews',
        description: 'Webhook reviews',
        when: { source: 'webhook:github' },
        map_to: [],
      },
      {
        id: 'retired-logs',
        description: 'Retired log source',
        when: { source: 'cloudwatch_logs' },
        map_to: [],
      },
    ],
    connections: [
      {
        id: 'gh-one',
        source: 'github',
        name: 'Engineering GitHub',
        configuration: {},
        inputs: { repos: [entry] },
        version: 2,
      },
    ],
    collectors: [collection('github', 'repos', 'octo/keen', entry, 'gh-one')],
  });
  const open = async (title) => {
    [...ui.$('rules').querySelectorAll('button')]
      .find((button) => button.querySelector('strong')?.textContent === title)
      .click();
    await ui.idle();
  };

  await open('Repository changes');
  assert.deepEqual(
    ['definition-adapter', 'definition-connection', 'definition-section', 'definition-entry'].map(
      (id) => ui.$(id).value,
    ),
    ['github', 'gh-one', 'repos', collectorId('github', 'repos', 'octo/keen')],
  );
  assert.equal(ui.field('owner').value, 'octo');
  assert.equal(ui.$('when-action_regex').value, '^push$');
  assert.equal(ui.$('confidence').value, '0.6');
  assert.equal(ui.$('enabled').checked, false);
  assert.equal(ui.$('input-picker').hidden, true);
  assert.equal(
    ui.$('selected-input').textContent,
    'Selected input: github repository “octo/keen” from Engineering GitHub. Choose another definition from the library to change inputs.',
  );
  assert.equal(ui.$('editor-heading').textContent, 'Edit Repository changes');
  assert.match(ui.$('targets').textContent, /A \/ A1/);

  await ui.click('close-editor');
  await open('Webhook reviews');
  assert.equal(ui.$('definition-adapter').value, 'webhooks');
  assert.equal(ui.$('definition-section').value, 'source');
  assert.equal(ui.$('when-source').value, 'webhook:github');
  assert.equal(
    ui.$('selected-input').textContent,
    'Selected input: All webhook:github events from all connections. Choose another definition from the library to change inputs.',
  );

  await ui.click('close-editor');
  await open('Retired log source');
  // A source that is no longer enabled stays selectable for its definition.
  assert.equal(ui.$('definition-adapter').selectedOptions[0].textContent, 'cloudwatch logs');
  assert.equal(ui.$('when-source').value, 'cloudwatch_logs');
});

test('restoring a revision reloads the rule and reopens it', async (t) => {
  const rule = {
    id: 'agent-logins',
    description: 'Agent logins',
    when: { source: 'keen-agent', action: 'login' },
    map_to: [{ framework: 'A', ref: 'A1' }],
  };
  const ui = await editor(t, {
    query: '?rule=agent-logins',
    rules: [rule, { ...rule, id: 'other', description: 'Other' }],
    revisions: {
      'agent-logins': [
        { revision: 2, version: 9, at: '2026-10-01T10:00:00' },
        { revision: 1, version: 5, at: '2026-09-01T10:00:00' },
      ],
    },
    restored: { 5: { ...rule, description: 'Agent sign-ins', when: { source: 'keen-agent' } } },
    confirm: false,
  });
  assert.equal(ui.$('rule-history').hidden, false);
  assert.deepEqual(
    [...ui.$('rule-revision').options].map((option) => [option.value, option.textContent]),
    [
      ['', "Select this rule's revision"],
      ['9', '#2 · 2026-10-01T10:00:00'],
      ['5', '#1 · 2026-09-01T10:00:00'],
    ],
  );
  assert.equal(ui.$('rule-restore').disabled, false);

  await ui.click('rule-restore');
  assert.deepEqual(ui.confirmations, [], 'nothing to restore without a chosen revision');
  ui.$('rule-revision').value = '5';
  await ui.click('rule-restore');
  assert.equal(ui.confirmations.length, 1);
  assert.deepEqual(ui.writes, []);

  ui.state.confirm = true;
  await ui.click('rule-restore');
  assert.deepEqual(ui.writes.at(-1), {
    method: 'POST',
    path: '/api/v1/admin/mapping-rules/agent-logins/revisions/5/restore',
    body: { version: 7 },
  });
  assert.equal(ui.$('description').value, 'Agent sign-ins');
  assert.equal(ui.$('when-action').value, '');
  assert.equal(
    ui.$('status').textContent,
    'Restored this rule. New evidence uses the restored settings; remap existing evidence separately.',
  );

  // Rules without saved revisions offer nothing to restore; new rules hide the history.
  await ui.click('close-editor');
  [...ui.$('rules').querySelectorAll('button')]
    .find((button) => button.textContent.startsWith('Other'))
    .click();
  await ui.idle();
  assert.equal(ui.$('rule-restore').disabled, true);
  await ui.click('new-rule');
  assert.equal(ui.$('rule-history').hidden, true);
});

test('saving with past evidence follows the historical run to completion', async (t) => {
  const ui = await editor(t, {
    saveBackfill: { id: 'job-1', status: 'queued' },
    jobs: {
      'job-1': [
        { id: 'job-1', status: 'running', examined: 10, total_estimate: 40, matched: 2 },
        {
          id: 'job-1',
          status: 'completed',
          examined: 40,
          total_estimate: 40,
          matched: 8,
          created_mappings: 3,
        },
      ],
    },
  });
  await ui.openInput('keen-agent');
  await ui.publish({ description: 'Agent evidence', predefine: true });

  assert.equal(
    ui.$('backfill').querySelector('span').textContent,
    'Historical evidence: running. Checked 10 of about 40 events; 2 matched, 0 new mappings.',
  );
  assert.ok(
    [...ui.$('backfill').querySelectorAll('button')].some((b) => b.textContent === 'Cancel'),
  );
  assert.equal(ui.$('step-number').textContent, '3');

  assert.equal(await ui.runPolls(), 1);
  assert.equal(
    ui.$('backfill').textContent,
    'Historical evidence: completed. Checked 40 of about 40 events; 8 matched, 3 new mappings. Automatic mappings have been reconciled with the saved rule.',
  );
  assert.equal(ui.$('backfill').querySelector('button'), null);
  assert.equal(
    ui.$('status').textContent,
    'Historical mapping complete: checked 40 events; 8 matched, 3 new mappings.',
  );
  assert.equal(await ui.runPolls(), 0, 'a finished run is not polled again');
});

test('a running historical run can be cancelled and stops polling when the editor closes', async (t) => {
  const running = { id: 'job-2', status: 'running', examined: 5, matched: 1 };
  const ui = await editor(t, {
    query: '?rule=rule-01',
    rules: rules(1),
    backfills: { 'rule-01': [running, { id: 'older', status: 'completed' }] },
    jobs: { 'job-2': [running] },
    confirm: false,
  });
  // Opening a definition shows its most recent run.
  assert.equal(
    ui.$('backfill').querySelector('span').textContent,
    'Historical evidence: running. Checked 5 of an estimated total being calculated events; 1 matched, 0 new mappings.',
  );
  const cancel = () => ui.$('backfill').querySelector('button');
  cancel().click();
  await ui.idle();
  assert.deepEqual(ui.writes, []);

  ui.state.confirm = true;
  cancel().click();
  await ui.idle();
  assert.deepEqual(ui.writes.at(-1).path, '/api/v1/admin/rule-backfills/job-2/cancel');
  assert.match(ui.$('backfill').textContent, /^Historical evidence: cancelled\./);
  assert.equal(cancel(), null);
  assert.ok(!ui.$('status').textContent.startsWith('Historical mapping complete'));

  // A poll queued before the editor closed does not fetch again.
  ui.state.jobs['job-2'] = [running];
  await ui.click('close-editor');
  const before = ui.calls.length;
  await ui.runPolls();
  assert.equal(ui.calls.length, before);
});

test('applying a saved rule to past evidence starts a run for that rule', async (t) => {
  const ui = await editor(t, {
    rules: rules(1),
    startBackfill: { id: 'job-3', status: 'queued' },
    jobs: {
      'job-3': [{ id: 'job-3', status: 'failed', examined: 3, matched: 0, error: 'Timed out' }],
    },
  });
  await ui.click('new-rule');
  await ui.click('backfill-start');
  assert.equal(ui.$('status').textContent, 'Save the rule first.');

  ui.$('rules').querySelector('button').click();
  await ui.idle();
  await ui.click('backfill-start');
  assert.deepEqual(
    ui.confirmations.map((c) => c.message),
    [
      'Evaluate all previous keen-agent evidence against saved rule rule-01? This runs in the background.',
    ],
  );
  assert.equal(ui.writes.at(-1).path, '/api/v1/admin/mapping-rules/rule-01/backfill');
  assert.equal(
    ui.$('backfill').textContent,
    'Historical evidence: failed. Checked 3 of an estimated total being calculated events; 0 matched, 0 new mappings. Error: Timed out',
  );

  ui.state.startBackfill = { status: 'no_previous_evidence' };
  await ui.click('backfill-start');
  assert.equal(
    ui.$('backfill').textContent,
    'No previously collected evidence to evaluate. The rule will apply to future events.',
  );
});

test('shared adapter settings load per adapter and save with their revision', async (t) => {
  const ui = await editor(t, {
    settings: { rss: { settings: { user_agent: 'Keen' }, version: 3 } },
  });
  assert.equal(ui.$('settings-adapter').value, 'jenkins');
  assert.deepEqual(
    [...ui.$('settings-adapter').options].slice(0, 3).map((o) => [o.value, o.textContent]),
    [
      ['jenkins', 'jenkins'],
      ['loki', 'loki'],
      ['rss', 'rss'],
    ],
  );
  await ui.choose('settings-adapter', 'rss');
  assert.deepEqual(JSON.parse(ui.$('settings-json').value), { user_agent: 'Keen' });

  ui.$('settings-json').value = '{"user_agent": "Keen audit"}';
  await ui.click('save-settings');
  assert.deepEqual(ui.writes.at(-1), {
    method: 'PUT',
    path: '/api/v1/admin/adapter-settings/rss',
    body: { version: 3, settings: { user_agent: 'Keen audit' } },
  });
  assert.equal(ui.$('status').textContent, 'Saved shared rss settings as revision 4.');
  // The saved revision becomes the next concurrency token.
  ui.$('settings-json').value = '{}';
  await ui.click('save-settings');
  assert.equal(ui.writes.at(-1).body.version, 4);

  const saves = ui.writes.length;
  ui.$('settings-json').value = '{"user_agent": ';
  await ui.click('save-settings');
  assert.equal(ui.writes.length, saves);
  assert.equal(ui.$('status').getAttribute('role'), 'alert');
});

test('choosing a source from Sources shows its definitions', async (t) => {
  const ui = await editor(t, {
    rules: [...rules(2), { id: 'feed', description: 'Feed', when: { source: 'rss' }, map_to: [] }],
  });
  ui.$('rule-filter').value = 'Definition';
  ui.window.dispatchEvent(
    new ui.window.CustomEvent('keen-filter-source', { detail: { source: 'rss' } }),
  );
  await ui.idle();
  assert.equal(ui.$('browse').open, true);
  assert.equal(ui.$('source-filter').value, 'rss');
  assert.equal(ui.$('rule-filter').value, '');
  assert.deepEqual(
    ui.library().map(([title]) => title),
    ['Feed'],
  );

  // A source without definitions still becomes the filter.
  ui.window.dispatchEvent(
    new ui.window.CustomEvent('keen-filter-source', { detail: { source: 'jenkins' } }),
  );
  await ui.idle();
  assert.equal(ui.$('source-filter').value, 'jenkins');
  assert.deepEqual(ui.library(), [['No evidence definitions yet. Create one to get started.']]);
});

test("an integration rule starts from the integration's source and name", async (t) => {
  const ui = await editor(t);
  ui.window.dispatchEvent(
    new ui.window.CustomEvent('keen-integration-rule', {
      detail: { source: 'integration:okta', name: 'Okta sign-ins' },
    }),
  );
  await ui.idle();
  assert.equal(ui.$('editor').hidden, false);
  assert.equal(ui.$('definition-adapter').value, 'integration:okta');
  assert.equal(ui.$('description').value, 'Okta sign-ins');
  assert.equal(ui.$('when-source').value, 'integration:okta');
});

test("exporting rules downloads the selected source's YAML", async (t) => {
  const downloads = [];
  let ok = true;
  const ui = await editor(t, {
    rules: [{ id: 'feed', when: { source: 'rss' }, map_to: [] }],
    setup(window) {
      window.HTMLAnchorElement.prototype.click = function () {
        downloads.push([this.download, this.href.startsWith('blob:')]);
      };
    },
    globals: {
      fetch: async (url, options) => {
        downloads.push([url, options.credentials]);
        return { ok, blob: async () => new Blob(['rules: []']) };
      },
    },
  });
  await ui.choose('source-filter', 'rss');
  await ui.click('export-rules');
  assert.deepEqual(downloads, [
    ['/api/v1/admin/mapping-rules/export.yaml?source=rss', 'same-origin'],
    ['keen-rules-rss.yaml', true],
  ]);
  assert.equal(ui.$('export-rules').disabled, false);

  await ui.choose('source-filter', '');
  await ui.click('export-rules');
  assert.deepEqual(downloads.at(-1), ['keen-rules-all.yaml', true]);

  ok = false;
  await ui.click('export-rules');
  assert.equal(
    ui.$('status').textContent,
    'Could not export mapping rules. Check your session and permissions.',
  );
  assert.equal(ui.$('export-rules').disabled, false);
});

// --- Step checks, samples, previews and refused saves --------------------------------

test('each step refuses to move on until its part of the rule is complete', async (t) => {
  const url = 'https://status.example/feed.xml';
  const ui = await editor(t, {
    sources: ['rss'],
    collectors: [collection('rss', 'feeds', url, { name: 'Status page', url })],
  });
  await ui.openInput('rss', 'env:rss', 'feeds', collectorId('rss', 'feeds', url));
  // Unusable collection settings keep the editor on the first step.
  ui.$('definition-extra').value = '[1]';
  await ui.click('next-step');
  assert.equal(ui.$('step-number').textContent, '1');
  assert.equal(ui.$('status').textContent, 'Additional collection settings must be a JSON object.');
  assert.equal(ui.$('status').getAttribute('role'), 'alert');

  ui.$('definition-extra').value = '{}';
  await ui.click('next-step');
  assert.equal(ui.$('step-number').textContent, '2');
  assert.equal(ui.$('status').hidden, true);

  // The description comes before the controls.
  ui.$('description').value = '   ';
  await ui.click('next-step');
  assert.equal(ui.$('step-number').textContent, '2');
  assert.equal(
    ui.$('status').textContent,
    'Describe what this evidence demonstrates before continuing.',
  );
  assert.equal(ui.document.activeElement, ui.$('description'));
  ui.$('description').value = 'Status page incidents';
  await ui.click('next-step');
  assert.equal(
    ui.$('status').textContent,
    'Add at least one framework control or clause before continuing.',
  );
  ui.$('control').value = 'A1';
  ui.$('add-target').click();
  await ui.click('next-step');
  assert.equal(ui.$('step-number').textContent, '3');
  assert.equal(ui.$('status').hidden, true);
});

test('Use this event fills the identifying fields and proposes a rule id', async (t) => {
  const ui = await editor(t, {
    samples: [
      {
        id: 'e1',
        source: 'keen-agent',
        system: 'Build Farm',
        action: 'deploy.finished',
        outcome: 'OK',
        actor: 'alice',
        severity: 2,
        summary: 'Deployed <b>keen</b>',
      },
    ],
  });
  await ui.openInput('keen-agent');
  assert.equal(ui.$('sample').options.length, 2);
  // Nothing happens until an event is chosen.
  await ui.click('use-sample');
  assert.equal(ui.$('when-action').value, '');
  await ui.choose('sample', '0');
  assert.equal(
    ui.$('sample-details').textContent,
    'Which system? (optional): Build Farm · What happened? (action): deploy.finished · ' +
      'What was the result? (outcome): OK · Who did it? (actor): alice · Severity number: 2',
  );
  await ui.click('use-sample');
  assert.deepEqual(
    ['system', 'action', 'outcome', 'actor'].map((f) => ui.$('when-' + f).value),
    ['Build Farm', 'deploy.finished', 'OK', ''],
  );
  assert.equal(ui.$('rule-id').value, 'keen-agent_build_farm_deploy_finished_ok');
  assert.equal(
    ui.$('preview-results').textContent,
    'Review the filled fields, select framework targets, then preview matches.',
  );
  assert.equal(ui.$('preview-results').querySelector('b'), null);
  // A rule id the administrator typed is kept.
  ui.$('rule-id').value = 'my_rule';
  ui.$('when-outcome').value = '';
  await ui.click('use-sample');
  assert.equal(ui.$('rule-id').value, 'my_rule');
  assert.equal(ui.$('when-outcome').value, 'OK');
});

test('the preview lists matching and non-matching samples as text', async (t) => {
  const ui = await editor(t, {
    preview: {
      examined: 3,
      matched: 1,
      note: 'Newest first.',
      examples: [{ summary: hostile, targets: { A: ['A1', 'A2'], B: ['B9'] } }],
      nonmatches: [
        { summary: 'Quiet night', reasons: ['Outcome differs.', 'Actor differs.'] },
        { summary: 'Blank entry' },
      ],
    },
  });
  await ui.openInput('keen-agent');
  ui.$('description').value = 'Agent evidence';
  await ui.click('preview');
  const results = [...ui.$('preview-results').children].map((p) => [p.className, p.textContent]);
  assert.deepEqual(results, [
    ['', '1 of 3 recent keen-agent events matched. Newest first.'],
    ['alert alert-success py-2 mb-2', `Matches: ${hostile}Maps to Framework A (A1, A2); B (B9)`],
    [
      'alert alert-warning py-2 mb-2',
      'Does not match: Quiet night. Outcome differs. Actor differs.',
    ],
    [
      'alert alert-warning py-2 mb-2',
      'Does not match: Blank entry. Review the additional filters.',
    ],
  ]);
  // The targets sit on their own line; a framework the picker does not list keeps its slug.
  assert.equal(
    ui.$('preview-results').querySelector('.alert-success .small').textContent,
    'Maps to Framework A (A1, A2); B (B9)',
  );
  assert.equal(ui.$('preview-results').querySelector('img'), null);
});

test('a field condition without a chosen field cannot be previewed', async (t) => {
  const ui = await editor(t);
  await ui.openInput('keen-agent');
  ui.$('description').value = 'Agent evidence';
  ui.$('field-add').click();
  const row = ui.$('field-rows').lastElementChild;
  row.fieldInputs.value.value = 'sshd';
  assert.equal(row.fieldInputs.path.value, '');
  await ui.click('preview');
  assert.equal(ui.$('preview-results').textContent, 'Choose an event field.');
  assert.deepEqual(ui.writes, []);
});

test('saving needs a preview of this version, and no evidence asks for confirmation', async (t) => {
  const ui = await editor(t, {
    preview: { examined: 0, matched: 0, examples: [], nonmatches: [] },
    confirm: false,
  });
  await ui.openInput('keen-agent');
  ui.$('description').value = 'Agent evidence';
  ui.$('control').value = 'A1';
  ui.$('add-target').click();
  await ui.click('save-rule');
  assert.equal(
    ui.$('status').textContent,
    'Preview this version of the rule before saving, or tick “Predefine this rule before evidence is available” under Advanced rule settings.',
  );
  assert.deepEqual(ui.writes, []);

  await ui.click('preview');
  await ui.click('save-rule');
  assert.deepEqual(
    ui.confirmations.map((c) => c.message),
    ['There is no recent evidence for this source. Save the rule without a sample match?'],
  );
  assert.deepEqual(
    ui.writes.map((w) => w.method),
    ['POST'],
  );

  // Editing the rule after the preview makes the preview stale again.
  ui.state.confirm = true;
  ui.$('description').value = 'Agent evidence, revised';
  await ui.click('save-rule');
  assert.match(ui.$('status').textContent, /Preview this version/);
  await ui.click('preview');
  ui.state.failSave = '{"detail":[{"msg":"Rule id is taken"},{"msg":"Pick another"}]}';
  await ui.click('save-rule');
  assert.equal(ui.$('status').textContent, 'Rule id is taken; Pick another');
  assert.equal(ui.$('status').getAttribute('role'), 'alert');
  ui.state.failSave = '{"detail":"Rules changed in another session"}';
  await ui.click('save-rule');
  assert.equal(ui.$('status').textContent, 'Rules changed in another session');
  ui.state.failSave = '{not json';
  await ui.click('save-rule');
  assert.equal(ui.$('status').textContent, '{not json');
});

test('a legacy summary regex is saved unchanged without a structured condition', async (t) => {
  const ui = await editor(t, {
    rules: [
      {
        id: 'legacy',
        description: 'Legacy summary rule',
        when: { source: 'keen-agent', summary_regex: '^(?:gills|systemd)$' },
        map_to: [{ framework: 'A', ref: 'A1' }],
      },
    ],
  });
  ui.$('rules').querySelector('button').click();
  await ui.idle();
  const row = ui.$('field-rows').firstElementChild;
  assert.equal(row.fieldInputs.op.value, 'regex');
  assert.equal(row.fieldInputs.value.value, '^(?:gills|systemd)$');
  await ui.click('preview');
  const [preview] = ui.writes;
  assert.deepEqual(preview.body.rule.when, {
    source: 'keen-agent',
    summary_regex: '^(?:gills|systemd)$',
  });
  assert.equal('fields' in preview.body.rule.when, false);

  // A removed pattern drops the legacy field altogether.
  row.fieldInputs.value.value = '';
  await ui.click('preview');
  assert.deepEqual(ui.writes.at(-1).body.rule.when, { source: 'keen-agent' });
});

test('control suggestions need a description, say when nothing fits and report failures', async (t) => {
  const ui = await editor(t, { suggestions: [] });
  await ui.openInput('keen-agent');
  await ui.click('suggest-controls');
  assert.equal(ui.$('status').textContent, 'Choose a framework and describe the evidence first.');
  assert.equal(ui.$('status').getAttribute('role'), 'status');
  assert.deepEqual(ui.writes, []);

  ui.$('description').value = 'Deployment approvals';
  await ui.click('suggest-controls');
  assert.equal(
    ui.$('control-suggestions').textContent,
    'No clear matches in this framework. Search the control list or try a more specific description.',
  );
  assert.deepEqual(ui.writes.at(-1).body, {
    framework: 'A',
    description: 'Deployment approvals',
    sample_summary: '',
  });

  ui.state.suggestions = [{ ref: 'A1', title: 'Control A', reason: 'Mentions approvals.' }];
  await ui.click('suggest-controls');
  const [button] = ui.$('control-suggestions').querySelectorAll('button');
  assert.equal(button.querySelector('strong').textContent, 'A1 · Control A');
  // Typing in the description clears stale suggestions.
  ui.$('description').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.$('control-suggestions').children.length, 0);

  ui.state.failSuggestions = 'Suggestions are unavailable';
  await ui.click('suggest-controls');
  assert.equal(ui.$('status').textContent, 'Suggestions are unavailable');
  assert.equal(ui.$('control-suggestions').children.length, 0);
});

test('a refused historical run is reported, and only enabled live integrations are sources', async (t) => {
  const ui = await editor(t, {
    rules: rules(1),
    failBackfill: 'Backfill already running',
    integrations: [
      { id: 'okta', name: 'Okta sign-ins', enabled: true, live_revision: 3 },
      { id: 'draft', name: 'Draft integration', enabled: true, live_revision: null },
      { id: 'paused', name: 'Paused integration', enabled: false, live_revision: 2 },
    ],
  });
  const adapters = [...ui.$('definition-adapter').options].map((o) => [o.value, o.textContent]);
  assert.ok(
    adapters.some(([value, text]) => value === 'integration:okta' && text === 'Okta sign-ins'),
  );
  assert.ok(!adapters.some(([value]) => value === 'integration:draft'));
  assert.ok(!adapters.some(([value]) => value === 'integration:paused'));

  ui.$('rules').querySelector('button').click();
  await ui.idle();
  await ui.click('backfill-start');
  assert.deepEqual(
    ui.confirmations.map((c) => c.message),
    [
      'Evaluate all previous keen-agent evidence against saved rule rule-01? This runs in the background.',
    ],
  );
  assert.equal(ui.$('status').textContent, 'Backfill already running');
  assert.equal(ui.$('status').getAttribute('role'), 'alert');

  // A refused deletion from the editor is reported the same way.
  ui.state.failDelete = 'Rules changed in another session. Reload before saving.';
  await ui.click('delete-rule');
  assert.equal(ui.$('status').textContent, ui.state.failDelete);
  assert.equal(ui.$('editor').hidden, false);
});

test('a webhook definition names its provider and locks a saved one', async (t) => {
  const provider = { ui_name: 'Pager', badge_color: '#123456', secret_header: 'X-Sig' };
  const fresh = await editor(t, { sources: ['webhooks', 'keen-agent'] });
  await fresh.openInput('webhooks', 'env:webhooks', 'providers');
  assert.equal(fresh.field('provider_name').value, '');
  assert.equal(fresh.field('provider_name').readOnly, false);
  assert.equal(fresh.$('when-source').value, 'webhook:');

  const saved = await editor(t, {
    sources: ['webhooks', 'keen-agent'],
    collectors: [collection('webhooks', 'providers', 'pager', provider)],
  });
  await saved.openInput('webhooks', 'env:webhooks', 'providers');
  assert.equal(saved.$('definition-entry').value, collectorId('webhooks', 'providers', 'pager'));
  assert.equal(saved.field('provider_name').value, 'pager');
  assert.equal(saved.field('provider_name').readOnly, true);
  assert.equal(saved.field('ui_name').value, 'Pager');
  assert.equal(saved.$('when-source').value, 'webhook:pager');
});

test('editing a mapping warns before leaving and clears on returning to the library', async (t) => {
  const ui = await editor(t, { rules: rules(1) });
  const leave = () => {
    const event = new ui.window.Event('beforeunload', { cancelable: true });
    ui.window.dispatchEvent(event);
    return event.defaultPrevented;
  };
  await ui.click('new-rule');
  assert.equal(leave(), false);
  assert.equal(ui.$('unsaved').hidden, true);

  ui.$('description').value = 'Half-finished mapping';
  ui.$('description').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(ui.$('unsaved').hidden, false);
  assert.equal(leave(), true);

  await ui.click('close-editor');
  assert.equal(ui.$('unsaved').hidden, true);
  assert.equal(leave(), false);
});
