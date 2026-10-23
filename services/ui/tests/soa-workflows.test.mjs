import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';
const soa = () => ({
  framework: 'A',
  can_view_risks: true,
  can_view_pestle: true,
  can_view_interested_parties: true,
  can_view_isms: true,
  can_view_swot: true,
  counts: {
    controls: 2,
    clauses: 2,
    risks: 2,
    pestle: 1,
    business_processes: 3,
    interested_parties: 1,
    isms_objectives: 1,
    isms_documents: 3,
    isms_effectiveness_measures: 2,
    isms_assets: 1,
  },
  controls: [
    {
      id: 'c1',
      ref: 'A.5.1',
      title: HOSTILE + 'Policies',
      in_scope: true,
      justification: 'Required by law',
      clauses: { k1: 'applicable', k2: 'partially_applicable' },
    },
    { id: 'c2', ref: 'A.8.2', title: 'Privileged access', in_scope: false, clauses: {} },
  ],
  clauses: [
    {
      id: 'k1',
      ref: '4.1',
      title: 'Context',
      controls: { c1: 'applicable' },
      evidence_mappings: [
        { title: 'Board minutes', url: 'https://docs.example.test/minutes' },
        { title: 'Bad link', url: 'javascript:alert(1)' },
      ],
    },
    { id: 'k2', ref: '6.1', title: 'Risk treatment', controls: { c1: 'partially_applicable' } },
  ],
  risks: [
    {
      id: 'r1',
      asset: 'Payroll DB',
      threat_summary: 'Ransomware',
      category: { name: 'Information' },
      subcategory: { name: 'Databases' },
      risk_types: ['Availability'],
      risk_score: 16,
      residual_risk_score: 6,
      risk_owner: { username: 'dana' },
      controls: [{ id: 'c1', ref: 'A.5.1', title: 'Policies' }],
    },
    { id: 'r2', asset: '', threat_summary: '', risk_score: 2, residual_risk_score: 0 },
  ],
  pestle_items: [
    {
      id: 'p1',
      type: 'Legal',
      lens: 'External',
      item: HOSTILE + 'Privacy law',
      rationale: 'Fines',
      overall_relevance: { code: 'high', label: 'High' },
      business_processes: [
        { business_process: { name: 'Payroll' }, relevance: { label: 'Medium' } },
        {},
      ],
      clauses: [
        { clause: { id: 'k1', ref: '4.1', title: 'Context' }, relevance: { label: 'High' } },
        {},
      ],
    },
    { id: 'p2', type: '', item: '', overall_relevance: { code: 'medium', label: 'Medium' } },
    { id: 'p3', item: 'Low item', overall_relevance: { code: 'low', label: 'Low' } },
    { id: 'p4', item: 'Unrated' },
  ],
  interested_parties: [
    {
      id: 'ip1',
      name: { name: 'Regulator' },
      nature: { name: 'Statutory' },
      note: 'Notify quickly',
      controls: [{ id: 'c1', ref: 'A.5.1', title: 'Policies' }],
      communications: [
        { event: 'Breach', when: '72h', with_whom: 'Commissioner', methods: ['Email', 'Portal'] },
        { event: 'Audit', when: 'Annually', with_whom: 'Board', methods: ['Email'] },
      ],
    },
    { id: 'ip2', name: null, nature: null, note: ' ' },
  ],
  isms_objectives: [
    {
      goal: 'Reduce phishing',
      requirement: 'Clause 6.2',
      metric: 'Click rate',
      owner: { username: 'dana' },
      completion_target_date: '2026-12-31',
      resource_users: [{ username: 'sam' }],
      resource_requirements_text: 'Training budget',
      controls: [{ id: 'c1', ref: 'A.5.1' }],
      clauses: [{ id: 'k2', ref: '6.1', title: 'Risk treatment' }],
    },
    { requirement: 'Requirement only' },
  ],
  isms_documents: [
    {
      id: 'd1',
      title: 'Policy',
      document_type: 'Policy',
      external_url: 'https://docs.example.test/policy',
      description: 'Top level',
      controls: [],
      clauses: [],
    },
    { id: 'd/2', title: 'Uploaded', has_file: true, filename: HOSTILE + 'plan.pdf' },
    { id: 'd3', title: 'Unsafe', external_url: 'javascript:alert(1)' },
    { id: 'd4' },
  ],
  isms_effectiveness_measures: [
    {
      id: 'm1',
      summary: 'Patch latency',
      description: 'Days to patch',
      source_type: 'patch_agent',
      metric_key: 'patch_days',
      target_display: '< 7 days',
      frequency: 'Monthly',
      owner: { username: 'dana' },
      latest_entry: {
        id: 'e1',
        value_display: '5 days',
        period: '2026-09',
        source_title: 'Patch agent',
      },
      metric_entry_count: 1,
      controls: [{ id: 'c1', ref: 'A.5.1' }],
    },
    {
      id: 'm2',
      summary: 'Backups',
      source_type: 'other',
      latest_entry: { source_type: 'manual' },
      metric_entry_count: 3,
    },
    { id: 'm3', description: 'Only a description' },
  ],
  isms_org_nodes: [
    {
      name: 'Security',
      parent: { name: 'Board' },
      people: [{ username: 'dana' }],
      controls: [],
      clauses: [],
    },
    {},
  ],
  isms_assets: [
    {
      asset: 'Laptop fleet',
      category: { name: 'Hardware' },
      subcategory_name: 'Laptops',
      license: 'Owned',
      owner_org_node: { name: 'IT' },
      register_held_by_org_node: { name: 'Finance' },
    },
    { name: 'Named asset' },
    {},
  ],
  swot_items: [],
});

async function soaPage(t, options = {}) {
  const ui = await page(t, 'statement-of-applicability', {
    query: options.query || '',
    setup(window) {
      if (options.tabs) window.bootstrap = options.tabs;
    },
    get: async (u) => {
      assert.equal(u.pathname, '/api/v1/statement-of-applicability');
      assert.equal(u.searchParams.get('framework'), 'A');
      if (options.fail) throw new Error('SoA offline');
      return options.data ?? soa();
    },
  });
  await settle(() => ui.el('status').textContent || ui.el('controlsRows').children.length);
  await new Promise((r) => setTimeout(r, 5));
  return ui;
}
const filter = (ui, id, value) => {
  ui.el(id).value = value;
  ui.el(id).dispatchEvent(new ui.window.Event('input'));
};
const rowTexts = (ui, id) =>
  [...ui.el(id).querySelectorAll('tr')].map((r) =>
    [...r.children].map((td) => td.textContent.replace(/\s+/g, ' ').trim()).join(' '),
  );

test('the summary and control/clause matrices show applicability with safe links', async (t) => {
  const ui = await soaPage(t);
  assert.equal(
    ui.el('soaMeta').textContent,
    'A · 2 controls · 2 clauses · 2 CIA risks · 1 PESTLE(E) items · 3 business processes · 1 interested parties · 1 ISMS objectives · 3 ISMS documents · 2 effectiveness measures · 1 assets',
  );
  assert.equal(ui.document.querySelector('main img'), null);
  assert.equal(ui.window.pwned, undefined);
  const head = [...ui.el('controlsHead').querySelectorAll('th')];
  assert.equal(head[4].getAttribute('title'), '4.1 Context');
  assert.equal(head[4].querySelector('a').getAttribute('href'), '/clause.html?id=k1&framework=A');
  const [c1, c2] = ui.el('controlsRows').querySelectorAll('tr');
  assert.equal(c1.querySelector('a').getAttribute('href'), '/control.html?id=c1&framework=A');
  assert.match(c1.textContent, /<img src=x onerror="globalThis\.pwned=1">Policies/);
  assert.deepEqual(
    [...c1.querySelectorAll('td[data-sort]')]
      .slice(1)
      .map((td) => [td.dataset.sort, td.firstElementChild.getAttribute('title')]),
    [
      ['A', 'Applicable'],
      ['P', 'Partially applicable'],
    ],
  );
  assert.equal(c1.querySelector('.badge').textContent, 'in');
  assert.equal(c2.querySelectorAll('td')[2].textContent, 'out');
  assert.equal(c2.querySelectorAll('td')[3].textContent, '-');
  assert.equal(
    c2.querySelectorAll('td')[4].firstElementChild.getAttribute('title'),
    'Not applicable',
  );

  const [k1, k2] = ui.el('clausesRows').querySelectorAll('tr');
  const evidence = k1.querySelectorAll('td')[2].querySelectorAll('a');
  // Only HTTP(S) evidence links are rendered.
  assert.equal(evidence.length, 1);
  assert.equal(evidence[0].getAttribute('href'), 'https://docs.example.test/minutes');
  assert.equal(k2.querySelectorAll('td')[2].textContent, '-');
  assert.equal(ui.el('clausesHead').querySelectorAll('th').length, 5);

  filter(ui, 'controlsFilter', 'LAW');
  assert.deepEqual(rowTexts(ui, 'controlsRows').length, 1);
  filter(ui, 'controlsFilter', 'zzz');
  assert.match(ui.el('controlsRows').textContent, /No controls match/);
  assert.equal(ui.el('controlsRows').querySelector('td').getAttribute('colspan'), '6');
  filter(ui, 'clausesFilter', 'minutes');
  assert.equal(rowTexts(ui, 'clausesRows').length, 1);
  filter(ui, 'clausesFilter', 'nothing');
  assert.match(ui.el('clausesRows').textContent, /No clauses match/);
  assert.equal(ui.el('clausesRows').querySelector('td').getAttribute('colspan'), '5');
});

test('risks, PESTLE(E) and Interested Parties render scores, relevance and filters', async (t) => {
  const ui = await soaPage(t);
  const [r1, r2] = ui.el('risksRows').querySelectorAll('tr');
  assert.equal(r1.querySelector('a').textContent, 'Payroll DB - Ransomware');
  assert.equal(r2.querySelector('a').textContent, 'Risk');
  assert.deepEqual(
    [...r1.querySelectorAll('td.text-end .badge')].map((b) => [b.textContent, b.className]),
    [
      ['16', 'badge text-bg-danger'],
      ['6', 'badge text-bg-warning'],
    ],
  );
  assert.equal(r2.querySelector('td.text-end .badge').className, 'badge text-bg-success');
  assert.match(r1.textContent, /dana/);
  assert.equal(r1.querySelector('a.badge').getAttribute('href'), '/control.html?id=c1&framework=A');
  assert.deepEqual(
    [...r2.cells].slice(1, 4).map((c) => c.textContent.replace(/\s+/g, ' ').trim()),
    ['-', '-', '-'],
  );
  filter(ui, 'risksFilter', 'databases');
  assert.equal(rowTexts(ui, 'risksRows').length, 1);
  filter(ui, 'risksFilter', 'a.5.1');
  assert.equal(rowTexts(ui, 'risksRows').length, 1);
  filter(ui, 'risksFilter', 'nope');
  assert.match(ui.el('risksRows').textContent, /No risks match/);

  const pestle = [...ui.el('pestleRows').querySelectorAll('tr')];
  assert.equal(pestle.length, 4);
  assert.equal(
    pestle[0].querySelector('a').getAttribute('href'),
    '/pestle_item.html?id=p1&framework=A',
  );
  assert.deepEqual(
    pestle.map((r) => r.querySelector('td:nth-child(4) .badge').className),
    ['badge text-bg-danger', 'badge text-bg-warning', 'badge badge-soft', 'badge text-bg-light'],
  );
  assert.equal(pestle[1].querySelector('a').textContent, 'PESTLE(E) item');
  const processes = pestle[0].querySelectorAll('td')[5].querySelectorAll('.badge');
  assert.deepEqual(
    [...processes].map((b) => [b.textContent, b.getAttribute('title')]),
    [
      ['Payroll', 'Medium'],
      ['Business process', '-'],
    ],
  );
  const clauseBadges = pestle[0].querySelectorAll('td')[6].querySelectorAll('a');
  assert.equal(clauseBadges[0].getAttribute('href'), '/clause.html?id=k1&tab=pestle&framework=A');
  assert.equal(clauseBadges[0].getAttribute('title'), 'Context · High');
  assert.equal(clauseBadges[1].textContent, 'Clause');
  filter(ui, 'pestleFilter', 'payroll');
  assert.equal(rowTexts(ui, 'pestleRows').length, 1);
  filter(ui, 'pestleFilter', '4.1');
  assert.equal(rowTexts(ui, 'pestleRows').length, 1);
  filter(ui, 'pestleFilter', 'zzz');
  assert.match(ui.el('pestleRows').textContent, /No PESTLE\(E\) items match/);

  const [ip1, ip2] = ui.el('interestedPartiesRows').querySelectorAll('tr');
  assert.equal(
    ip1.querySelector('a').getAttribute('href'),
    '/interested_party.html?id=ip1&framework=A',
  );
  assert.equal(
    ip1.querySelectorAll('td')[3].querySelector('a').getAttribute('href'),
    '/control.html?id=c1&tab=interested-parties&framework=A',
  );
  assert.match(
    ip1.querySelectorAll('td')[4].textContent,
    /Breach · 72h · CommissionerAudit · Annually · Board/,
  );
  // Methods are de-duplicated across communications.
  assert.deepEqual(
    [...ip1.querySelectorAll('td')[5].querySelectorAll('.badge')].map((b) => b.textContent),
    ['Email', 'Portal'],
  );
  assert.equal(ip2.querySelector('a').textContent, 'Interested party');
  assert.equal(rowTexts(ui, 'interestedPartiesRows')[1], 'Interested party - - - - -');
  filter(ui, 'interestedPartiesFilter', 'portal');
  assert.equal(rowTexts(ui, 'interestedPartiesRows').length, 1);
  filter(ui, 'interestedPartiesFilter', 'zzz');
  assert.match(ui.el('interestedPartiesRows').textContent, /No interested parties match/);
});

test('ISMS objectives, documents, measures, organisation and assets are listed', async (t) => {
  const ui = await soaPage(t);
  const [o1, o2] = ui.el('ismsObjectivesRows').querySelectorAll('tr');
  assert.match(o1.textContent, /Reduce phishing\s*Clause 6\.2/);
  assert.match(o1.textContent, /sam/);
  assert.equal(
    o1.querySelectorAll('td')[5].querySelector('a').getAttribute('href'),
    '/control.html?id=c1&tab=isms&framework=A',
  );
  assert.equal(
    o1.querySelectorAll('td')[6].querySelector('a').getAttribute('href'),
    '/clause.html?id=k2&tab=isms&framework=A',
  );
  assert.equal(
    o1.querySelectorAll('td')[6].querySelector('a').getAttribute('title'),
    'Risk treatment',
  );
  assert.match(o2.querySelector('td').textContent, /^Requirement only/);
  filter(ui, 'ismsObjectivesFilter', 'dana');
  assert.equal(rowTexts(ui, 'ismsObjectivesRows').length, 1);
  filter(ui, 'ismsObjectivesFilter', 'zzz');
  assert.match(ui.el('ismsObjectivesRows').textContent, /No ISMS objectives match/);

  const docs = [...ui.el('ismsDocumentsRows').querySelectorAll('tr')];
  const docLink = (row) => row.querySelectorAll('td')[2];
  assert.equal(
    docLink(docs[0]).querySelector('a').getAttribute('href'),
    'https://docs.example.test/policy',
  );
  assert.equal(
    docLink(docs[1]).querySelector('a').getAttribute('href'),
    '/api/v1/isms/documents/d%2F2/file',
  );
  assert.equal(docLink(docs[1]).textContent, HOSTILE + 'plan.pdf');
  assert.equal(docLink(docs[2]).querySelector('a').getAttribute('href'), '#');
  assert.equal(docLink(docs[3]).textContent, '-');
  assert.equal(docs[3].querySelector('td').textContent, 'Document');
  filter(ui, 'ismsDocumentsFilter', 'top level');
  assert.equal(rowTexts(ui, 'ismsDocumentsRows').length, 1);
  filter(ui, 'ismsDocumentsFilter', 'zzz');
  assert.match(ui.el('ismsDocumentsRows').textContent, /No ISMS documents match/);

  const measures = [...ui.el('ismsEffectivenessRows').querySelectorAll('tr')];
  assert.equal(
    measures[0].querySelector('a').getAttribute('href'),
    '/isms-effectiveness-measure.html?id=m1&framework=A',
  );
  assert.match(measures[0].textContent, /patch_days/);
  assert.equal(measures[0].querySelectorAll('td')[1].textContent, 'patch_agent');
  assert.equal(measures[1].querySelectorAll('td')[1].textContent, 'Other');
  const latest = measures[0].querySelectorAll('td')[2];
  assert.equal(
    latest.querySelector('a').getAttribute('href'),
    '/isms-effectiveness-metric.html?id=e1&framework=A',
  );
  assert.match(latest.textContent, /5 days\s*2026-09 · Patch agent\s*1 metric entry/);
  assert.match(
    measures[1].querySelectorAll('td')[2].textContent,
    /^Metric entry\s*manual\s*3 metric entries/,
  );
  assert.equal(measures[1].querySelectorAll('td')[2].querySelector('a'), null);
  assert.equal(measures[1].querySelector('a').textContent, 'Backups');
  assert.equal(measures[2].querySelector('a').textContent, 'Effectiveness measure');
  assert.match(measures[2].querySelectorAll('td')[2].textContent, /No metric entries yet/);
  filter(ui, 'ismsEffectivenessFilter', 'patch agent');
  assert.equal(rowTexts(ui, 'ismsEffectivenessRows').length, 1);
  filter(ui, 'ismsEffectivenessFilter', 'zzz');
  assert.match(ui.el('ismsEffectivenessRows').textContent, /No effectiveness measures match/);

  const orgs = rowTexts(ui, 'ismsOrgRows');
  assert.match(orgs[0], /^Security Board dana/);
  assert.match(orgs[1], /^Org node Root/);
  const assets = rowTexts(ui, 'ismsAssetsRows');
  assert.match(assets[0], /^Laptop fleet Hardware Laptops Owned IT Finance/);
  assert.match(assets[1], /^Named asset - - - - -/);
  assert.match(assets[2], /^Asset/);
});

test('sections the user cannot view explain the restriction', async (t) => {
  const data = {
    ...soa(),
    can_view_risks: false,
    can_view_pestle: false,
    can_view_interested_parties: false,
    can_view_isms: false,
    counts: undefined,
  };
  const ui = await soaPage(t, { data });
  assert.match(ui.el('soaMeta').textContent, /^A · 0 controls/);
  assert.match(ui.el('risksRows').textContent, /do not have permission to view risks/);
  assert.match(ui.el('pestleRows').textContent, /do not have permission to view PESTLE\(E\) items/);
  assert.match(
    ui.el('interestedPartiesRows').textContent,
    /do not have permission to view interested parties/,
  );
  assert.match(
    ui.el('ismsObjectivesRows').textContent,
    /do not have permission to view ISMS objectives/,
  );
  assert.match(
    ui.el('ismsDocumentsRows').textContent,
    /do not have permission to view ISMS documents/,
  );
  assert.match(
    ui.el('ismsEffectivenessRows').textContent,
    /do not have permission to view ISMS effectiveness measures/,
  );
  assert.match(ui.el('ismsOrgRows').textContent, /You do not have permission to view ISMS/);
  assert.match(ui.el('ismsAssetsRows').textContent, /You do not have permission to view ISMS/);
});

test('an empty framework shows empty states', async (t) => {
  const ui = await soaPage(t, {
    data: { framework: 'A', can_view_isms: true, counts: { isms_business_processes: 4 } },
  });
  assert.match(ui.el('soaMeta').textContent, /4 business processes/);
  assert.match(ui.el('ismsOrgRows').textContent, /No organisation chart nodes yet/);
  assert.match(ui.el('ismsAssetsRows').textContent, /No ISMS assets yet/);
  assert.match(ui.el('swotRows').textContent, /No SWOT items mapped/);
});

test('a load failure is reported in every section', async (t) => {
  const ui = await soaPage(t, { fail: true });
  assert.equal(
    ui.el('status').textContent,
    'Failed to load Statement of Applicability: Error: SoA offline',
  );
  for (const [id, text] of [
    ['controlsRows', 'Failed to load controls.'],
    ['clausesRows', 'Failed to load clauses.'],
    ['risksRows', 'Failed to load risks.'],
    ['pestleRows', 'Failed to load PESTLE(E) items.'],
    ['interestedPartiesRows', 'Failed to load interested parties.'],
    ['ismsObjectivesRows', 'Failed to load ISMS objectives.'],
    ['ismsDocumentsRows', 'Failed to load ISMS documents.'],
    ['ismsEffectivenessRows', 'Failed to load effectiveness measures.'],
    ['ismsOrgRows', 'Failed to load organisation chart.'],
    ['ismsAssetsRows', 'Failed to load assets.'],
  ])
    assert.equal(ui.el(id).textContent, text);
});

function fakeTabs() {
  const shown = [];
  return { shown, Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) } };
}

test('the tab query parameter opens the matching tab and tab changes are recorded', async (t) => {
  for (const [tab, id] of [
    ['risks', 'soaRisksTab'],
    ['interestedparties', 'soaInterestedPartiesTab'],
    ['isms', 'soaIsmsObjectivesTab'],
    ['ISMSDocuments', 'soaIsmsDocumentsTab'],
    ['effectiveness', 'soaIsmsEffectivenessTab'],
    ['orgassets', 'soaIsmsOrgAssetsTab'],
    // The value recorded when the Organisation/Assets tab is selected.
    ['ismsorgassets', 'soaIsmsOrgAssetsTab'],
    ['swot', 'soaSwotTab'],
  ]) {
    const tabs = fakeTabs();
    await soaPage(t, { query: '&tab=' + tab, tabs });
    await settle(() => tabs.shown.length);
    assert.deepEqual(tabs.shown, [id], tab);
  }
  const unknown = fakeTabs();
  await soaPage(t, { query: '&tab=unknown', tabs: unknown });
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(unknown.shown, []);

  const ui = await soaPage(t);
  ui.el('soaIsmsEffectivenessTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'ismseffectiveness');
  ui.el('soaIsmsOrgAssetsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'ismsorgassets');
  ui.el('soaPestleTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'pestle');
  assert.equal(new URL(ui.window.location.href).searchParams.get('framework'), 'A');
});
