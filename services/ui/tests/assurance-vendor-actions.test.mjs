// The Vendors tab groups the SWOT link with the Edit and Delete row actions
// and keeps the row aligned so buttons do not stretch vertically.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const meta = () => ({
  assets: [{ id: 'asset-1', name: 'Demo Production Platform' }],
  users: [],
  org_nodes: [],
});

const VENDOR_ID = 'vendor-1';

function vendor(name) {
  return {
    id: VENDOR_ID,
    name,
    description: 'Fictional supplier for demonstration only.',
    asset_ids: ['asset-1'],
  };
}

async function loadVendors(t, vendors, { manage } = {}) {
  return page(t, 'assurance', {
    me: manage ? { is_admin: true } : { is_admin: false },
    get(url) {
      if (url.pathname === '/api/v1/isms/assurance/meta') return meta();
      if (url.pathname === '/api/v1/isms/people') return { items: [] };
      if (url.pathname === '/api/v1/isms/vendors') return { items: vendors };
      throw new Error(`Unexpected GET ${url.pathname}`);
    },
  });
}

test('vendor rows keep SWOT beside Edit and Delete at row-action size', async (t) => {
  const ui = await loadVendors(t, [vendor('Demo Example Hosting')], {
    manage: true,
  });
  await settle(() => ui.document.querySelector('[data-edit-vendor]') !== null);
  const row = ui.document.querySelector('#vendorsList .list-group-item');
  const actions = [...row.children].find((child) =>
    child.querySelector('button, a[href*="swot.html"]'),
  );
  const edit = actions.querySelector('[data-edit-vendor]');
  const remove = actions.querySelector('[data-delete-vendor]');
  const swot = actions.querySelector('a[href*="swot.html"]');
  assert.ok(edit && remove && swot);
  // One group holds every row action, including the SWOT link.
  assert.deepEqual(
    [...actions.children].map((node) => node.textContent.trim()),
    ['Edit', 'Delete', 'SWOT'],
  );
  // Same compact button sizing and behaviour classes across the group.
  for (const node of [edit, remove, swot]) {
    assert.ok(node.classList.contains('btn-sm'), 'row buttons share btn-sm sizing');
  }
  const rows = ui.document.querySelectorAll('#vendorsList .list-group-item');
  for (const item of rows) {
    assert.ok(
      item.classList.contains('align-items-start'),
      'row alignment must not stretch the action buttons',
    );
  }
  assert.equal(
    swot.getAttribute('href'),
    `/swot.html?target_type=vendor&target_id=${encodeURIComponent(VENDOR_ID)}`,
  );
  ui.window.close();
});

test('vendor SWOT link survives without manage rights and escapes hostile names', async (t) => {
  const ui = await loadVendors(t, [vendor('<img src=x onerror=alert(1)>')], { manage: false });
  const vendorsList = ui.document.getElementById('vendorsList');
  await settle(() => vendorsList.querySelector('a[href*="swot.html"]') !== null);
  const row = vendorsList.querySelector('.list-group-item');
  assert.equal(row.querySelectorAll('button').length, 0);
  const swot = row.querySelector('a[href*="swot.html"]');
  assert.ok(swot.classList.contains('btn-sm'));
  assert.equal(
    swot.getAttribute('href'),
    `/swot.html?target_type=vendor&target_id=${encodeURIComponent(VENDOR_ID)}`,
  );
  assert.equal(row.querySelectorAll('img').length, 0);
  assert.match(row.textContent, /<img src=x/);
  ui.window.close();
});
