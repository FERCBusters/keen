import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { page, settle } from './support/page-fixture.mjs';

// The SWOT workspace: its other views, mapping filters, paging past the end and
// exports, with list and meta responses shaped as services/api/app/services sends them.
function swot(overrides = {}) {
  return {
    id: 's1',
    title: 'Flood <b>exposure</b>',
    description: 'Ground floor server room',
    response: 'Relocate',
    kind: 'threat',
    significance: 4,
    status: 'planned',
    parent_id: null,
    owner_user_id: null,
    start_date: '2026-10-01',
    target_date: '2026-11-01',
    version: 2,
    created_at: '2026-09-01T00:00:00',
    updated_at: '2026-09-02T00:00:00',
    ...overrides,
  };
}

const fullMeta = {
  framework: 'A',
  section: 'all',
  can_view: true,
  can_manage: true,
  users: [{ id: 'u1', username: 'olivia', email: 'olivia@example.test' }],
  controls: [
    { id: 'c1', framework: 'A', type: 'control', ref: 'A.5', title: null, in_scope: true },
    { id: 'c2', framework: 'A', type: 'control', ref: 'A.8', title: 'Assets', in_scope: true },
  ],
  clauses: [{ id: 'k1', framework: 'A', ref: '4.1', title: 'Context' }],
};
// With the ISMS sections that list people, controls and clauses switched off,
// isms_meta leaves those keys out.
const sparseMeta = { framework: 'A', section: 'all', can_view: true, can_manage: true };

async function workspace(
  t,
  { me = { is_admin: true }, meta = fullMeta, pages, get, ...options } = {},
) {
  const lists = [];
  const ui = await page(t, 'swot', {
    me,
    ...options,
    setup(window) {
      window.d3 = d3;
      options.setup?.(window);
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/meta') return structuredClone(meta);
      if (u.pathname === '/api/v1/isms/swot' && u.searchParams.get('limit') === '100') {
        lists.push(u.searchParams);
        return structuredClone(
          pages ? pages(lists.length, u) : { items: [swot()], total: 1, limit: 100, offset: 0 },
        );
      }
      if (get) return get(u);
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => lists.length > 0 && ui.el('resultCount').textContent);
  return { ...ui, lists };
}

const choose = (ui, id, value) => {
  ui.el(id).value = value;
  ui.el(id).dispatchEvent(new ui.window.Event('change'));
};

test('the workspace switches to the timeline and hierarchy views and back to the board', async (t) => {
  const ui = await workspace(t, {
    pages: () => ({
      items: [
        swot(),
        swot({ id: 's2', title: 'Undated child', parent_id: 's1', target_date: null }),
      ],
      total: 2,
    }),
  });
  choose(ui, 'view', 'timeline');
  const host = ui.el('planningView');
  assert.equal(host.className, 'planning-timeline');
  assert.match(host.textContent, /1 items with target dates are plotted; 1 without a target date/);
  assert.equal(host.querySelectorAll('rect').length, 1);
  assert.equal(ui.el('planningHint').hidden, true);

  choose(ui, 'view', 'hierarchy');
  assert.equal(host.className, '');
  const links = [...host.querySelectorAll('a')];
  assert.deepEqual(
    links.map((a) => a.textContent),
    ['Flood <b>exposure</b> · Threat · 4/5', 'Undated child · Threat · 4/5'],
  );
  assert.equal(host.querySelector('b'), null);
  // The child sits under its parent.
  assert.equal(links[1].closest('ul').closest('li').querySelector('a'), links[0]);

  choose(ui, 'view', 'kanban');
  assert.ok(host.querySelector('[data-status-id="s1"]'));
  assert.equal(ui.el('planningHint').hidden, false);
});

test('readers see each status as text on the board, with no way to move items', async (t) => {
  const ui = await workspace(t, { me: { can_view_isms: true } });
  const host = ui.el('planningView');
  assert.equal(host.querySelector('[data-status-id]'), null);
  assert.equal(host.querySelector('[draggable]'), null);
  choose(ui, 'view', 'kanban');
  assert.equal(host.querySelector('[data-status-id]'), null);
  assert.match(host.textContent, /Planned/);
  assert.equal(ui.el('planningHint').hidden, true);
  assert.equal(ui.el('newItem').hidden, true);
});

test('control and clause filters list references and send the chosen reference', async (t) => {
  const ui = await workspace(t);
  assert.deepEqual(
    [...ui.el('controlFilter').options].map((o) => [o.value, o.textContent]),
    [
      ['', 'All controls'],
      ['A.5', 'A.5'],
      ['A.8', 'A.8 Assets'],
    ],
  );
  assert.deepEqual(
    [...ui.el('clauseFilter').options].map((o) => [o.value, o.textContent]),
    [
      ['', 'All clauses'],
      ['4.1', '4.1 Context'],
    ],
  );
  assert.equal(ui.lists[0].has('control'), false);
  assert.equal(ui.lists[0].has('clause'), false);
  choose(ui, 'controlFilter', 'A.5');
  await settle(() => ui.lists.length === 2);
  assert.equal(ui.lists[1].get('control'), 'A.5');
  assert.equal(ui.lists[1].has('clause'), false);
  choose(ui, 'clauseFilter', '4.1');
  await settle(() => ui.lists.length === 3);
  assert.equal(ui.lists[2].get('control'), 'A.5');
  assert.equal(ui.lists[2].get('clause'), '4.1');
});

test('without control and clause catalogues the mapping filters offer only All', async (t) => {
  const ui = await workspace(t, { meta: sparseMeta });
  assert.deepEqual(
    [...ui.el('controlFilter').options].map((o) => o.textContent),
    ['All controls'],
  );
  assert.deepEqual(
    [...ui.el('clauseFilter').options].map((o) => o.textContent),
    ['All clauses'],
  );
});

test('a later page emptied by deletions elsewhere reports its position without a first item', async (t) => {
  const ui = await workspace(t, {
    pages: (n) =>
      n === 1
        ? { items: Array.from({ length: 100 }, (_, i) => swot({ id: 's' + i })), total: 150 }
        : // Fifty items were deleted between the two requests.
          { items: [], total: 100, limit: 100, offset: 100 },
  });
  ui.el('next').click();
  await settle(() => ui.lists.length === 2);
  await settle(() => /of 100/.test(ui.el('resultCount').textContent));
  assert.equal(
    ui.el('resultCount').textContent,
    'Showing 100-100 of 100 matching items. This view includes these items only; use Previous/Next to see more.',
  );
  assert.equal(ui.el('next').disabled, true);
  assert.equal(ui.el('previous').disabled, false);
});

function exportGet(release) {
  return async (u) => {
    if (u.pathname === '/api/v1/isms/swot' && u.searchParams.get('limit') === '200')
      return { items: [{ id: 's1' }], total: 1, limit: 200, offset: 0 };
    if (u.pathname === '/api/v1/isms/swot/s1') {
      await release?.();
      return {
        ...swot({ owner_user_id: 'u1' }),
        controls: [],
        clauses: [],
        links: [],
      };
    }
    throw new Error('Unexpected GET ' + u);
  };
}

function captureDownloads(t, window, downloads, blobs) {
  window.HTMLAnchorElement.prototype.click = function () {
    downloads.push(this.download);
  };
  const createObjectURL = URL.createObjectURL;
  URL.createObjectURL = (blob) => {
    blobs.push(blob);
    return 'blob:keen-export';
  };
  t.after(() => {
    URL.createObjectURL = createObjectURL;
  });
}

test('an export without a people list leaves the owner column empty', async (t) => {
  const downloads = [];
  const blobs = [];
  const ui = await workspace(t, {
    meta: sparseMeta,
    setup: (window) => captureDownloads(t, window, downloads, blobs),
    get: exportGet(),
  });
  ui.el('exportExcel').click();
  await settle(() => downloads.length === 1);
  const workbook = Buffer.from(await blobs[0].arrayBuffer()).toString('latin1');
  // The owner's account id is exported; the Owner column has no name for it.
  assert.match(workbook, /<t xml:space="preserve">u1<\/t>/);
  assert.doesNotMatch(workbook, /olivia/);
  assert.equal(ui.el('status').textContent, 'Exported 1 matching SWOT items.');
});

test('the PDF export renders the filtered items and downloads a PDF', async (t) => {
  const downloads = [];
  const blobs = [];
  const drawn = [];
  const ui = await workspace(t, {
    setup(window) {
      captureDownloads(t, window, downloads, blobs);
      // jsdom has no canvas; this one records the text drawn and returns a JPEG page.
      const create = window.document.createElement.bind(window.document);
      window.document.createElement = (name) =>
        name === 'canvas'
          ? {
              getContext: () => ({
                fillRect() {},
                fillText: (text) => drawn.push(text),
                measureText: (text) => ({ width: text.length * 11 }),
              }),
              toBlob: (done) => done(new Blob([new Uint8Array([255, 216, 255, 217])])),
            }
          : create(name);
    },
    get: exportGet(),
  });
  ui.el('exportPdf').click();
  await settle(() => downloads.length === 1);
  assert.match(downloads[0], /^swot-\d{4}-\d{2}-\d{2}\.pdf$/);
  assert.equal(blobs[0].type, 'application/pdf');
  assert.ok(drawn.includes('Flood <b>exposure</b>'));
  assert.ok(drawn.includes('Owner: olivia'));
  assert.equal(ui.el('status').textContent, 'Exported 1 matching SWOT items.');
  assert.equal(ui.el('exportPdf').disabled, false);
});

test('cancelling while the last PDF page is encoded downloads nothing', async (t) => {
  const downloads = [];
  const blobs = [];
  const ui = await workspace(t, {
    setup(window) {
      captureDownloads(t, window, downloads, blobs);
      const create = window.document.createElement.bind(window.document);
      window.document.createElement = (name) =>
        name === 'canvas'
          ? {
              getContext: () => ({
                fillRect() {},
                fillText() {},
                measureText: (text) => ({ width: text.length * 11 }),
              }),
              toBlob(done) {
                // The person cancels while the browser is still encoding the page.
                window.document.getElementById('cancelExport').click();
                done(new Blob([new Uint8Array([255, 216, 255, 217])]));
              },
            }
          : create(name);
    },
    get: exportGet(),
  });
  ui.el('exportPdf').click();
  await settle(() => ui.el('status').textContent === 'Export cancelled.');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.deepEqual(downloads, []);
  assert.deepEqual(blobs, []);
  assert.equal(ui.el('cancelExport').hidden, true);
});
