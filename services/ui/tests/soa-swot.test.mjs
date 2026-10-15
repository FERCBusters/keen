import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

test('SoA SWOT escapes stored titles and keeps framework on detail links', async (t) => {
  const ui = await page(t, 'statement-of-applicability', {
    get: async () => ({
      framework: 'A',
      can_view_swot: true,
      controls: [],
      clauses: [],
      swot_items: [
        {
          id: 'item-1',
          title: '<img src=x>',
          kind: 'threat',
          significance: 5,
          status: 'under_review',
          controls: [{ ref: 'A.5.1' }],
          clauses: [{ ref: '6.1' }],
        },
      ],
    }),
  });
  await settle(() => ui.el('swotRows').textContent.includes('A.5.1'));
  assert.equal(ui.el('swotRows').querySelector('img'), null);
  assert.match(ui.el('swotRows').textContent, /under review/);
  assert.match(ui.el('swotRows').querySelector('a').href, /framework=A/);
});
test('SoA hides SWOT tab when access is unavailable', async (t) => {
  const ui = await page(t, 'statement-of-applicability', {
    get: async () => ({ controls: [], clauses: [], can_view_swot: false }),
  });
  await settle(() => ui.el('soaSwotTab').closest('li').hidden);
});
