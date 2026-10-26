import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const vendor = {
  id: 'vendor-1',
  name: '<img src=x onerror=alert(1)>',
  description: 'Supplier',
  asset_ids: [],
  assets: [],
};

test('vendor register rows open a dedicated page instead of inline editors', async (t) => {
  const ui = await page(t, 'assurance', {
    get: (url) => {
      if (url.pathname === '/api/v1/isms/people') return { items: [] };
      if (url.pathname === '/api/v1/isms/vendors') return { items: [vendor] };
      throw new Error('Unexpected GET ' + url);
    },
  });
  const row = ui.el('vendorsList').querySelector('tr');
  assert.match(row.querySelector('a').getAttribute('href'), /vendor.html\?id=vendor-1/);
  assert.equal(row.querySelector('img,button'), null);
  assert.equal(ui.document.querySelector('#vendorForm'), null);
});

test('read-only vendor detail retains safe related SWOT navigation', async (t) => {
  const ui = await page(t, 'vendor', {
    query: '&id=vendor-1',
    me: { can_view_isms: true },
    get: (url) => {
      if (url.pathname === '/api/v1/isms/vendors/vendor-1') return vendor;
      if (url.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      throw new Error('Unexpected GET ' + url);
    },
  });
  assert.equal(ui.el('editRecord').hidden, true);
  assert.equal(ui.el('deleteRecord').hidden, true);
  ui.el('tab-related').click();
  await settle(ui.idle);
  const link = ui.el('recordSwot').querySelector('a');
  assert.match(link.getAttribute('href'), /target_type=vendor&target_id=vendor-1/);
  assert.equal(ui.document.querySelector('#recordFacts img'), null);
  assert.match(ui.el('recordTitle').textContent, /<img src=x/);
});
