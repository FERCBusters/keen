// The evidence mapping editor (pages/evidence-config.js) assembled on the
// Admin page, with a small in-memory server standing in for the administrator
// API. Tests change `state` to shape what the server holds and returns, and
// `state.gate(method, path)` may return a promise that holds one request.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './load-ui.mjs';

const html = await readFile(new URL('../../public/admin.html', import.meta.url), 'utf8');

export const hostile = '<img src=x onerror=alert(1)>';
export const collectorId = (adapter, section, key) =>
  JSON.stringify([adapter, section, String(key)]);
export function collection(adapter, section, key, entry, connectionId) {
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
export function rules(count, extra = {}) {
  return Array.from({ length: count }, (_, i) => ({
    id: 'rule-' + String(i + 1).padStart(2, '0'),
    description: 'Definition ' + String(i + 1).padStart(2, '0'),
    when: { source: 'keen-agent' },
    map_to: [{ framework: 'A', ref: 'A1' }],
    ...extra,
  }));
}
// A promise and the function that settles it, to hold a request mid-flight.
export function deferred() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}
function page(items, offset, size) {
  return {
    items: items.slice(offset, offset + size),
    next_offset: offset + size < items.length ? offset + size : null,
  };
}

export async function editor(t, server = {}) {
  const state = {
    sources: ['keen-agent'],
    observedSources: [],
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
    revisions: {},
    backfills: {},
    jobs: {},
    saveBackfill: null,
    startBackfill: null,
    preview: { examined: 1, matched: 1, examples: [], nonmatches: [] },
    samples: [],
    patternSamples: [],
    suggestions: [],
    integrations: [],
    frameworks: [{ slug: 'A', name: 'Framework A' }],
    controls: [{ ref: 'A1', title: 'Control A' }],
    events: {},
    confirm: true,
    gate: () => null,
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
      await state.gate(method, url.pathname, url);
      return await handler(url, body);
    } finally {
      pending--;
    }
  };
  const get = tracked('GET', async (url) => {
    const path = url.pathname,
      query = url.searchParams;
    if (path === '/api/v1/admin/evidence-event-samples') {
      if (!query.get('source') && !query.get('source_regex'))
        return { sources: structuredClone(state.observedSources), items: [] };
      return {
        items: structuredClone(query.get('source_regex') ? state.patternSamples : state.samples),
      };
    }
    if (path === '/api/v1/admin/source-catalogue')
      return { items: state.sources.map((adapter) => ({ adapter, enabled: true })) };
    if (path === '/api/v1/admin/integrations')
      return { collectors: structuredClone(state.integrations) };
    if (path === '/api/v1/admin/source-connections')
      return { connections: structuredClone(state.connections), types: [] };
    if (path === '/api/v1/admin/evidence-definitions')
      return structuredClone({
        rules: state.rules,
        collectors: state.collectors,
        rules_version: state.rulesVersion,
        warnings: state.warnings || [],
      });
    if (path.startsWith('/api/v1/admin/managed-configurations/')) {
      if (state.failConfiguration) throw new Error(state.failConfiguration);
      return structuredClone(
        state.documents[decodeURIComponent(path.split('/').pop())] || {
          document: {},
          version: 1,
        },
      );
    }
    if (path.startsWith('/api/v1/admin/adapter-settings/'))
      return structuredClone(
        state.settings[decodeURIComponent(path.split('/').pop())] || { settings: {}, version: 1 },
      );
    if (path === '/api/v1/frameworks') return { items: structuredClone(state.frameworks) };
    if (path === '/api/v1/controls') {
      const controls =
        typeof state.controls === 'function'
          ? state.controls(query.get('framework'))
          : state.controls;
      return { items: structuredClone(controls) };
    }
    if (path === '/api/v1/admin/bookstack/catalog') {
      const kind = query.get('kind'),
        offset = Number(query.get('offset')),
        book = query.get('book_id');
      const items =
        kind === 'books' ? state.books : (kind === 'chapters' ? state.chapters : state.pages)[book];
      return page(items || [], offset, state.catalogueSize);
    }
    const revisions = path.match(/^\/api\/v1\/admin\/mapping-rules\/([^/]+)\/revisions$/);
    if (revisions) return { items: state.revisions[decodeURIComponent(revisions[1])] || [] };
    if (path === '/api/v1/admin/rule-backfills')
      return { items: state.backfills[query.get('rule_id')] || [] };
    if (path === '/api/v1/admin/mapping-rules/insights') {
      if (state.failInsights) throw new Error(state.failInsights);
      return structuredClone(
        state.insights || {
          version: state.rulesVersion,
          days: 90,
          rules: [],
          consolidation: [],
          overlaps: [],
        },
      );
    }
    const job = path.match(/^\/api\/v1\/admin\/rule-backfills\/([^/]+)$/);
    if (job) {
      const sequence = state.jobs[decodeURIComponent(job[1])];
      return structuredClone(sequence.length > 1 ? sequence.shift() : sequence[0]);
    }
    const event = path.match(/^\/api\/v1\/events\/([^/]+)$/);
    if (event && state.events[decodeURIComponent(event[1])])
      return structuredClone(state.events[decodeURIComponent(event[1])]);
    throw new Error('Unexpected GET ' + url.pathname + url.search);
  });
  const post = tracked('POST', async (url) => {
    const path = url.pathname;
    if (path === '/api/v1/admin/mapping-rules/preview') return structuredClone(state.preview);
    if (path === '/api/v1/admin/control-suggestions')
      return { items: structuredClone(state.suggestions) };
    const backfill = path.match(/^\/api\/v1\/admin\/mapping-rules\/([^/]+)\/backfill$/);
    if (backfill) return { backfill: structuredClone(state.startBackfill) };
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
    if (state.failSave) throw new Error(state.failSave);
    if (path.startsWith('/api/v1/admin/evidence-definitions/')) {
      const rule = structuredClone(body.rule);
      state.rules = [...state.rules.filter((item) => item.id !== rule.id), rule];
      state.rulesVersion++;
      return {
        rules_version: state.rulesVersion,
        rule,
        backfill: structuredClone(state.saveBackfill),
      };
    }
    const saved = path.match(/^\/api\/v1\/admin\/mapping-rules\/([^/]+)$/);
    if (saved) {
      const rule = structuredClone(body.rule);
      state.rules = state.rules.some((item) => item.id === rule.id)
        ? state.rules.map((item) => (item.id === rule.id ? rule : item))
        : [...state.rules, rule];
      state.rulesVersion++;
      return {
        version: state.rulesVersion,
        item: rule,
        backfill: body.apply_existing ? structuredClone(state.saveBackfill) : null,
      };
    }
    throw new Error('Unexpected PUT ' + path);
  });
  const remove = tracked('DELETE', async (url) => {
    const rule = url.pathname.match(/^\/api\/v1\/admin\/mapping-rules\/([^/]+)$/);
    if (!rule) throw new Error('Unexpected DELETE ' + url.pathname);
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
      // Long timers (polls, expiring notices) wait until a test runs them.
      setTimeout: (fn, ms, ...args) => {
        if (ms < 5000) return setTimeout(fn, ms, ...args);
        delayed.push({ fn, ms });
        return -delayed.length;
      },
      clearTimeout: (id) => {
        if (typeof id === 'number' && id < 0) delayed[-id - 1].cancelled = true;
        else clearTimeout(id);
      },
    },
    vendor: {
      apiGet: get,
      apiPost: post,
      apiPut: put,
      apiDelete: remove,
      getCurrentFramework: () => state.activeFramework || 'A',
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
  // Opens a library entry by its title, as a click on it does.
  async function open(title) {
    const button = [...$('rules').querySelectorAll('button')].find(
      (b) => b.querySelector('strong')?.textContent === title,
    );
    assert.ok(button, `the library lists ${title}`);
    button.click();
    await idle();
  }
  async function type(suffix, value) {
    $(suffix).value = value;
    $(suffix).dispatchEvent(new ui.window.Event('input', { bubbles: true }));
    await idle();
  }
  // Runs the long timers due so far: historical-run polls and expiring notices.
  async function runTimers(ms) {
    const due = delayed.filter((timer) => timer.ms === ms && !timer.cancelled && !timer.ran);
    for (const timer of due) {
      timer.ran = true;
      timer.fn();
    }
    await idle();
    return due.length;
  }
  const field = (name) => $('definition-fields').querySelector(`[data-field="${name}"]`);
  await idle();
  return {
    ...ui,
    $,
    state,
    calls,
    writes,
    confirmations,
    delayed,
    idle,
    choose,
    click,
    openInput,
    open,
    type,
    runTimers,
    field,
  };
}
