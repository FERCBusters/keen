import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

function clause(overrides = {}) {
  return {
    id: 'k1',
    ref: '6.1.2',
    title: '<b>Risk assessment</b>',
    framework: 'A',
    parent_ref: '6.1',
    upstream_url: 'https://guidance.example/6.1.2',
    evidence_mappings: [
      { title: 'SoA <i>sheet</i>', url: 'https://docs.example/soa' },
      { title: 'Bad', url: 'javascript:alert(1)' },
      { title: '', url: '/event.html?id=e1' },
      { title: 'Blank', url: '   ' },
    ],
    ...overrides,
  };
}

async function clausePage(t, options = {}) {
  const state = {
    clause: options.clause || clause(),
    linked: options.linked || [
      {
        id: 'c2',
        ref: 'A2',
        title: '<img src=x>Backup',
        applicability: 'partially_applicable',
        in_scope: true,
      },
      { id: 'c1', ref: 'A1', title: 'Access', applicability: 'applicable', in_scope: false },
    ],
    patched: [],
  };
  const fail = options.fail || {};
  const ui = await page(t, 'clause', {
    query: options.query ?? '&id=k1',
    me: options.me || { is_admin: true },
    setup: options.setup,
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/clauses/k1/controls') {
        if (fail.clause) throw new Error('Clause offline');
        return { clause: structuredClone(state.clause), items: structuredClone(state.linked) };
      }
      if (p === '/api/v1/clauses/k1/changelog') {
        if (fail.changelog) throw new Error('Log offline');
        return { items: options.changelog || [] };
      }
      if (p === '/api/v1/controls') {
        if (fail.controls) throw new Error('Catalogue offline');
        return {
          items: [
            { id: 'c1', ref: 'A1', title: 'Access' },
            { id: 'c2', ref: 'A2', title: 'Backup' },
            { id: 'c3', ref: 'A3', title: 'Crypto <b>keys</b>' },
            { id: 'k9', ref: '9', type: 'clause' },
          ],
        };
      }
      if (p === '/api/v1/clauses/k1/pestle') {
        if (fail.pestle) throw new Error('PESTLE offline');
        return {
          items: options.pestle ?? [
            {
              pestle_item: {
                id: 'p1',
                type: 'Legal',
                lens: 'External',
                item: '<script>Privacy</script>',
                overall_relevance: { code: 'high', label: '1 High' },
                rationale: 'GDPR',
              },
              relevance: { code: 'medium', label: '2 Medium' },
            },
            {
              pestle_item: { id: 'p2', item: 'Weather' },
              relevance: { code: 'low', label: '3 Low' },
            },
          ],
        };
      }
      if (p === '/api/v1/clauses/k1/isms') {
        if (fail.isms) throw new Error('ISMS offline');
        return {
          swot: [{ id: 's1', title: 'Weak <b>backups</b>' }],
          objectives: [{ goal: 'Zero breaches' }],
          documents: [{}],
          org_nodes: [{ name: 'CISO' }],
          assets: [{ asset: 'DB' }],
          meetings: [{ title: 'Board', updated_at: '2026-01-01' }],
        };
      }
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Rejected');
      if (u.pathname.endsWith('/evidence-urls')) return { evidence_mappings: b.evidence_mappings };
      if (u.pathname.endsWith('/upstream-url')) return { upstream_url: b.upstream_url };
      if (u.pathname.endsWith('/controls')) {
        state.linked = b.controls.map((c) => ({
          id: c.control_id,
          ref: c.control_id.toUpperCase(),
          applicability: c.applicability,
        }));
        return options.skipped
          ? { skipped_intermediate_parent: true, detail: 'Parent <b>inferred</b>' }
          : { ok: true };
      }
      throw new Error('Unexpected PATCH ' + u);
    },
  });
  await settle(() => ui.el('controlsMeta').textContent || ui.el('status').textContent);
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
}

const tab = (ui, id) => ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));

test('clause page renders header, guidance, evidence mappings and linked controls safely', async (t) => {
  const { ui } = await clausePage(t);
  assert.equal(ui.el('clauseTitle').textContent, '6.1.2  <b>Risk assessment</b>');
  assert.equal(ui.el('clauseMeta').textContent, 'A · parent 6.1');
  assert.equal(ui.el('clauseLinks').querySelector('a').href, 'https://guidance.example/6.1.2');
  assert.match(ui.el('clausesBreadcrumb').getAttribute('href'), /clauses\.html\?framework=A/);
  // Only safe evidence URLs become links; blank URLs are dropped from the count.
  assert.equal(ui.el('clauseEvidenceMeta').textContent, '3 mapped evidence links');
  const links = [...ui.el('clauseEvidenceLinks').querySelectorAll('a')];
  assert.deepEqual(
    links.map((a) => a.getAttribute('href')),
    ['https://docs.example/soa', '/event.html?id=e1'],
  );
  assert.deepEqual(
    links.map((a) => a.textContent),
    ['SoA <i>sheet</i>', '/event.html?id=e1'],
  );
  // Linked controls are escaped and show applicability and scope.
  assert.equal(ui.el('controlsMeta').textContent, '2 linked controls');
  assert.equal(ui.el('rows').querySelector('img'), null);
  const rows = [...ui.el('rows').querySelectorAll('tr')];
  assert.match(rows[0].textContent, /Partially applicable[\s\S]*In scope/);
  assert.match(rows[1].textContent, /Applicable[\s\S]*Out/);
  assert.match(rows[0].querySelector('a').getAttribute('href'), /control\.html\?id=c2&framework=A/);
  // Admin controls are present but closed until requested.
  assert.equal(ui.el('adminClauseUrlCard').style.display, '');
  assert.equal(ui.el('clauseUpstreamUrl').value, 'https://guidance.example/6.1.2');
  assert.equal(ui.el('adminControlsCard').style.display, 'none');
  assert.equal(ui.el('adminClauseEvidenceEditor').style.display, 'none');
  assert.match(ui.el('clauseChangelog').textContent, /No clause changes have been recorded yet/);
});

test('blank evidence links and unsafe guidance URLs are left out', async (t) => {
  const { ui } = await clausePage(t, {
    clause: clause({
      evidence_mappings: [{ title: 'Register', url: 'https://a.example/1' }, { url: ' ' }],
      upstream_url: 'javascript:alert(1)',
      parent_ref: '',
    }),
    linked: [{ id: 'c1', ref: 'A1', applicability: 'other' }],
  });
  assert.equal(ui.el('clauseLinks').innerHTML, '');
  assert.equal(ui.el('clauseMeta').textContent, 'A');
  assert.equal(ui.el('clauseEvidenceMeta').textContent, '1 mapped evidence link');
  assert.equal(ui.el('controlsMeta').textContent, '1 linked control');
  assert.match(ui.el('rows').textContent, /Not applicable/);
});

test('clauses without mappings or controls show empty states to readers', async (t) => {
  const { ui } = await clausePage(t, {
    me: { can_view_controls: true },
    clause: clause({ evidence_mappings: [] }),
    linked: [],
  });
  assert.equal(
    ui.el('clauseEvidenceMeta').textContent,
    'No evidence links mapped directly to this clause yet.',
  );
  assert.match(ui.el('clauseEvidenceLinks').textContent, /No evidence mapping links have been set/);
  assert.match(ui.el('rows').textContent, /No controls are linked to this clause/);
  for (const id of ['adminClauseUrlCard', 'editLinkedControls', 'editClauseEvidence'])
    assert.equal(ui.el(id).style.display, 'none', id);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/controls?')));
  tab(ui, 'clauseIsmsTab');
  await settle(() =>
    /do not have permission to view ISMS/.test(ui.el('clauseIsmsView').textContent),
  );
});

test('administrators edit evidence mappings, keeping one blank row and ignoring blank URLs', async (t) => {
  const { ui, state } = await clausePage(t);
  ui.el('editClauseEvidence').click();
  assert.equal(ui.el('adminClauseEvidenceEditor').style.display, '');
  assert.equal(ui.el('editClauseEvidence').textContent, 'Hide edit');
  let rows = ui.el('clauseEvidenceUrlInputs').querySelectorAll('.clause-evidence-mapping-row');
  assert.equal(rows.length, 3);
  assert.equal(rows[0].querySelector('.clause-evidence-title').value, 'SoA <i>sheet</i>');
  // Removing every row leaves one empty row to type into.
  for (const btn of [
    ...ui.el('clauseEvidenceUrlInputs').querySelectorAll('.remove-clause-evidence-url'),
  ])
    btn.click();
  rows = ui.el('clauseEvidenceUrlInputs').querySelectorAll('.clause-evidence-mapping-row');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].querySelector('.clause-evidence-url').value, '');
  rows[0].querySelector('.clause-evidence-title').value = ' Policy ';
  rows[0].querySelector('.clause-evidence-url').value = ' https://docs.example/policy ';
  ui.el('addClauseEvidenceUrl').click();
  ui.el('addClauseEvidenceUrl').click();
  const last = [
    ...ui.el('clauseEvidenceUrlInputs').querySelectorAll('.clause-evidence-mapping-row'),
  ].at(-1);
  last.querySelector('.clause-evidence-title').value = 'No URL';
  ui.el('saveClauseEvidenceUrls').click();
  await settle(() => ui.el('status').textContent === 'Clause evidence mapping saved');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/clauses/k1/evidence-urls',
    body: { evidence_mappings: [{ title: 'Policy', url: 'https://docs.example/policy' }] },
  });
  assert.equal(ui.el('adminClauseEvidenceEditor').style.display, 'none');
  assert.equal(ui.el('clauseEvidenceMeta').textContent, '1 mapped evidence link');
});

test('administrators save the guidance URL and see failures reported', async (t) => {
  const { ui, state } = await clausePage(t);
  ui.el('clauseUpstreamUrl').value = '  ';
  ui.el('saveClauseUpstreamUrl').click();
  await settle(() => ui.el('status').textContent === 'Clause URL saved');
  assert.deepEqual(state.patched[0].body, { upstream_url: null });
  assert.equal(ui.el('clauseLinks').innerHTML, '');

  const failing = await clausePage(t, { patchFail: true });
  failing.ui.el('saveClauseUpstreamUrl').click();
  await settle(() =>
    /Failed to save clause URL: Error: Rejected/.test(failing.ui.el('status').textContent),
  );
  assert.equal(failing.ui.el('saveClauseUpstreamUrl').disabled, false);
  failing.ui.el('editClauseEvidence').click();
  failing.ui.el('saveClauseEvidenceUrls').click();
  await settle(() =>
    /Failed to save clause evidence mapping/.test(failing.ui.el('status').textContent),
  );
  failing.ui.el('editLinkedControls').click();
  failing.ui.el('saveClauseControls').click();
  await settle(() => /Failed to save linked controls/.test(failing.ui.el('status').textContent));
});

test('administrators link controls with applicability and can filter the catalogue', async (t) => {
  const { ui, state } = await clausePage(t);
  ui.el('editLinkedControls').click();
  assert.equal(ui.el('adminControlsCard').style.display, '');
  const sel = (id) => ui.el('adminControlsList').querySelector(`[data-control-id="${id}"]`);
  assert.equal(sel('c1').value, 'applicable');
  assert.equal(sel('c2').value, 'partially_applicable');
  assert.equal(sel('c3').value, '');
  assert.equal(sel('k9'), null);
  assert.equal(ui.el('adminControlsList').querySelector('b'), null);
  ui.el('controlSearch').value = 'crypto';
  ui.el('controlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(
    [...ui.el('adminControlsList').querySelectorAll('select')].map((s) => s.dataset.controlId),
    ['c3'],
  );
  sel('c3').value = 'applicable';
  ui.el('saveClauseControls').click();
  await settle(() => ui.el('status').textContent === 'Linked controls saved');
  // Only visible, applicable rows are submitted.
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/clauses/k1/controls',
    body: { controls: [{ control_id: 'c3', applicability: 'applicable' }] },
  });
  await settle(() => ui.el('controlsMeta').textContent === '1 linked control');
  ui.el('controlSearch').value = 'zzz';
  ui.el('controlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('adminControlsList').textContent, /No controls match/);
  ui.el('editLinkedControls').click();
  assert.equal(ui.el('adminControlsCard').style.display, 'none');
});

test('a skipped intermediate parent shows the API detail as plain text', async (t) => {
  const { ui } = await clausePage(t, { skipped: true, query: '&id=k1&tab=changelog' });
  ui.el('editLinkedControls').click();
  ui.el('saveClauseControls').click();
  await settle(() => ui.el('status').textContent === 'Parent <b>inferred</b>');
});

test('intermediate parent clauses cannot have controls linked directly', async (t) => {
  const { ui } = await clausePage(t, { clause: clause({ parent_id: 'k0', child_count: 2 }) });
  ui.el('editLinkedControls').click();
  assert.match(
    ui.el('adminControlsList').textContent,
    /Link controls to the child clauses instead/,
  );
  assert.equal(ui.el('saveClauseControls').disabled, true);
  assert.equal(ui.el('controlSearch').disabled, true);
});

test('PESTLE and ISMS tabs load linked records on demand', async (t) => {
  const { ui } = await clausePage(t);
  tab(ui, 'clausePestleTab');
  await settle(() => ui.el('clausePestleRows').querySelectorAll('tr').length === 2);
  assert.match(ui.window.location.search, /tab=pestle/);
  assert.equal(ui.el('clausePestleRows').querySelector('script'), null);
  const first = ui.el('clausePestleRows').querySelector('tr');
  assert.match(first.innerHTML, /text-bg-warning">2 Medium[\s\S]*text-bg-danger">1 High/);
  assert.match(
    first.querySelector('a').getAttribute('href'),
    /pestle_item\.html\?id=p1&framework=A/,
  );
  assert.equal(
    ui.el('clausePestleRows').querySelectorAll('tr')[1].cells[5].textContent.trim(),
    '-',
  );

  tab(ui, 'clauseIsmsTab');
  await settle(() => ui.el('clauseIsmsView').querySelector('table'));
  const rows = [...ui.el('clauseIsmsView').querySelectorAll('tbody tr')];
  assert.deepEqual(
    rows.map((r) => r.cells[1].textContent),
    ['Weak <b>backups</b>', 'Zero breaches', 'Document', 'CISO', 'DB', 'Board'],
  );
  assert.match(rows[0].querySelector('a').getAttribute('href'), /swot\.html\?id=s1&framework=A/);
  assert.match(rows[1].querySelector('a').getAttribute('href'), /isms\.html\?tab=objectives/);
  tab(ui, 'clauseChangelogTab');
  assert.match(ui.window.location.search, /tab=changelog/);
  tab(ui, 'clauseControlsTab');
  assert.doesNotMatch(ui.window.location.search, /tab=/);
});

test('empty and failed PESTLE/ISMS lookups are explained', async (t) => {
  const empty = await clausePage(t, { pestle: [], query: '&id=k1&tab=pestle' });
  assert.match(
    empty.ui.el('clausePestleRows').textContent,
    /No PESTLE\(E\) items are currently linked/,
  );
  const failed = await clausePage(t, { fail: { pestle: true, isms: true } });
  tab(failed.ui, 'clausePestleTab');
  await settle(() =>
    /Failed to load PESTLE\(E\) relevance/.test(failed.ui.el('clausePestleRows').textContent),
  );
  tab(failed.ui, 'clauseIsmsTab');
  await settle(() =>
    /Failed to load ISMS references/.test(failed.ui.el('clauseIsmsView').textContent),
  );
});

test('clause load failures and a missing id are reported', async (t) => {
  const failed = await clausePage(t, { fail: { clause: true } });
  assert.match(failed.ui.el('status').textContent, /Failed to load clause: Error: Clause offline/);
  assert.match(failed.ui.el('rows').textContent, /Failed to load linked controls/);
  const missing = await page(t, 'clause', {
    get: async (u) => {
      throw new Error('Unexpected GET ' + u);
    },
  });
  assert.equal(missing.el('status').textContent, 'Missing clause id.');
});

test('a deep-linked tab is shown through the tab component, and a failed changelog is reported in its pane', async (t) => {
  const shown = [];
  const { ui } = await clausePage(t, {
    query: '&id=k1&tab=changelog',
    fail: { changelog: true },
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(shown, ['clauseChangelogTab']);
  await settle(() => /Failed to load changelog/.test(ui.el('clauseChangelog').textContent));
  assert.equal(ui.el('clauseChangelog').textContent.trim(), 'Failed to load changelog.');
});

test('a control catalogue that cannot load is reported when editing linked controls', async (t) => {
  const { ui } = await clausePage(t, { fail: { controls: true } });
  ui.el('editLinkedControls').click();
  await settle(() => /Failed to load controls/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Failed to load controls: Error: Catalogue offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('adminControlsList').querySelector('select'), null);
});
