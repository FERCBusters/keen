import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const item = {
  id: 'p1',
  type: 'Legal',
  lens: 'External',
  item: '<img src=x> Regulation',
  overall_relevance: { code: 'high', label: '1 High' },
  rationale: 'Annual',
  controls: [],
  clauses: [],
  affected: [
    {
      kind: 'vendor',
      id: 'v1',
      label: '<b>Regulator</b>',
      detail: 'Data protection authority',
      href: '/vendor.html?id=v1',
    },
  ],
};
async function pestlePage(t, options = {}) {
  return page(t, 'pestle', {
    ...options,
    get: async (u) => {
      if (u.pathname === '/api/v1/pestle/meta')
        return {
          types: ['Legal', 'Social'],
          lenses: ['Internal', 'External'],
          relevance_levels: [{ id: 'high', code: 'high', label: '1 High' }],
        };
      if (u.pathname === '/api/v1/pestle/items') {
        if (options.fail) throw new Error('Unavailable');
        return { items: options.empty ? [] : [item], total: options.empty ? 0 : 1, framework: 'A' };
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
}
test('PESTLE list renders safe content and scoped links', async (t) => {
  const ui = await pestlePage(t);
  assert.match(ui.el('pestleRows').textContent, /Regulation/);
  assert.equal(ui.el('pestleRows').querySelector('img'), null);
  assert.match(ui.el('pestleItemsMeta').textContent, /1 PESTLE/);
  assert.match(ui.el('newPestleItem').href, /framework=A/);
  // Who an item affects links to the record, its name as text.
  const affected = ui.el('pestleRows').querySelector('a.badge[href^="/vendor.html"]');
  assert.equal(affected.getAttribute('href'), '/vendor.html?id=v1&framework=A');
  assert.equal(affected.textContent, 'Vendor / 3rd party: <b>Regulator</b>');
  assert.equal(affected.title, 'Data protection authority');
});
for (const state of ['empty', 'reader', 'denied', 'error'])
  test('PESTLE ' + state + ' state', async (t) => {
    const ui = await pestlePage(t, {
      empty: state === 'empty',
      fail: state === 'error',
      me:
        state === 'reader'
          ? { can_view_pestle: true }
          : state === 'denied'
            ? {}
            : { is_admin: true },
    });
    if (state === 'empty') assert.match(ui.el('pestleRows').textContent, /No PESTLE/);
    if (state === 'error') assert.match(ui.el('status').textContent, /Unavailable/);
    if (state === 'denied') {
      assert.match(ui.el('pestleRows').textContent, /do not have permission/);
      assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/pestle/items')));
    }
    if (state === 'reader') assert.ok(ui.el('newPestleItem').classList.contains('d-none'));
  });
