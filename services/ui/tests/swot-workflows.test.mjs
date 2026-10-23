import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { page, settle } from './support/page-fixture.mjs';

// SWOT workflows on saved items, the workspace list and the Excel export.
const copy = (value) => JSON.parse(JSON.stringify(value ?? null));

function swot(overrides = {}) {
  return {
    id: 's1',
    title: 'Flood exposure',
    kind: 'threat',
    description: 'Ground floor server room',
    response: 'Relocate',
    significance: 4,
    status: 'planned',
    version: 2,
    links: [],
    controls: [],
    clauses: [],
    ...overrides,
  };
}

const CHOICE = {
  target_type: 'document',
  target_id: 'd1',
  title: 'Continuity plan',
  url: '/isms-document.html?id=d1',
};

async function detail(t, { item = swot(), get, ...options } = {}) {
  const state = { item: copy(item), detailLoads: 0, questionLoads: 0 };
  const ui = await page(t, 'isms-swot', {
    htmlName: 'swot',
    query: '&id=' + item.id,
    ...options,
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/swot/' + item.id) {
        state.detailLoads += 1;
        return copy(state.item);
      }
      if (u.pathname.startsWith('/api/v1/questions/entities/')) state.questionLoads += 1;
      if (get) {
        const answer = await get(u);
        if (answer !== undefined) return answer;
      }
      if (u.pathname === '/api/v1/isms/meta') return { users: [], controls: [], clauses: [] };
      if (u.pathname === '/api/v1/isms/swot') return { items: [copy(state.item)], total: 1 };
      if (u.pathname.endsWith('/changelog')) return { items: [] };
      if (u.pathname === '/api/v1/isms/swot-choices/objective') return { items: [] };
      if (u.pathname === '/api/v1/isms/swot-choices/document') return { items: [CHOICE] };
      if (u.pathname.startsWith('/api/v1/questions/entities/')) return { threads: [] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('pageTitle').textContent === item.title && state.questionLoads > 0);
  return { ...ui, state };
}

const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));

test('relationships on a saved item are added and removed with its version', async (t) => {
  const ui = await detail(t, {
    item: swot({ links: [{ ...CHOICE, target_id: 'old', title: 'Old plan' }] }),
    post: async (u, body) => ({ ok: true, path: u.pathname, body }),
    remove: async () => ({ ok: true }),
  });
  ui.el('linkType').value = 'document';
  ui.el('linkType').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('linkItem').options.length === 2);
  ui.el('linkItem').value = 'd1';
  const loads = ui.state.detailLoads;

  submit(ui, 'linkForm');

  await settle(() => ui.state.detailLoads === loads + 1);
  const post = ui.calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/v1/isms/swot/s1/links');
  assert.deepEqual(copy(post.body), {
    target_type: 'document',
    target_id: 'd1',
    expected_version: 2,
  });

  ui.el('relatedList').querySelector('[data-unlink-id="old"]').click();

  await settle(() => ui.state.detailLoads === loads + 2);
  assert.equal(
    ui.calls.find((c) => c.method === 'DELETE').url,
    '/api/v1/isms/swot/s1/links/document/old?expected_version=2',
  );
});

test('a relationship the server refuses is reported', async (t) => {
  const ui = await detail(t, {
    post: async () => {
      throw new Error('Item changed; reload');
    },
  });
  ui.el('linkType').value = 'document';
  ui.el('linkType').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('linkItem').options.length === 2);
  ui.el('linkItem').value = 'd1';

  submit(ui, 'linkForm');

  await settle(() => ui.el('status').textContent === 'Error: Item changed; reload');
  assert.equal(ui.el('status').dataset.alert, 'danger');
});

test('a question about the item is posted and the thread list refreshed', async (t) => {
  const ui = await detail(t, { post: async () => ({ id: 'q1' }) });
  ui.el('questionBody').value = 'Who owns the relocation?';
  const loads = ui.state.questionLoads;

  submit(ui, 'questionForm');

  await settle(() => ui.state.questionLoads === loads + 1);
  const post = ui.calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/v1/questions/entities/isms_swot/s1');
  assert.deepEqual(copy(post.body), { body: 'Who owns the relocation?' });
  assert.equal(ui.el('questionBody').value, '');
});

test('deleting an item asks first and returns to the workspace', async (t) => {
  const navigations = [];
  const location = {
    origin: 'https://keen.example',
    href: 'https://keen.example/swot.html?id=s1&framework=A',
    search: '?id=s1&framework=A',
    assign: (url) => navigations.push(url),
    replace: (url) => navigations.push(url),
  };
  let answer = false;
  const ui = await detail(t, {
    globals: { location, confirm: () => answer },
    remove: async () => ({}),
  });

  ui.el('deleteItem').click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(ui.calls.filter((c) => c.method === 'DELETE').length, 0);

  answer = true;
  ui.el('deleteItem').click();
  await settle(() => navigations.length === 1);
  assert.equal(
    ui.calls.find((c) => c.method === 'DELETE').url,
    '/api/v1/isms/swot/s1?expected_version=2',
  );
  assert.deepEqual(navigations, ['/swot.html?framework=A']);
});

test('saving a changed item reloads it and confirms the save', async (t) => {
  let saved;
  const ui = await detail(t, {
    patch: async (u, body) => {
      saved = copy(body);
      return { ok: true };
    },
  });
  ui.el('response').value = 'Move the servers upstairs';

  submit(ui, 'itemForm');

  await settle(() => ui.el('status').textContent === 'SWOT item saved.');
  assert.equal(saved.response, 'Move the servers upstairs');
  assert.equal(saved.expected_version, 2);
  assert.equal(ui.el('status').dataset.alert, 'success');
});

test('a parent beyond the first page of candidates is still shown', async (t) => {
  const ui = await detail(t, {
    item: swot({ parent_id: 'p9' }),
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/swot' && u.searchParams.get('limit') === '200')
        return { items: [{ id: 'p1', title: 'Nearby parent' }], total: 300 };
      if (u.pathname === '/api/v1/isms/swot/p9') return { id: 'p9', title: 'Distant parent' };
      return undefined;
    },
  });
  await settle(() => ui.el('parent').value === 'p9');
  assert.deepEqual(
    [...ui.el('parent').options].map((o) => [o.value, o.textContent]),
    [
      ['', 'Top level'],
      ['p1', 'Nearby parent'],
      ['p9', 'Distant parent'],
    ],
  );
});

async function workspace(t, { query = '', items = [], total = items.length, ...options } = {}) {
  const lists = [];
  const ui = await page(t, 'isms-swot', {
    htmlName: 'swot',
    query,
    ...options,
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/meta') {
        await options.onMeta?.();
        return {
          controls: [],
          clauses: [],
          users: [
            { id: 'u1', display_name: 'Olivia Owner' },
            { id: 'u2', username: 'sam' },
          ],
        };
      }
      if (u.pathname === '/api/v1/isms/swot' && u.searchParams.get('limit') === '100') {
        lists.push(u.searchParams);
        return { items, total };
      }
      if (options.get) return options.get(u);
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => lists.length > 0);
  return { ...ui, lists };
}

test('a link from a related record opens the workspace filtered to that record', async (t) => {
  // The record is not among the current choices, for example after its title
  // changed; the filter still applies to it.
  const ui = await workspace(t, {
    query: '&target_type=document&target_id=d9',
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/swot-choices/document') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  const related = ui.el('relatedItem');
  assert.equal(ui.el('relatedType').value, 'document');
  assert.equal(related.value, 'd9');
  assert.equal(related.selectedOptions[0].textContent, 'Selected record');
  assert.equal(related.disabled, false);
  assert.equal(ui.el('relatedItemField').hidden, false);
  assert.equal(ui.lists[0].get('target_type'), 'document');
  assert.equal(ui.lists[0].get('target_id'), 'd9');
});

test('the search box waits for a pause and the list pages in hundreds', async (t) => {
  const items = Array.from({ length: 100 }, (_, i) =>
    swot({ id: 's' + i, title: 'Item ' + i, status: 'defined' }),
  );
  const ui = await workspace(t, { items, total: 250 });
  assert.equal(
    ui.el('resultCount').textContent,
    'Showing 1-100 of 250 matching items. This view includes these items only; use Previous/Next to see more.',
  );
  assert.equal(ui.el('previous').disabled, true);
  assert.equal(ui.el('next').disabled, false);

  ui.el('next').click();
  await settle(() => ui.lists.length === 2);
  assert.equal(ui.lists[1].get('offset'), '100');
  await settle(() => ui.el('previous').disabled === false);
  ui.el('previous').click();
  await settle(() => ui.lists.length === 3);
  assert.equal(ui.lists[2].get('offset'), '0');
  ui.el('next').click();
  await settle(() => ui.lists.length === 4);

  for (const text of ['f', 'flo', 'flood']) {
    ui.el('search').value = text;
    ui.el('search').dispatchEvent(new ui.window.Event('input'));
  }
  assert.equal(ui.lists.length, 4);
  await settle(() => ui.lists.length === 5);
  await new Promise((resolve) => setTimeout(resolve, 300));
  // One request follows the pause, for the final text, from the first page.
  assert.equal(ui.lists.length, 5);
  assert.equal(ui.lists[4].get('q'), 'flood');
  assert.equal(ui.lists[4].get('offset'), '0');
});

test('a list that fits on one page offers no paging', async (t) => {
  const ui = await workspace(t, { items: [swot()], total: 1 });
  assert.equal(ui.el('resultCount').textContent, 'Showing all 1 matching items.');
  assert.equal(ui.el('previous').disabled, true);
  assert.equal(ui.el('next').disabled, true);
});

function exportFixture(release) {
  return async (u) => {
    if (u.pathname === '/api/v1/isms/swot' && u.searchParams.get('limit') === '200')
      return { items: [{ id: 's1' }], total: 1 };
    if (u.pathname === '/api/v1/isms/swot/s1') {
      await release?.();
      return swot({ owner_user_id: 'u1' });
    }
    throw new Error('Unexpected GET ' + u);
  };
}

test('Excel export collects the filtered items with their owners and downloads them', async (t) => {
  const downloads = [];
  const exports = [];
  const ui = await workspace(t, {
    setup(window) {
      window.HTMLAnchorElement.prototype.click = function () {
        downloads.push({ href: this.href, download: this.download });
      };
    },
    get: async (u) => {
      if (u.searchParams.get('limit') === '200') exports.push(u.searchParams);
      return exportFixture()(u);
    },
  });
  ui.el('kindFilter').value = 'threat';
  ui.el('search').value = 'flood';
  const blobs = [];
  const createObjectURL = URL.createObjectURL;
  URL.createObjectURL = (blob) => {
    blobs.push(blob);
    return 'blob:keen-export';
  };
  t.after(() => {
    URL.createObjectURL = createObjectURL;
  });

  ui.el('exportExcel').click();

  assert.equal(ui.el('exportExcel').disabled, true);
  assert.equal(ui.el('exportPdf').disabled, true);
  assert.equal(ui.el('cancelExport').hidden, false);
  await settle(() => downloads.length === 1);
  assert.equal(exports[0].get('kind'), 'threat');
  assert.equal(exports[0].get('q'), 'flood');
  assert.equal(exports[0].get('framework'), 'A');
  assert.match(downloads[0].download, /^swot-\d{4}-\d{2}-\d{2}\.xlsx$/);
  assert.equal(downloads[0].href, 'blob:keen-export');
  // The stored workbook names the owner instead of their account id.
  const workbook = Buffer.from(await blobs[0].arrayBuffer()).toString('latin1');
  assert.match(workbook, /Olivia Owner/);
  assert.equal(ui.el('status').textContent, 'Exported 1 matching SWOT items.');
  assert.equal(ui.el('exportExcel').disabled, false);
  assert.equal(ui.el('cancelExport').hidden, true);
});

for (const [how, stage] of [
  ['Cancel', 'collecting items'],
  ['leaving the page', 'collecting items'],
  ['Cancel', 'naming owners'],
])
  test(`an export stops on ${how} while ${stage}`, async (t) => {
    const downloads = [];
    let release;
    const waiting = new Promise((resolve) => (release = resolve));
    let reached;
    const pause = new Promise((resolve) => (reached = resolve));
    const hold = async () => {
      reached();
      await waiting;
    };
    let metaRequests = 0;
    const ui = await workspace(t, {
      setup(window) {
        window.HTMLAnchorElement.prototype.click = () => downloads.push('download');
      },
      get: exportFixture(stage === 'collecting items' ? hold : null),
      onMeta: stage === 'naming owners' ? () => (++metaRequests === 2 ? hold() : null) : null,
    });

    ui.el('exportExcel').click();
    await pause;
    if (how === 'Cancel') ui.el('cancelExport').click();
    else ui.window.dispatchEvent(new ui.window.Event('pagehide'));
    release();

    await settle(() => ui.el('status').textContent === 'Error: Export cancelled.');
    assert.equal(ui.el('status').dataset.alert, 'danger');
    assert.deepEqual(downloads, []);
    assert.equal(ui.el('exportExcel').disabled, false);
    assert.equal(ui.el('cancelExport').hidden, true);
  });

test('leaving a new item with unsaved edits asks for confirmation', async (t) => {
  const ui = await page(t, 'isms-swot', {
    htmlName: 'swot',
    query: '&new=1',
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/meta') return { users: [], controls: [], clauses: [] };
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (u.pathname === '/api/v1/isms/swot-choices/objective') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('pageTitle').textContent === 'New SWOT item');
  const leave = () => {
    const event = new ui.window.Event('beforeunload', { cancelable: true });
    ui.window.dispatchEvent(event);
    return event.defaultPrevented;
  };
  assert.equal(leave(), false);

  ui.el('title').value = 'Supplier concentration';
  ui.el('itemForm').dispatchEvent(new ui.window.Event('input', { bubbles: true }));

  assert.equal(leave(), true);
});

// --- Refused actions, sampling and load failures -------------------------------------

test('refused unlinking, questions, replies and deletion are reported and leave the item as it was', async (t) => {
  const refuse = async () => {
    throw new Error('Item changed; reload');
  };
  const ui = await detail(t, {
    item: swot({
      links: [{ ...CHOICE, target_type: 'document' }],
    }),
    me: { user_id: 'u7', is_admin: true, can_create_questions: true },
    get: (u) =>
      u.pathname.startsWith('/api/v1/questions/entities/')
        ? {
            threads: [
              {
                id: 'q1',
                status: 'unanswered',
                created_by_user_id: 'u7',
                posts: [{ author_username: 'erin', body: 'Why?' }],
              },
            ],
          }
        : undefined,
    globals: { confirm: () => true },
    post: refuse,
    remove: refuse,
  });
  const reported = () => ui.el('status').textContent === 'Error: Item changed; reload';
  const reset = () => (ui.el('status').textContent = '');

  ui.el('relatedList').querySelector('[data-unlink-id="d1"]').click();
  await settle(reported);
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.ok(ui.el('relatedList').querySelector('[data-unlink-id="d1"]'));
  reset();

  ui.el('questionBody').value = 'Who owns this?';
  submit(ui, 'questionForm');
  await settle(reported);
  assert.equal(ui.el('questionBody').value, 'Who owns this?');
  reset();

  const reply = ui.el('questionList').querySelector('[data-reply-thread="q1"]');
  reply.elements.body.value = 'Facilities';
  reply.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await settle(reported);
  reset();

  ui.el('deleteItem').click();
  await settle(reported);
  assert.equal(ui.el('pageTitle').textContent, 'Flood exposure');
  assert.deepEqual(
    ui.calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
    [
      '/api/v1/isms/swot/s1/links/document/d1?expected_version=2',
      '/api/v1/isms/swot/s1?expected_version=2',
    ],
  );
});

test('a SWOT item is sampled into an audit with a link back to it', async (t) => {
  const ui = await detail(t, {
    me: { is_admin: true, features: { enabled: { sampling: true } } },
    get: (u) =>
      u.pathname === '/api/v1/audits'
        ? { items: [{ id: 'au1', title: 'Annual audit', status: 'open' }] }
        : undefined,
    post: async () => ({}),
  });
  assert.equal(ui.el('sample').hidden, false);
  ui.el('sample').click();
  await settle(() => ui.el('entityAuditSampleSelect')?.value === 'au1');
  assert.equal(ui.el('entityAuditSampleTitle').textContent, 'Sample “Flood exposure” into audit');
  ui.el('entityAuditSampleSubmit').click();
  await settle(() => ui.calls.some((c) => c.method === 'POST'));
  const post = ui.calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/v1/audits/au1/evidence');
  assert.deepEqual(copy(post.body), {
    entity_type: 'isms_swot',
    entity_id: 's1',
    evidence_url: '/swot.html?id=s1&framework=A',
    title: 'Flood exposure',
    notes: '',
  });
});

test('an item that cannot be loaded is reported, and an invalid field returns to Details', async (t) => {
  const shown = [];
  const ui = await page(t, 'isms-swot', {
    htmlName: 'swot',
    query: '&id=s1&tab=related',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/swot/s1') throw new Error('404 Not found');
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('status').textContent === 'Error: 404 Not found');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.deepEqual(shown, []);

  ui.el('title').dispatchEvent(new ui.window.Event('invalid', { bubbles: false }));
  assert.deepEqual(shown, ['detailsTab']);
});

test('a new item draws its draft relationships when the visualisation tab opens', async (t) => {
  const ui = await page(t, 'isms-swot', {
    htmlName: 'swot',
    query: '&new=1',
    setup(window) {
      window.d3 = d3;
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/meta') return { users: [], controls: [], clauses: [] };
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (u.pathname === '/api/v1/isms/swot-choices/objective') return { items: [] };
      if (u.pathname === '/api/v1/isms/swot-choices/document') return { items: [CHOICE] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('linkItem').options.length >= 1);
  ui.el('title').value = 'Draft <title>';
  ui.el('linkType').value = 'document';
  ui.el('linkType').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('linkItem').options.length === 2);
  ui.el('linkItem').value = 'd1';
  submit(ui, 'linkForm');

  ui.el('visualisationTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));

  const graph = ui.el('graph');
  assert.ok(graph.querySelector('svg'));
  assert.match(graph.textContent, /Draft <title>/);
  assert.equal(
    graph.querySelector('svg a').getAttribute('aria-label'),
    'Document: Continuity plan',
  );
  assert.equal(ui.calls.filter((c) => c.method !== 'GET').length, 0);
});
