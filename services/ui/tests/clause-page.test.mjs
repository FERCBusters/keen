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
    put: [],
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
        await options.gate;
        return { clause: structuredClone(state.clause), items: structuredClone(state.linked) };
      }
      if (p === '/api/v1/stories/related/clause/k1') return { items: options.stories || [] };
      if (p === '/api/v1/clauses/k1/changelog') {
        if (fail.changelog) throw new Error('Log offline');
        return { items: options.changelog || [] };
      }
      if (p === '/api/v1/controls') {
        if (fail.controls) throw new Error('Catalogue offline');
        if (options.catalogueFailures > 0) {
          options.catalogueFailures -= 1;
          throw new Error('Catalogue busy');
        }
        if (options.catalogue) return { items: options.catalogue };
        return {
          items: [
            { id: 'c1', ref: 'A1', title: 'Access' },
            { id: 'c2', ref: 'A2', title: 'Backup' },
            { id: 'c3', ref: 'A3', title: 'Crypto <b>keys</b>' },
            { id: 'k9', ref: '9', type: 'clause' },
          ],
        };
      }
      if (p === '/api/v1/clauses/k1/risks') {
        if (fail.risks) throw new Error('Risks forbidden');
        return { framework: 'A', items: options.risks || [] };
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
      if (p === '/api/v1/users') return [{ id: 'u1', username: 'dana', is_active: true }];
      if (p === '/api/v1/clauses/k1/isms') {
        if (fail.isms) throw new Error('ISMS offline');
        if (options.isms) return options.isms;
        return {
          swot: [{ id: 's1', title: 'Weak <b>backups</b>' }],
          legal_items: [{ id: 'l1', title: 'Data <b>Act</b>' }],
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
    put: async (u, b) => {
      state.put.push({ path: u.pathname, body: plain(b) });
      return { owners: b.user_ids.map((id) => ({ id, username: 'dana' })) };
    },
  });
  // A gated clause load is awaited by the test itself.
  if (options.gate) return { ui, state };
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
    /Failed to save clause URL: Rejected/.test(failing.ui.el('status').textContent),
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

test('administrators link controls in the shared picker, each with its applicability', async (t) => {
  const { ui, state } = await clausePage(t);
  ui.el('editLinkedControls').click();
  assert.equal(ui.el('adminControlsCard').style.display, '');
  const select = ui.el('clauseControls');
  const option = (id) => [...select.options].find((o) => o.value === id);
  // A linked control is chosen and carries its applicability; clauses in the
  // catalogue are not offered.
  assert.equal(select.multiple, true);
  assert.deepEqual(
    [...select.options].map((o) => [o.value, o.selected, o.dataset.extra || '']),
    [
      ['c1', true, 'applicable'],
      ['c2', true, 'partially_applicable'],
      ['c3', false, ''],
    ],
  );
  assert.equal(option('k9'), undefined);
  assert.equal(select.querySelector('b'), null, 'titles are text');
  assert.deepEqual(JSON.parse(select.dataset.pickerExtra), {
    label: 'Applicability',
    choices: [
      ['applicable', 'Applicable'],
      ['partially_applicable', 'Partially applicable'],
    ],
    default: 'applicable',
  });
  assert.equal(ui.el('adminControlsList').textContent, '');
  // Unlink one, change one, and link another without naming its applicability.
  option('c1').selected = false;
  option('c2').dataset.extra = 'applicable';
  option('c3').selected = true;
  ui.el('saveClauseControls').click();
  await settle(() => ui.el('status').textContent === 'Linked controls saved');
  // Every chosen control is sent, whatever a search was showing.
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/clauses/k1/controls',
    body: {
      controls: [
        { control_id: 'c2', applicability: 'applicable' },
        { control_id: 'c3', applicability: 'applicable' },
      ],
    },
  });
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
  assert.equal(ui.el('clauseControls').disabled, true);
  assert.equal(ui.el('clauseControls').options.length, 0);
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
    /pestle_item\.html\?id=p1&tab=details&framework=A/,
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
    ['Weak <b>backups</b>', 'Data <b>Act</b>', 'Zero breaches', 'Document', 'CISO', 'DB', 'Board'],
  );
  assert.match(rows[0].querySelector('a').getAttribute('href'), /swot\.html\?id=s1&framework=A/);
  // A legal register item opens its own page, and its title is shown as text.
  assert.equal(rows[1].cells[0].textContent, 'Legal register');
  assert.equal(
    rows[1].querySelector('a').getAttribute('href'),
    '/legal-register.html?id=l1&framework=A',
  );
  assert.equal(rows[1].querySelector('b'), null);
  assert.match(rows[2].querySelector('a').getAttribute('href'), /isms\.html\?tab=objectives/);
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
  assert.match(failed.ui.el('status').textContent, /Failed to load clause: Clause offline/);
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
  assert.equal(ui.el('status').textContent, 'Failed to load controls: Catalogue offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('clauseControls').options.length, 0);
});

test("a clause's owners show in its header, and administrators change them", async (t) => {
  const { ui, state } = await clausePage(t, {
    clause: clause({ owners: [{ id: 'u9', username: '<i>sam</i>' }] }),
  });
  assert.equal(ui.el('clauseMeta').textContent, 'A · parent 6.1 · Owners: <i>sam</i>');
  assert.equal(ui.el('ownersList').querySelector('i'), null);
  ui.el('ownersEdit').click();
  await settle(() => !ui.el('ownersEditor').hidden);
  ui.el('ownersSelect').options[0].selected = true;
  ui.el('ownersSave').click();
  await settle(() => /Owners saved/.test(ui.el('status').textContent));
  assert.deepEqual(state.put, [
    { path: '/api/v1/admin/clauses/k1/owners', body: { user_ids: ['u1'] } },
  ]);
  assert.equal(ui.el('clauseMeta').textContent, 'A · parent 6.1 · Owners: dana');
  await settle(
    () => ui.calls.filter((c) => c.url.startsWith('/api/v1/clauses/k1/changelog')).length === 2,
  );

  const reader = await clausePage(t, { me: { can_view_controls: true } });
  assert.equal(reader.ui.el('ownersEdit').hidden, true);
  assert.equal(reader.ui.el('ownersList').textContent, 'No owners yet.');
});

const ismsCalls = (ui) =>
  ui.calls.filter((c) => c.url.startsWith('/api/v1/clauses/k1/isms')).length;

test('a deep link to the ISMS tab is shown through the tab component', async (t) => {
  const shown = [];
  await clausePage(t, {
    query: '&id=k1&tab=isms',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(shown, ['clauseIsmsTab']);
});

test('PESTLE and ISMS opened while the clause loads use the framework in the address', async (t) => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const { ui } = await clausePage(t, { gate, clause: clause({ framework: 'B' }) });
  tab(ui, 'clausePestleTab');
  await settle(() => ui.el('clausePestleRows').querySelectorAll('tr').length === 2);
  assert.equal(
    ui.el('clausePestleRows').querySelector('a').getAttribute('href'),
    '/pestle_item.html?id=p1&tab=details&framework=A',
  );
  assert.equal(ui.el('openPestleFromClause').getAttribute('href'), '/pestle.html?framework=A');
  tab(ui, 'clauseIsmsTab');
  await settle(() => ui.el('clauseIsmsView').querySelector('table'));
  const isms = new URL(
    ui.calls.find((c) => c.url.startsWith('/api/v1/clauses/k1/isms')).url,
    'https://keen.example',
  );
  assert.equal(isms.searchParams.get('framework'), 'A');
  assert.equal(
    ui.el('clauseIsmsView').querySelector('a').getAttribute('href'),
    '/swot.html?id=s1&framework=A',
  );
  release();
  await settle(() => ui.el('controlsMeta').textContent === '2 linked controls');
  assert.equal(ui.el('openPestleFromClause').getAttribute('href'), '/pestle.html?framework=B');
  assert.equal(ui.el('clausesBreadcrumb').getAttribute('href'), '/clauses.html?framework=B');
});

test('switched-off PESTLE and ISMS load nothing, even after a save on their tab', async (t) => {
  const me = { is_admin: true, features: { enabled: { pestle: false, isms: false } } };
  const pestle = await clausePage(t, { me, query: '&id=k1&tab=pestle' });
  assert.equal(
    pestle.ui.calls.some((c) => c.url.includes('/pestle')),
    false,
  );
  const { ui, state } = await clausePage(t, { me, query: '&id=k1&tab=isms' });
  ui.el('editLinkedControls').click();
  await settle(() => ui.el('clauseControls').options.length);
  ui.el('saveClauseControls').click();
  await settle(() => ui.el('status').textContent === 'Linked controls saved');
  await settle(() => ui.idle());
  assert.equal(state.patched.length, 1);
  assert.equal(ismsCalls(ui), 0);
  assert.equal(ui.el('clauseIsmsView').querySelector('table'), null);
});

test('saving linked controls on the ISMS tab reloads the ISMS references', async (t) => {
  const { ui } = await clausePage(t, { query: '&id=k1&tab=isms' });
  assert.equal(ismsCalls(ui), 0);
  ui.el('editLinkedControls').click();
  await settle(() => ui.el('clauseControls').options.length);
  ui.el('saveClauseControls').click();
  await settle(() => ui.el('clauseIsmsView').querySelector('table'));
  assert.equal(ismsCalls(ui), 1);
});

test('the ISMS tab loads once, names unnamed records by kind and says when nothing is linked', async (t) => {
  const { ui } = await clausePage(t, {
    isms: {
      swot: [],
      objectives: [
        { id: 'o1', goal: '', requirement: 'Annual review' },
        { id: 'o2', goal: '', requirement: '' },
      ],
      documents: [],
      org_nodes: [{ id: 'n1', name: '' }],
      assets: [{ id: 'a1', asset: '' }],
      effectiveness_measures: [],
      meetings: [{ id: 'mt1', title: '' }],
    },
  });
  tab(ui, 'clauseIsmsTab');
  await settle(() => ui.el('clauseIsmsView').querySelector('table'));
  assert.deepEqual(
    [...ui.el('clauseIsmsView').querySelectorAll('tbody tr')].map((r) => [
      r.cells[0].textContent,
      r.cells[1].textContent,
    ]),
    [
      ['Objectives', 'Annual review'],
      ['Objectives', 'Objective'],
      ['Organisation chart', 'Org node'],
      ['Assets', 'Asset'],
      ['Meetings', 'Meeting'],
    ],
  );
  // Opening the tab again keeps what it loaded.
  tab(ui, 'clauseIsmsTab');
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ismsCalls(ui), 1);

  const empty = await clausePage(t, {
    query: '&id=k1&tab=isms',
    isms: {
      swot: [],
      objectives: [],
      documents: [],
      org_nodes: [],
      assets: [],
      effectiveness_measures: [],
      meetings: [],
    },
  });
  tab(empty.ui, 'clauseIsmsTab');
  await settle(() => !/Loading/.test(empty.ui.el('clauseIsmsView').textContent));
  assert.equal(
    empty.ui.el('clauseIsmsView').textContent,
    'No ISMS entities are currently linked to this clause.',
  );
});

test('a clause page without an id opens its tabs without requests', async (t) => {
  const ui = await page(t, 'clause', {
    get: async (u) => {
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('status').textContent === 'Missing clause id.');
  const before = [ui.el('clausePestleRows').textContent, ui.el('clauseIsmsView').textContent];
  for (const id of ['clausePestleTab', 'clauseIsmsTab', 'clauseChangelogTab'])
    ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(
    ui.calls.map((c) => c.url),
    ['/api/v1/frameworks'],
  );
  assert.deepEqual(
    [ui.el('clausePestleRows').textContent, ui.el('clauseIsmsView').textContent],
    before,
  );
});

test('editing the evidence of a clause without mappings starts from one blank row', async (t) => {
  const { ui } = await clausePage(t, { clause: clause({ evidence_mappings: [] }) });
  ui.el('editClauseEvidence').click();
  const rows = [
    ...ui.el('clauseEvidenceUrlInputs').querySelectorAll('.clause-evidence-mapping-row'),
  ];
  assert.equal(rows.length, 1);
  assert.equal(rows[0].querySelector('.clause-evidence-title').value, '');
  assert.equal(rows[0].querySelector('.clause-evidence-url').value, '');
});

test('an untitled control is offered by its reference alone, and an empty catalogue says so', async (t) => {
  const { ui } = await clausePage(t, {
    catalogue: [
      { id: 'c1', ref: 'A1', title: 'Null routing' },
      { id: 'c4', ref: 'A4', title: null },
    ],
  });
  ui.el('editLinkedControls').click();
  await settle(() => ui.el('clauseControls').options.length === 2);
  assert.deepEqual(
    [...ui.el('clauseControls').options].map((o) => o.textContent),
    ['A1 Null routing', 'A4'],
  );
  const empty = await clausePage(t, { catalogue: [] });
  empty.ui.el('editLinkedControls').click();
  await settle(() => /No controls/.test(empty.ui.el('adminControlsList').textContent));
  assert.equal(empty.ui.el('adminControlsList').textContent, 'No controls in this framework.');
});

test('a control catalogue that failed with the page is requested again when editing starts', async (t) => {
  const { ui } = await clausePage(t, { catalogueFailures: 1 });
  assert.match(ui.el('status').textContent, /Catalogue busy/);
  ui.el('editLinkedControls').click();
  await settle(() => ui.el('clauseControls').options.length);
  assert.equal(ui.calls.filter((c) => c.url.startsWith('/api/v1/controls?')).length, 2);
  assert.deepEqual(
    [...ui.el('clauseControls').options].map((o) => o.textContent.split(' ')[0]),
    ['A1', 'A2', 'A3'],
  );
});

test('risk readers see the risks linked to the clause, as text', async (t) => {
  const { ui } = await clausePage(t, {
    risks: [
      {
        id: 'r 1',
        asset: '<b>Payroll DB</b>',
        threat_summary: 'Ransomware',
        category: { name: 'Information' },
        subcategory: { name: 'Databases' },
        risk_types: ['Availability'],
        risk_score: 16,
        residual_risk_score: 6,
        risk_owner: { username: 'dana' },
      },
    ],
  });
  await settle(() => ui.el('clauseRisksView').querySelector('tbody tr'));
  assert.equal(ui.el('clauseRisksCard').style.display, '');
  assert.equal(ui.el('clauseRisksCount').textContent, '1');
  const row = ui.el('clauseRisksView').querySelector('tbody tr');
  assert.equal(row.querySelector('b'), null);
  assert.equal(row.querySelector('a').textContent, '<b>Payroll DB</b>');
  assert.equal(row.querySelector('a').getAttribute('href'), '/risk.html?id=r+1&framework=A');
  assert.match(row.textContent, /Ransomware.*Information.*Databases.*Availability.*16.*6.*dana/s);
});

test('a clause without linked risks says so, and the card stays hidden when risks cannot be read', async (t) => {
  const none = await clausePage(t);
  await settle(() => none.ui.el('clauseRisksCard').style.display === '');
  assert.equal(none.ui.el('clauseRisksView').textContent, 'No risks are linked to this clause.');
  assert.equal(none.ui.el('clauseRisksCount').textContent, '0');

  const failed = await clausePage(t, { fail: { risks: true } });
  await settle(() => failed.ui.idle());
  assert.equal(failed.ui.el('clauseRisksCard').style.display, 'none');
  assert.equal(failed.ui.el('status').textContent, '');

  // Without the feature, or without permission to read risks, nothing is requested.
  for (const me of [
    { is_admin: true, features: { enabled: { risk_cia: false } } },
    { can_view_controls: true, can_view_clauses: true },
  ]) {
    const hidden = await clausePage(t, { me });
    await settle(() => hidden.ui.idle());
    assert.equal(hidden.ui.el('clauseRisksCard').style.display, 'none');
    assert.equal(
      hidden.ui.calls.some((c) => c.url.includes('/risks')),
      false,
    );
  }
});

test('story readers get a Stories tab listing the stories assigned to the clause', async (t) => {
  const { ui } = await clausePage(t, {
    stories: [{ id: 's1', title: 'Audit <i>gap</i>', status: 'closed', updated_at: '2026-05-01' }],
  });
  const tab = ui.document.getElementById('storiesRelatedTab');
  assert.equal(tab.textContent, 'Stories');
  assert.ok(!ui.calls.some((c) => c.url.includes('/stories/')), 'loads when shown');
  tab.dispatchEvent(new ui.window.Event('shown.bs.tab'));
  const pane = ui.document.getElementById('storiesRelatedPane');
  await settle(() => pane.querySelector('.list-group'));
  assert.equal(tab.textContent, 'Stories (1)');
  assert.match(pane.textContent, /assigned them to this clause/);
  assert.equal(
    pane.querySelector('.list-group a').getAttribute('href'),
    '/story.html?id=s1&framework=A',
  );
  assert.equal(pane.querySelector('.list-group a').textContent, 'Audit <i>gap</i>');
  assert.equal(pane.querySelector('i'), null);
});

test('a clause page shows no Stories tab without story access or without a clause', async (t) => {
  const reader = await clausePage(t, { me: { can_view_isms: true } });
  assert.equal(reader.ui.document.getElementById('storiesRelatedTab'), null);
  const blank = await clausePage(t, { query: '' });
  assert.equal(blank.ui.document.getElementById('storiesRelatedTab'), null);
});
