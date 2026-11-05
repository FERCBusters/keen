// BookStack policy capture: choosing a book or a control, sampling a section
// whose requirement has gone, and clicks that land away from a button.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

const sections = () => [
  {
    id: 's1',
    document_title: 'Access policy',
    permalink: 'https://bookstack.example/link/11',
    page_title: 'Page 11',
    target_framework: 'B',
    target_ref: 'B.1',
    captured_at: '2026-01-01',
    revision_count: 4,
    page_updated_at: '2026-01-01',
    sha256: 'abcdef0123456789',
    archived: false,
  },
  {
    id: 's2',
    document_title: 'Retired policy',
    permalink: 'https://bookstack.example/link/3',
    page_title: 'P3',
    target_framework: null,
    target_ref: null,
    captured_at: '2025-01-01',
    revision_count: null,
    page_updated_at: null,
    sha256: 'ffffffffffffffff',
    archived: false,
  },
];

async function bookstack(t) {
  const state = { posted: [], patched: [] };
  const ui = await page(t, 'bookstack-sections', {
    htmlName: 'isms',
    frameworks: [{ slug: 'A', name: 'Framework A' }, { slug: 'B' }],
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/isms/documents') return { items: [{ id: 'd1', title: 'Access policy' }] };
      if (p === '/api/v1/isms/bookstack-catalog' && u.searchParams.get('kind') === 'books')
        return { items: [{ id: 7, name: 'Handbook' }] };
      if (p === '/api/v1/isms/bookstack-catalog')
        return { items: [{ id: 11, name: 'Page 11' }], next_offset: null };
      if (p === '/api/v1/controls')
        return { items: [{ id: 'c1', ref: 'A1', title: null }], total: 1 };
      if (p === '/api/v1/clauses') return { items: [{ id: 'k5', ref: '5.2', title: 'Policy' }] };
      if (p === '/api/v1/isms/bookstack-sections') return { items: sections() };
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      return { ok: true };
    },
    patch: async (u) => {
      state.patched.push(u.pathname + u.search);
      return { ok: true };
    },
  });
  await ui.api.initBookstackDocuments(
    { is_admin: true, bookstack_enabled: true },
    ui.el('bookstackDocumentContent'),
  );
  const pageRequests = () =>
    ui.calls.filter((c) => c.url.includes('/api/v1/isms/bookstack-catalog?kind=pages'));
  return { ui, state, pageRequests };
}

test('choosing the book placeholder again keeps the pages already listed', async (t) => {
  const { ui, pageRequests } = await bookstack(t);
  ui.el('bookSelect').value = '7';
  ui.el('bookSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('pageSelect').options.length === 2);
  ui.el('bookSelect').value = '';
  ui.el('bookSelect').dispatchEvent(new ui.window.Event('change'));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(pageRequests().length, 1);
  assert.deepEqual(
    [...ui.el('pageSelect').options].map((o) => o.textContent),
    ['Choose page', 'Page 11'],
  );
});

test('a capture linked to a control names the control and no clause', async (t) => {
  const { ui, state } = await bookstack(t);
  // A control without a title is listed by its reference.
  assert.equal(
    ui.el('targetRequirement').querySelector('[value="control:c1"]').textContent,
    'Control A1 · ',
  );
  ui.el('bookSelect').value = '7';
  ui.el('bookSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('pageSelect').options.length === 2);
  ui.el('policyDocument').value = 'd1';
  ui.el('pageSelect').value = '11';
  ui.el('targetRequirement').value = 'control:c1';
  ui.el('sectionForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/isms/bookstack-sections',
    body: {
      document_id: 'd1',
      page_id: 11,
      anchor: '',
      target_control_id: 'c1',
      target_clause_id: null,
    },
  });
});

test('a section whose requirement was deleted is sampled into audits of the open framework', async (t) => {
  const { ui } = await bookstack(t);
  const audits = () => ui.calls.filter((c) => c.url.startsWith('/api/v1/audits'));
  ui.el('sectionRows').querySelector('[data-sample="s1"]').click();
  await settle(() => audits().length === 1);
  assert.equal(new URL(audits()[0].url, 'https://keen.example').searchParams.get('framework'), 'B');
  ui.el('sectionRows').querySelector('[data-sample="s2"]').click();
  await settle(() => audits().length === 2);
  assert.equal(new URL(audits()[1].url, 'https://keen.example').searchParams.get('framework'), 'A');
});

test('a click in the section table away from its buttons changes nothing', async (t) => {
  const { ui, state } = await bookstack(t);
  const row = ui.el('sectionRows').querySelector('tr');
  assert.match(row.textContent, /B · B\.1/);
  row.querySelector('td').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(state.patched, []);
  assert.equal(ui.el('bookstackStatus').textContent, '');
  assert.equal(ui.document.getElementById('entityAuditSampleSelect'), null);
});
