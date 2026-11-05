import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

// The shapes services/api/app/services/pestle.py sends for an item page.
const meta = {
  types: [
    'Political',
    'Economical',
    'Social',
    'Technological',
    'Legal',
    'Environmental',
    'Ethical',
  ],
  lenses: ['Internal', 'External'],
  relevance_levels: [
    { id: 'r0', code: 'na', label: 'N/A', sort_order: 0 },
    { id: 'r1', code: 'high', label: '1 High', sort_order: 1 },
    { id: 'r2', code: 'medium', label: '2 Medium', sort_order: 2 },
    { id: 'r3', code: 'low', label: '3 Low', sort_order: 3 },
  ],
};
const item = {
  id: 'p1',
  framework: 'A',
  type: 'Economical',
  lens: 'External',
  item: 'Energy prices <b>rise</b>',
  overall_relevance: { id: 'r2', code: 'medium', label: '2 Medium', sort_order: 2 },
  overall_relevance_id: 'r2',
  rationale: '',
  created_by: 'root',
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-03-02T00:00:00Z',
  business_process_count: 0,
  clause_count: 0,
  business_processes: [],
  clauses: [],
  related_controls: [],
};

async function pestleItem(t, { query = '&id=p1', confirm = true, vendor = {} } = {}) {
  const requests = [];
  const ui = await page(t, 'pestle_item', {
    query,
    confirm,
    vendor,
    get: async (u) => {
      if (u.pathname === '/api/v1/pestle/meta') return structuredClone(meta);
      if (u.pathname === '/api/v1/pestle/items/p1') return structuredClone(item);
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u) => {
      requests.push(['PATCH', u.pathname]);
      throw new Error('Unexpected PATCH ' + u);
    },
    remove: async (u) => {
      requests.push(['DELETE', u.pathname]);
      return { ok: true };
    },
  });
  await settle(() => ui.el('pestleItemMeta').textContent !== '-' || ui.el('status').textContent);
  return { ui, requests };
}

test('a medium overall relevance shows an amber badge', async (t) => {
  const { ui } = await pestleItem(t);
  const badge = ui.el('pestleBadges').querySelector('.badge');
  assert.equal(badge.className, 'badge text-bg-warning');
  assert.equal(badge.textContent, '2 Medium');
  assert.equal(ui.el('pestleItemTitle').textContent, 'Energy prices <b>rise</b>');
  assert.equal(ui.el('pestleBadges').querySelector('b'), null);
});

test('a new item opened with no framework chosen names the selected framework', async (t) => {
  const { ui } = await pestleItem(t, { query: '', vendor: { getCurrentFramework: () => '' } });
  assert.equal(ui.el('pestleItemMeta').textContent, 'selected framework · new item');
  assert.equal(ui.el('pestleType').value, 'Political');
  assert.equal(ui.el('pestleLens').value, 'Internal');
});

test('returning to the details tab of an unsaved item leaves a bare address', async (t) => {
  const { ui } = await pestleItem(t, { query: '' });
  ui.window.history.replaceState({}, '', '/pestle_item.html?tab=relevance');
  ui.el('pestleDetailsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.window.location.href, 'https://keen.example/pestle_item.html');
});

test('saving relevance before the item exists sends nothing', async (t) => {
  const { ui, requests } = await pestleItem(t, { query: '' });
  // Managers see the save buttons on a new item, beside the request to save it first.
  assert.equal(ui.el('saveBusinessProcessRelevance').style.display, '');
  assert.match(ui.el('businessProcessRelevanceList').textContent, /Save the item first/);
  ui.el('saveBusinessProcessRelevance').click();
  ui.el('saveClauseRelevance').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(requests, []);
  assert.equal(ui.el('saveBusinessProcessRelevance').disabled, false);
  assert.equal(ui.el('status').textContent, '');
});

test('cancelling the delete confirmation keeps the item', async (t) => {
  const { ui, requests } = await pestleItem(t, { confirm: false });
  ui.el('deletePestleItem').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(requests, []);
  assert.equal(ui.el('deletePestleItem').disabled, false);
  assert.equal(ui.el('status').textContent, '');
  assert.equal(ui.window.location.pathname, '/pestle_item.html');
});
