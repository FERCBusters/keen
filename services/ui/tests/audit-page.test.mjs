import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
// Bodies are created in the page realm; compare them as plain JSON values.
const plain = (v) => (v instanceof FormData ? v : JSON.parse(JSON.stringify(v)));
const NodeFilter = { SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2, FILTER_SKIP: 3 };

// Mutations reload the audit; wait for the reload to finish before the window closes.
async function idle(ui) {
  const count = (path) =>
    ui.calls.filter((c) => c.method === 'GET' && c.url.split('?')[0] === path).length;
  await settle(() => count('/api/v1/audits/a1') === count('/api/v1/audits/a1/scope-map'));
  await new Promise((r) => setTimeout(r, 20));
}

function baseAudit(overrides = {}) {
  return {
    id: 'a1',
    title: '<img src=x onerror=alert(1)> Annual audit',
    framework_slug: 'A',
    status: 'open',
    audit_type: 'external',
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    created_by_user_id: 'u1',
    created_by_username: 'alice',
    executive_summary: '<p>Summary</p>',
    report_notes: '<p>Notes</p>',
    final_report: {
      download_url: '/api/v1/audits/a1/report/download',
      filename: '<b>report</b>.pdf',
      content_type: 'application/pdf',
      size_bytes: 12,
      uploaded_at: '2026-02-01T00:00:00Z',
    },
    attendees: [
      { id: 'at1', name: '<script>bob</script>', email: 'bob@example.test', role: 'Auditor' },
      { id: 'at2', user_id: 'u9', username: 'carol', email: 'carol@example.test' },
    ],
    findings: [
      {
        id: 'f1',
        kind: 'major_nc',
        status: 'in_progress',
        title: '<i>Missing review</i>',
        control: { ref: 'A1', title: 'Access' },
        description: '<p>Detail</p><script>alert(1)</script>',
      },
      { id: 'f2', kind: 'unknown_kind', status: 'accepted', title: 'Other' },
    ],
    evidence: [
      {
        id: 'e1',
        event_id: 'ev1',
        event: { id: 'ev1', summary: 'Login <b>event</b>', timestamp: '2026-01-02T00:00:00Z' },
        notes: 'Checked',
        added_at: '2026-01-03T00:00:00Z',
        added_by_username: 'alice',
        controls: [{ ref: 'A1' }],
      },
      {
        id: 'e2',
        evidence_url: 'javascript:alert(1)',
        title: 'Hostile link',
      },
      {
        id: 'e3',
        entity: {
          kind: 'Risk',
          title: 'Risk one',
          href: 'https://keen.example/risk.html?id=r1',
          subtitle: 'High',
        },
      },
    ],
    scoped_documents: [],
    scoped_controls: [{ id: 'c1', ref: 'A1', title: 'Access' }],
    scoped_clauses: [{ id: 'cl4', ref: '4', title: 'Context' }],
    scoped_swot: [{ id: 's1', title: 'Strength <b>one</b>', kind: 'strength' }],
    ...overrides,
  };
}

const controls = [
  { id: 'c1', ref: 'A1', title: 'Access' },
  { id: 'c2', ref: 'A2', title: 'Backup' },
  { id: 'c3', ref: 'A3', title: 'Crypto' },
  { id: 'cx', ref: '4', title: 'Clause row', type: 'clause' },
];
const clauses = [
  { id: 'cl4', ref: '4', title: 'Context' },
  { id: 'cl41', ref: '4.1', title: 'Organisation', parent_id: 'cl4' },
  { id: 'cl42', ref: '4.2', title: 'Interested parties', parent_id: 'cl4' },
  { id: 'cl5', ref: '5', title: 'Leadership' },
];
const documents = [
  {
    id: 'd1',
    title: 'Policy <u>doc</u>',
    document_type: 'policy',
    clauses: [{ id: 'cl5' }],
    controls: [{ id: 'c3' }],
  },
];

async function auditPage(t, options = {}) {
  const state = { audit: options.audit || baseAudit(), patched: [], put: null, posted: [] };
  const fail = options.fail || {};
  const ui = await page(t, 'audit', {
    query: '&id=a1',
    scripts: [d3],
    me: options.me || { is_admin: true, user_id: 'u1' },
    globals: { NodeFilter, ...(options.globals || {}) },
    setup: options.setup,
    vendor: options.vendor,
    get: async (u) => {
      switch (u.pathname) {
        case '/api/v1/audits/users':
          if (fail.users) throw new Error('Users offline');
          return {
            items: [
              { id: 'u2', username: 'zed' },
              { id: 'u9', username: 'carol', email: 'carol@example.test' },
            ],
          };
        case '/api/v1/isms/people':
          if (fail.people) throw new Error('People offline');
          return {
            items: state.people || [{ id: 'p1', name: 'Dana', email: 'dana@example.test' }],
          };
        case '/api/v1/audits/a1':
          return structuredClone(state.audit);
        case '/api/v1/controls':
          return { items: controls };
        case '/api/v1/clauses':
          return { items: clauses };
        case '/api/v1/graph/clause_control':
          return {
            links: [
              { source: 'clause:cl41', target: 'control:c2' },
              { source: 'clause:cl42', target: 'control:c1' },
              { source: '', target: 'x' },
            ],
          };
        case '/api/v1/isms/documents':
          return { items: documents };
        case '/api/v1/isms/swot':
          if (fail.swot) throw new Error('SWOT offline');
          return { items: [{ id: 's2', title: 'Threat', kind: 'threat' }] };
        case '/api/v1/audits/a1/scope-map':
          if (fail.scopeMap) throw new Error('<b>Map offline</b>');
          return (
            options.scopeMap || {
              clauses: [
                {
                  id: 'cl41',
                  ref: '4.1',
                  title:
                    'Organisation and its context, including the external and internal issues relevant to purpose, strategic direction and intended outcomes of the management system',
                },
                { id: 'cl5', ref: '5', title: '' },
              ],
              controls: [
                { id: 'c1', ref: 'A1', title: 'Access' },
                {
                  id: 'c2',
                  ref: 'A2',
                  title: 'Supercalifragilisticexpialidociousunbreakabletoken',
                },
              ],
              links: [
                { clause_id: 'cl41', control_id: 'c2', applicability: 'applicable' },
                { clause_id: 'cl41', control_id: 'c1', applicability: 'partially_applicable' },
                { clause_id: 'missing', control_id: 'c1' },
              ],
              summary: {},
            }
          );
        case '/api/v1/events/ev1':
          return {
            timestamp: '2026-01-02T00:00:00Z',
            source: 'auth',
            artifacts: [
              { id: 'art1', content_type: 'text/plain', captured_at: '2026-01-02T00:00:00Z' },
              { id: 'art2', content_type: 'image/png' },
            ],
          };
        default:
          throw new Error('Unexpected GET ' + u);
      }
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Patch rejected');
      if (u.pathname === '/api/v1/audits/a1' && b.status) state.audit.status = b.status;
      return { ok: true };
    },
    put: async (u, b) => {
      assert.equal(u.pathname, '/api/v1/audits/a1/scope');
      state.put = plain(b);
      return { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (u.pathname === '/api/v1/isms/people') {
        state.people = [
          { id: 'p1', name: 'Dana', email: 'dana@example.test' },
          { id: 'p2', name: b.name, email: b.email },
        ];
        return { id: 'p2' };
      }
      return { ok: true };
    },
    remove: async (u) => {
      state.removed = (state.removed || []).concat(u.pathname);
      return { ok: true };
    },
  });
  return { ui, state };
}

test('audit page renders details, KPIs, people, findings and evidence with hostile text inert', async (t) => {
  const { ui } = await auditPage(t);
  assert.equal(ui.el('title').textContent, baseAudit().title);
  assert.equal(ui.el('meta').querySelector('img'), null);
  assert.match(ui.el('meta').textContent, /A · External · Open · 2026-01-01 → 2026-01-31/);
  assert.match(ui.el('sampleEventLink').href, /events\.html\?framework=A$/);
  assert.equal(ui.el('auditTitle').value, baseAudit().title);
  assert.equal(ui.el('auditType').value, 'external');
  assert.equal(ui.el('kpiEvidence').textContent, '3');
  assert.equal(ui.el('kpiScope').textContent, '2');
  assert.equal(ui.el('kpiScope').title, '0 documents, 1 clauses, 1 controls');
  assert.equal(ui.el('kpiPeople').textContent, '2');
  assert.equal(ui.el('kpiFindings').textContent, '2');
  // Final report metadata is text; the download link keeps the API path.
  assert.equal(ui.el('reportInfo').querySelector('b'), null);
  assert.match(
    ui.el('reportInfo').textContent,
    /<b>report<\/b>\.pdf · application\/pdf · 12 bytes/,
  );
  assert.equal(
    ui.el('reportInfo').querySelector('a').getAttribute('href'),
    '/api/v1/audits/a1/report/download',
  );
  // Attendees and findings.
  assert.equal(ui.el('attendees').querySelector('script'), null);
  assert.match(ui.el('attendees').textContent, /<script>bob<\/script>/);
  assert.match(ui.el('attendees').textContent, /bob@example\.test · Auditor/);
  assert.equal(ui.el('attendees').querySelectorAll('[data-del-attendee]').length, 2);
  const findings = ui.el('findings').textContent;
  assert.match(findings, /Major Non-Conformity/);
  assert.match(findings, /In progress/);
  assert.match(findings, /<i>Missing review<\/i>/);
  assert.match(findings, /unknown_kind/);
  assert.match(findings, /Control: A1 Access/);
  assert.equal(ui.el('findings').querySelector('script'), null);
  assert.equal(ui.el('findings').querySelector('i'), null);
  // Evidence links: event -> event page, javascript: URL never becomes an href, entity URL kept.
  const links = [...ui.el('evidence').querySelectorAll('a.fw-semibold')].map((a) =>
    a.getAttribute('href'),
  );
  assert.match(links[0], /^\/event\.html\?id=ev1&framework=A$/);
  assert.equal(links[1], '#');
  assert.equal(links[2], 'https://keen.example/risk.html?id=r1');
  assert.match(ui.el('evidence').textContent, /Event evidence/);
  assert.match(ui.el('evidence').textContent, /Evidence URL/);
  assert.match(ui.el('evidence').textContent, /Risk[\s\S]*· High/);
  assert.equal(ui.el('evidence').querySelector('b'), null);
  // Administrators see delete and editable forms; templates controls are hidden.
  assert.ok(!ui.el('deleteAudit').classList.contains('d-none'));
  assert.ok(ui.el('templateScheduleCard').classList.contains('d-none'));
  assert.equal(ui.el('auditLockNotice').style.display, 'none');
  assert.equal(ui.el('auditTitle').disabled, false);
});

test('audit scope selection expands parent clauses and documents to children and linked controls', async (t) => {
  const { ui, state } = await auditPage(t);
  // The saved parent clause cl4 is normalised to its children and their linked controls.
  const checked = (sel) =>
    [...ui.document.querySelectorAll(sel)]
      .filter((x) => x.checked)
      .map((x) => x.getAttribute(sel.slice(1, -1)));
  assert.deepEqual(checked('[data-clause-id]'), ['cl41', 'cl42']);
  assert.deepEqual(checked('[data-control-id]').sort(), ['c1', 'c2']);
  // Clause-typed control rows are excluded from the control list.
  assert.equal(ui.document.querySelector('[data-control-id="cx"]'), null);
  assert.match(ui.el('clausesList').textContent, /child clauses selected/);
  assert.equal(ui.el('clauseScopeCount').textContent, '2 selected');

  // Selecting a document adds its clauses and controls.
  const doc = ui.document.querySelector('[data-document-id="d1"]');
  assert.equal(ui.el('documentsList').querySelector('u'), null);
  doc.checked = true;
  doc.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('documentScopeCount').textContent, '1 selected');
  assert.equal(ui.document.querySelector('[data-clause-id="cl5"]').checked, true);
  assert.equal(ui.document.querySelector('[data-control-id="c3"]').checked, true);

  // Unticking a control, ticking a SWOT candidate, then saving sends the exact scope.
  const c3 = ui.document.querySelector('[data-control-id="c3"]');
  c3.checked = false;
  c3.dispatchEvent(new ui.window.Event('change'));
  const swot = ui.document.querySelector('[data-swot-scope="s2"]');
  swot.checked = true;
  swot.dispatchEvent(new ui.window.Event('change'));
  ui.el('saveScope').click();
  await settle(() => state.put);
  assert.deepEqual(state.put.clauses.sort(), ['cl41', 'cl42', 'cl5']);
  assert.deepEqual(state.put.controls.sort(), ['c1', 'c2']);
  assert.deepEqual(state.put.documents, ['d1']);
  assert.deepEqual(state.put.swot_ids.sort(), ['s1', 's2']);
  await settle(() => /Scope saved/.test(ui.el('status').textContent));
  await idle(ui);
});

test('clicking a parent clause selects its children; filters and deselect buttons apply locally', async (t) => {
  const { ui } = await auditPage(t, {
    audit: baseAudit({ scoped_clauses: [], scoped_controls: [] }),
  });
  assert.equal(ui.el('deselectClauses').disabled, true);
  const parent = ui.document.querySelector('[data-clause-id="cl4"]');
  parent.checked = true;
  parent.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.document.querySelector('[data-clause-id="cl4"]').checked, false);
  assert.equal(ui.document.querySelector('[data-clause-id="cl41"]').checked, true);
  assert.match(ui.el('status').textContent, /Selected 2 child clauses and 2 linked controls/);
  assert.equal(ui.el('deselectClauses').disabled, false);

  // A leaf clause adds its linked controls; unticking removes only the clause.
  ui.el('deselectControls').click();
  assert.equal(ui.el('controlScopeCount').textContent, '0 selected');
  const leaf = ui.document.querySelector('[data-clause-id="cl41"]');
  leaf.checked = false;
  leaf.dispatchEvent(new ui.window.Event('change'));
  leaf.checked = true;
  leaf.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.document.querySelector('[data-control-id="c2"]').checked, true);

  ui.el('clauseFilter').value = 'leader';
  ui.el('clauseFilter').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(
    [...ui.document.querySelectorAll('[data-clause-id]')].map((x) => x.dataset.clauseId),
    ['cl5'],
  );
  ui.el('controlFilter').value = 'nothing-matches';
  ui.el('controlFilter').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('controlsList').textContent, /No controls match/);
  ui.el('documentFilter').value = 'zzz';
  ui.el('documentFilter').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('documentsList').textContent, /No ISMS documents match/);

  ui.el('deselectClauses').click();
  assert.equal(ui.el('clauseScopeCount').textContent, '0 selected');
  assert.equal(ui.el('deselectDocuments').disabled, true);
  ui.el('documentFilter').value = '';
  ui.el('documentFilter').dispatchEvent(new ui.window.Event('input'));
  const doc = ui.document.querySelector('[data-document-id="d1"]');
  doc.checked = true;
  doc.dispatchEvent(new ui.window.Event('change'));
  ui.el('deselectDocuments').click();
  assert.equal(ui.el('documentScopeCount').textContent, '0 selected');
  assert.match(ui.el('status').textContent, /All ISMS documents deselected/);
});

test('audit scope graph renders a bipartite SVG and reports unlinked items', async (t) => {
  const { ui } = await auditPage(t);
  const svg = ui.el('scopeHeatmap').querySelector('svg');
  assert.ok(svg);
  assert.equal(svg.querySelectorAll('.audit-scope-links path').length, 2);
  assert.equal(svg.querySelectorAll('path[stroke-dasharray="5 5"]').length, 1);
  assert.equal(svg.querySelectorAll('a.scope-clause').length, 2);
  assert.match(
    svg.querySelector('a.scope-clause').getAttribute('href'),
    /clause\.html\?id=cl41&framework=A/,
  );
  assert.match(svg.querySelector('a.scope-control').getAttribute('href'), /control\.html\?id=c/);
  // Long labels are wrapped and ellipsised to fit.
  const tspans = [...svg.querySelectorAll('.audit-scope-clause text tspan')].map(
    (x) => x.textContent,
  );
  assert.ok(tspans.some((x) => x.endsWith('…')));
  assert.match(
    ui.el('scopeHeatmapSummary').textContent,
    /2 clause\(s\), 2 control\(s\), 3 clause\/control relationship\(s\)/,
  );
  assert.match(
    ui.el('scopeHeatmapSummary').textContent,
    /1 selected clause\(s\) have no selected control link/,
  );
});

test('scope graph nodes open their clause or control; Ctrl or Cmd opens a new tab', async (t) => {
  const location = { href: 'https://keen.example/audit.html?id=a1&framework=A' };
  const opened = [];
  const { ui } = await auditPage(t, {
    globals: { location },
    setup(window) {
      window.open = (...args) => void opened.push(args);
    },
  });
  const svg = ui.el('scopeHeatmap').querySelector('svg');
  const click = (link, init = {}) => {
    const event = new ui.window.MouseEvent('click', { bubbles: true, cancelable: true, ...init });
    link.querySelector('g').dispatchEvent(event);
    return event;
  };
  const clause = svg.querySelector('a.scope-clause');
  const [control, other] = svg.querySelectorAll('a.scope-control');

  // The page navigates itself, so the link's own navigation is cancelled.
  assert.equal(click(clause).defaultPrevented, true);
  assert.equal(location.href, clause.getAttribute('href'));
  click(control, { ctrlKey: true });
  click(other, { metaKey: true });

  assert.deepEqual(opened, [
    [control.getAttribute('href'), '_blank', 'noopener'],
    [other.getAttribute('href'), '_blank', 'noopener'],
  ]);
  assert.match(control.getAttribute('href'), /^\/control\.html\?id=c\d&framework=A$/);
  assert.equal(location.href, clause.getAttribute('href'));
});

test('audit scope graph shows guidance for empty scope and escapes load failures', async (t) => {
  const empty = await auditPage(t, { scopeMap: { clauses: [], controls: [], links: [] } });
  assert.match(
    empty.ui.el('scopeHeatmap').textContent,
    /Select at least one clause and one control/,
  );
  const failed = await auditPage(t, { fail: { scopeMap: true } });
  assert.match(
    failed.ui.el('scopeHeatmap').textContent,
    /Failed to load scope visualisation: Error: <b>Map offline<\/b>/,
  );
  assert.equal(failed.ui.el('scopeHeatmap').querySelector('b'), null);
});

test('saving audit details sends the edited fields and reloads', async (t) => {
  const { ui, state } = await auditPage(t);
  ui.el('auditTitle').value = 'Renamed';
  ui.el('auditStatus').value = 'in_progress';
  ui.el('detailsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/audits/a1',
    body: {
      title: 'Renamed',
      framework_slug: 'A',
      status: 'in_progress',
      audit_type: 'external',
      start_date: '2026-01-01',
      end_date: '2026-01-31',
      executive_summary: '<p>Summary</p>',
    },
  });
  await settle(() => /Audit details saved/.test(ui.el('status').textContent));
  await idle(ui);
});

test('audit details with a date in the wrong format are not saved', async (t) => {
  const invalid = new Set();
  const { ui, state } = await auditPage(t, {
    vendor: {
      // The design system's date field reports whether its text is a date.
      wireIsoDateTextField: (input) => ({ syncHiddenFromText: () => !invalid.has(input.id) }),
    },
  });
  for (const field of ['auditStart', 'auditEnd']) {
    invalid.clear();
    invalid.add(field);
    ui.el('detailsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
    assert.equal(ui.el('status').textContent, 'Enter dates using the configured date format.');
    assert.equal(ui.el('status').dataset.alert, 'warning');
  }
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(state.patched, []);

  invalid.clear();
  ui.el('detailsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].body.start_date, '2026-01-01');
  await settle(() => /Audit details saved/.test(ui.el('status').textContent));
  await idle(ui);
});

test('report notes, URL evidence and evidence notes are saved through their endpoints', async (t) => {
  const { ui, state } = await auditPage(t);
  ui.el('auditNotes').value = '<p>Updated</p>';
  ui.el('reportNotesForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0].body, { report_notes: '<p>Updated</p>' });

  ui.el('urlEvidence').value = '   ';
  ui.el('urlEvidenceForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('urlEvidence').value = ' https://evidence.example/item ';
  ui.el('urlEvidenceForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/audits/a1/evidence',
    body: { evidence_url: 'https://evidence.example/item' },
  });
  await settle(() => ui.el('urlEvidence').value === '');

  ui.document.querySelector('[data-evidence-notes="e1"]').value = 'Verified screenshot';
  ui.document.querySelector('[data-save-evidence="e1"]').click();
  await settle(() => state.patched.length === 2);
  assert.deepEqual(state.patched[1], {
    path: '/api/v1/audits/a1/evidence/e1',
    body: { notes: 'Verified screenshot' },
  });
  ui.document.querySelector('[data-del-evidence="e2"]').click();
  await settle(() => state.removed?.includes('/api/v1/audits/a1/evidence/e2'));
  await idle(ui);
});

test('findings can be added, edited and removed', async (t) => {
  const { ui, state } = await auditPage(t);
  ui.el('findingKind').value = 'ofi';
  ui.el('findingTitle').value = 'Improve logging';
  ui.el('findingStatus').value = 'open';
  ui.el('findingControl').value = 'A2';
  ui.el('findingDesc').value = '<p>Desc</p>';
  ui.el('findingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/audits/a1/findings',
    body: {
      kind: 'ofi',
      title: 'Improve logging',
      status: 'open',
      control: 'A2',
      description: '<p>Desc</p>',
    },
  });
  await settle(() => ui.el('findingTitle').value === '');

  const form = ui.document.querySelector('[data-finding-edit-form="f1"]');
  assert.ok(form.classList.contains('d-none'));
  ui.document.querySelector('[data-edit-finding="f1"]').click();
  assert.ok(!form.classList.contains('d-none'));
  ui.document.querySelector('[data-cancel-finding="f1"]').click();
  assert.ok(form.classList.contains('d-none'));
  ui.document.querySelector('[data-finding-edit-title="f1"]').value = 'Edited';
  ui.document.querySelector('[data-finding-edit-status="f1"]').value = 'closed';
  ui.document.querySelector('[data-finding-edit-control="f1"]').value = '';
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].path, '/api/v1/audits/a1/findings/f1');
  assert.equal(state.patched[0].body.kind, 'major_nc');
  assert.equal(state.patched[0].body.title, 'Edited');
  assert.equal(state.patched[0].body.status, 'closed');
  assert.equal(state.patched[0].body.control, null);
  ui.document.querySelector('[data-del-finding="f2"]').click();
  await settle(() => state.removed?.includes('/api/v1/audits/a1/findings/f2'));
  await idle(ui);
});

test('attendees can be chosen from users or people and validation errors are shown', async (t) => {
  const { ui, state } = await auditPage(t);
  // Users are sorted by username in the attendee selector.
  assert.deepEqual(
    [...ui.el('attendeeUserId').options].map((o) => o.value),
    ['', 'u9', 'u2'],
  );
  ui.el('attendeeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Choose a Person or enter a name/.test(ui.el('status').textContent));

  ui.el('attendeeUserId').value = 'u9';
  ui.el('attendeeUserId').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('attendeeName').value, 'carol');
  assert.equal(ui.el('attendeeEmail').value, 'carol@example.test');
  ui.el('attendeePersonId').value = 'p1';
  ui.el('attendeeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Select a Person or KEEN user/.test(ui.el('status').textContent));
  assert.equal(state.posted.length, 0);

  ui.el('attendeePersonId').value = '';
  ui.el('attendeeRole').value = 'Lead';
  ui.el('attendeeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0].body, {
    user_id: 'u9',
    person_id: null,
    name: 'carol',
    email: 'carol@example.test',
    role: 'Lead',
  });
  await settle(() => ui.el('attendeeName').value === '');

  // Choosing a directory person fills their details and clears the user selection.
  ui.el('attendeeUserId').value = 'u2';
  ui.el('attendeePersonId').value = 'p1';
  ui.el('attendeePersonId').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('attendeeUserId').value, '');
  assert.equal(ui.el('attendeeName').value, 'Dana');
  assert.equal(ui.el('attendeeCreatePerson').hidden, true);

  ui.document.querySelector('[data-del-attendee="at1"]').click();
  await settle(() => state.removed?.includes('/api/v1/audits/a1/attendees/at1'));
  await idle(ui);
});

test('a new attendee name can be saved to the people directory', async (t) => {
  const { ui, state } = await auditPage(t);
  ui.el('attendeeName').value = 'dana';
  ui.el('attendeeName').dispatchEvent(new ui.window.Event('input'));
  // An existing person is matched case-insensitively and reused.
  assert.equal(ui.el('attendeePersonId').value, 'p1');
  ui.el('attendeePersonId').value = '';
  ui.el('attendeeName').value = 'Erin';
  ui.el('attendeeEmail').value = 'erin@example.test';
  ui.el('attendeeEmail').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('attendeeCreatePerson').hidden, false);
  ui.el('attendeeCreatePerson').click();
  await settle(() => ui.el('attendeePersonId').value === 'p2');
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/isms/people',
    body: { name: 'Erin', email: 'erin@example.test' },
  });
  assert.equal(ui.el('attendeeCreatePerson').hidden, true);
  assert.match(ui.el('status').textContent, /Person saved to the directory/);
  await idle(ui);
});

test('locked audits show a reopen path and reject edits until reopened', async (t) => {
  const { ui, state } = await auditPage(t, { audit: baseAudit({ status: 'completed' }) });
  assert.match(ui.el('auditLockNotice').textContent, /This audit is Completed/);
  assert.equal(ui.el('auditLockNotice').style.display, '');
  assert.equal(ui.el('auditTitle').disabled, true);
  assert.equal(ui.el('auditStatus').disabled, false);
  assert.equal(
    ui.document.querySelector('#detailsForm button[type="submit"]').textContent,
    'Reopen audit',
  );
  assert.equal(ui.el('saveScope').disabled, true);
  assert.equal(ui.document.querySelector('[data-del-attendee]'), null);
  assert.equal(ui.document.querySelector('[data-edit-finding]'), null);
  assert.equal(ui.document.querySelector('[data-evidence-notes="e1"]').readOnly, true);
  assert.ok(ui.document.querySelector('[data-control-id]').disabled);

  // Keeping a locked status is refused locally.
  ui.el('detailsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Choose Open or In progress/.test(ui.el('status').textContent));
  assert.equal(state.patched.length, 0);
  // Programmatic edits through disabled controls are still refused.
  ui.el('saveScope').disabled = false;
  ui.el('saveScope').click();
  await settle(() => /Reopen it to Open or In progress/.test(ui.el('status').textContent));
  assert.equal(state.put, null);

  // Reopening sends only the status change.
  ui.el('auditStatus').value = 'open';
  ui.el('auditTitle').value = 'Ignored while locked';
  ui.el('detailsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0].body, { status: 'open' });
  await settle(() => /Audit reopened/.test(ui.el('status').textContent));
  await settle(() => ui.el('auditTitle').disabled === false);
  await idle(ui);
});

test('audit readers without management permission receive a read-only view', async (t) => {
  const { ui, state } = await auditPage(t, { me: { can_view_audits: true, user_id: 'u3' } });
  for (const id of [
    'detailsForm',
    'reportForm',
    'reportNotesForm',
    'urlEvidenceForm',
    'attendeeForm',
    'findingForm',
  ])
    assert.ok(ui.el(id).classList.contains('d-none'), id);
  for (const id of ['saveScope', 'deselectDocuments', 'deselectClauses', 'deselectControls'])
    assert.ok(ui.el(id).classList.contains('d-none'), id);
  assert.ok(ui.el('deleteAudit').classList.contains('d-none'));
  assert.match(ui.el('status').textContent, /read-only access/);
  // Readers do not request the user directory used for attendee selection.
  assert.ok(!ui.calls.some((c) => c.url === '/api/v1/audits/users'));
  assert.equal(ui.document.querySelector('[data-del-attendee]'), null);
  assert.equal(ui.document.querySelector('[data-save-evidence]'), null);
  ui.el('deleteAudit').click();
  assert.match(ui.el('status').textContent, /Admin role required/);
  assert.equal(state.removed, undefined);
});

test('audit managers who did not create the audit cannot edit it', async (t) => {
  const { ui, state } = await auditPage(t, {
    me: { can_manage_audits: true, can_view_events: true, user_id: 'u2' },
  });
  assert.equal(ui.el('auditTitle').disabled, true);
  assert.equal(ui.el('auditStatus').disabled, true);
  assert.equal(ui.document.querySelector('#detailsForm button[type="submit"]').disabled, true);
  assert.equal(ui.el('saveScope').disabled, true);
  assert.equal(ui.el('findingTitle').disabled, true);
  assert.equal(ui.document.querySelector('[data-swot-scope]').disabled, true);
  assert.equal(ui.document.querySelector('[data-del-attendee]'), null);
  assert.equal(ui.document.querySelector('[data-edit-finding]'), null);
  assert.ok(ui.el('deleteAudit').classList.contains('d-none'));
  // Re-enabling a control in the DOM does not bypass the page's edit check.
  ui.el('deselectControls').disabled = false;
  ui.el('deselectControls').click();
  assert.match(ui.el('status').textContent, /Only the audit creator or an audit administrator/);
  ui.el('findingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Only the audit creator/.test(ui.el('status').textContent));
  assert.equal(state.posted.length, 0);
});

test('audit creators who are managers can edit their own audit but cannot delete it', async (t) => {
  const { ui } = await auditPage(t, {
    me: { can_manage_audits: true, can_view_events: true, user_id: 'u1' },
  });
  assert.ok(!ui.el('detailsForm').classList.contains('d-none'));
  assert.equal(ui.el('auditTitle').disabled, false);
  assert.ok(ui.el('deleteAudit').classList.contains('d-none'));
});

test('audit deletion requires confirmation and returns to the audit list', async (t) => {
  const cancelled = await auditPage(t, { globals: { confirm: () => false } });
  cancelled.ui.el('deleteAudit').click();
  assert.equal(cancelled.state.removed, undefined);

  let prompt = '';
  const { ui, state } = await auditPage(t, { globals: { confirm: (m) => ((prompt = m), true) } });
  ui.el('deleteAudit').click();
  await settle(() => ui.el('status').textContent === 'Audit deleted');
  assert.deepEqual(state.removed, ['/api/v1/audits/a1']);
  assert.match(prompt, /Delete audit "<img src=x onerror=alert\(1\)> Annual audit"\?/);
});

test('final report upload requires a file and posts multipart form data', async (t) => {
  const { ui, state } = await auditPage(t);
  ui.el('reportForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Choose a report file first/.test(ui.el('status').textContent));
  Object.defineProperty(ui.el('reportFile'), 'files', {
    value: [new File(['%PDF'], 'final.pdf')],
    configurable: true,
  });
  ui.el('reportForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].path, '/api/v1/audits/a1/report');
  assert.equal(state.posted[0].body.get('file').name, 'final.pdf');
  await settle(() => /Final report uploaded/.test(ui.el('status').textContent));
  await idle(ui);
});

test('failed mutations surface the error and keep the page usable', async (t) => {
  const { ui } = await auditPage(t, { patchFail: true });
  ui.el('detailsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Failed: Error: Patch rejected/.test(ui.el('status').textContent));
  ui.el('reportNotesForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Failed: Error: Patch rejected/.test(ui.el('status').textContent));
});

test('scheduled audit templates edit their schedule and hide sampling/export actions', async (t) => {
  const { ui, state } = await auditPage(t, {
    audit: baseAudit({
      status: 'template',
      schedule_date_rule: 'first_monday_of_month',
      schedule_anchor_month: 3,
      schedule_next_run_date: '2027-03-01',
      schedule_last_run_date: '2026-03-02',
      schedule_recurrence: 'yearly',
      schedule_interval: 1,
    }),
  });
  assert.ok(!ui.el('templateScheduleCard').classList.contains('d-none'));
  assert.ok(ui.el('btnAuditZip').classList.contains('d-none'));
  assert.ok(ui.el('sampleEventLink').classList.contains('d-none'));
  assert.ok(ui.el('deleteAudit').classList.contains('d-none'));
  assert.equal(ui.el('auditStatus').disabled, true);
  assert.equal(ui.el('urlEvidence').disabled, true);
  assert.equal(
    ui.el('tmplScheduleInfo').textContent,
    'Last materialised: 2026-03-02 · Next run: 2027-03-01',
  );
  // The fuzzy rule shows its computed first matching weekday.
  assert.equal(ui.el('tmplExactDateFields').style.display, 'none');
  assert.equal(ui.el('tmplFuzzyMonthLabel').textContent, 'First Monday of');
  assert.equal(ui.el('tmplScheduleStart').value, '2027-03-01');
  assert.match(
    ui.el('tmplSchedulePreview').textContent,
    /Next run will be 2027-03-01; future runs keep the first Monday rule/,
  );
  assert.equal(ui.el('tmplRecurrence').querySelector('option[value="weekly"]').disabled, true);

  ui.el('tmplDateRule').value = 'first_weekday_of_month';
  ui.el('tmplFuzzyMonth').value = '8';
  ui.el('tmplFuzzyYear').value = '2026';
  ui.el('tmplFuzzyYear').dispatchEvent(new ui.window.Event('input'));
  // 1 August 2026 is a Saturday, so the first weekday is Monday 3 August.
  assert.equal(ui.el('tmplScheduleStart').value, '2026-08-03');
  ui.el('templateScheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/audits/scheduled/a1',
    body: {
      start_date: '2026-08-03',
      schedule_date_rule: 'first_weekday_of_month',
      schedule_anchor_month: 8,
      schedule_recurrence: 'yearly',
      schedule_interval: 1,
      schedule_until_date: null,
    },
  });

  // Exact dates re-enable weekly recurrence and send no anchor month.
  ui.el('tmplDateRule').value = 'exact';
  ui.el('tmplDateRule').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('tmplExactDateFields').style.display, '');
  assert.equal(ui.el('tmplSchedulePreview').textContent, '');
  ui.el('tmplScheduleStart').value = '2026-09-09';
  ui.el('tmplRecurrence').value = 'weekly';
  ui.el('templateScheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 2);
  assert.equal(state.patched[1].body.schedule_anchor_month, null);
  assert.equal(state.patched[1].body.schedule_recurrence, 'weekly');

  // Template details are saved through the scheduled-template endpoint without a status.
  ui.el('auditTitle').value = 'Template title';
  ui.el('detailsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 3);
  assert.equal(state.patched[2].path, '/api/v1/audits/scheduled/a1');
  assert.equal(state.patched[2].body.title, 'Template title');
  assert.equal('status' in state.patched[2].body, false);
  await idle(ui);
});

test('a template with no history explains that no audit has materialised', async (t) => {
  const { ui } = await auditPage(t, { audit: baseAudit({ status: 'template' }) });
  assert.equal(
    ui.el('tmplScheduleInfo').textContent,
    'This template has not materialised an audit yet.',
  );
  assert.equal(ui.el('tmplFuzzyDateFields').style.display, 'none');
});

test('disabled features skip their catalogue requests and keep the saved scope', async (t) => {
  const off = {
    controls: false,
    clauses: false,
    visualisations: false,
    documents: false,
    swot: false,
    people: false,
  };
  const { ui, state } = await auditPage(t, {
    me: { is_admin: true, user_id: 'u1', features: { enabled: off } },
  });
  for (const path of [
    '/api/v1/controls',
    '/api/v1/clauses',
    '/api/v1/graph/clause_control',
    '/api/v1/isms/documents',
    '/api/v1/isms/swot',
    '/api/v1/isms/people',
  ])
    assert.ok(!ui.calls.some((c) => c.url.startsWith(path + '?') || c.url === path), path);
  assert.equal(ui.el('swotScope'), null);
  assert.equal(ui.document.querySelector('[data-control-id="c1"]').checked, true);
  ui.el('saveScope').click();
  await settle(() => state.put);
  assert.deepEqual(state.put, { clauses: ['cl4'], controls: ['c1'] });
});

test('unavailable SWOT, people and user lists degrade without blocking the audit', async (t) => {
  const { ui, state } = await auditPage(t, { fail: { swot: true, people: true, users: true } });
  assert.equal(ui.el('title').textContent, baseAudit().title);
  assert.deepEqual(
    [...ui.el('attendeeUserId').options].map((o) => o.value),
    [''],
  );
  assert.deepEqual(
    [...ui.el('attendeePersonId').options].map((o) => o.value),
    [''],
  );
  const box = ui.document.querySelector('[data-swot-scope="s1"]');
  assert.equal(box.disabled, true);
  ui.el('saveScope').click();
  await settle(() => state.put);
  assert.equal('swot_ids' in state.put, false);
});

function readZip(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let end = bytes.length - 22;
  assert.equal(view.getUint32(end, true), 0x06054b50);
  const count = view.getUint16(end + 10, true);
  let cd = view.getUint32(end + 16, true);
  const files = [];
  for (let i = 0; i < count; i++) {
    assert.equal(view.getUint32(cd, true), 0x02014b50);
    const size = view.getUint32(cd + 20, true);
    const nameLen = view.getUint16(cd + 28, true);
    const local = view.getUint32(cd + 42, true);
    const name = new TextDecoder().decode(bytes.slice(cd + 46, cd + 46 + nameLen));
    const dataStart = local + 30 + view.getUint16(local + 26, true);
    files.push({ name, data: bytes.slice(dataStart, dataStart + size) });
    cd += 46 + nameLen;
  }
  return files;
}

async function auditZipExport(t, responses, audit = baseAudit()) {
  const fetched = [];
  let zip = null;
  const ctx = new Proxy({}, { get: (_, k) => (k in {} ? undefined : () => {}), set: () => true });
  class CaptureUrl extends URL {
    static createObjectURL(blob) {
      zip = blob;
      return 'blob:keen';
    }
    static revokeObjectURL() {}
  }
  const captured = [];
  const { ui } = await auditPage(t, {
    audit,
    me: {
      is_admin: true,
      user_id: 'u1',
      event_data_masking: 'samples',
      sample_pdf_footer: ' Footer ',
    },
    globals: {
      URL: CaptureUrl,
      fetch: async (url) => {
        fetched.push(url);
        const [body, headers] = responses[url];
        return new Response(body, { headers });
      },
    },
    setup(window) {
      window.HTMLCanvasElement.prototype.getContext = () => ctx;
      window.HTMLCanvasElement.prototype.toBlob = (cb) =>
        cb(new Blob([new Uint8Array([0xff, 0xd8, 0xff])]));
      window.html2canvas = async (node) => {
        // The captured clone replaces form controls with static text and masks identifiers.
        assert.equal(node.querySelector('#reportForm'), null);
        assert.equal(node.querySelector('button'), null);
        assert.ok(node.querySelector('.export-scope-list'));
        assert.ok(!node.textContent.includes('bob@example.test'));
        assert.match(node.textContent, /\[MASKED_EMAIL\]/);
        captured.push(node.textContent);
        return { width: 1000, height: 3000 };
      };
    },
  });
  ui.el('btnAuditZip').click();
  await settle(() => zip);
  const files = readZip(new Uint8Array(await zip.arrayBuffer()));
  return { ui, fetched, files, captured, names: files.map((f) => f.name) };
}

test('audit ZIP export bundles the page PDF, final report and masked sample artifacts', async (t) => {
  const responses = {
    '/api/v1/audits/a1/report/download': [
      'final',
      {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="_b_report__b_.pdf"; filename*=UTF-8''%3Cb%3Ereport%3C_b%3E.pdf`,
      },
    ],
    '/api/v1/artifacts/art1/download?sample_export=1': [
      'log',
      {
        'Content-Disposition': `attachment; filename="artifact-art1.txt"; filename*=UTF-8''artifact-art1.txt`,
      },
    ],
    '/api/v1/artifacts/art2/download?sample_export=1': ['png', { 'Content-Type': 'image/png' }],
  };
  const { ui, fetched, files, names } = await auditZipExport(t, responses);
  assert.ok(ui.calls.some((c) => c.url === '/api/v1/events/ev1?framework=A&sample_export=1'));
  assert.deepEqual(fetched, Object.keys(responses));
  assert.equal(
    names[0],
    'Keen Audit - a1 - img src=x onerror=alert(1) Annual audit-audit-page.pdf',
  );
  // Header-unsafe characters from the server-sanitised download name are replaced.
  assert.equal(names[1], 'final-report/b report _b .pdf');
  // Colons in the event timestamp are not valid in ZIP paths on every platform.
  assert.equal(names[2], 'sampled-evidence/001 2026-01-02T00-00-00Z auth ev1/artifact-art1.txt');
  assert.equal(names[3], 'sampled-evidence/001 2026-01-02T00-00-00Z auth ev1/artifact-art2.png');
  assert.equal(new TextDecoder().decode(files[1].data), 'final');
  const pdf = new TextDecoder().decode(files[0].data);
  assert.ok(pdf.startsWith('%PDF-1.4'));
  assert.match(pdf, /\/Type \/Pages \/Count 3/);
  assert.match(
    ui.el('status').textContent,
    /Audit ZIP download started \(final report, 2 sampled evidence attachments\)/,
  );
  assert.equal(ui.el('btnAuditZip').disabled, false);
});

test('the audit ZIP capture masks addresses that end a sentence', async (t) => {
  const audit = baseAudit();
  audit.evidence[0].notes =
    'Checked a login from 192.0.2.1. Contact dana@example.test. Peer fe80::1.';
  const responses = {
    '/api/v1/audits/a1/report/download': ['final', { 'Content-Type': 'application/pdf' }],
    '/api/v1/artifacts/art1/download?sample_export=1': ['log', {}],
    '/api/v1/artifacts/art2/download?sample_export=1': ['png', {}],
  };
  const { captured } = await auditZipExport(t, responses, audit);
  const text = captured.join('\n');
  assert.match(
    text,
    /Checked a login from \[MASKED_IP\]\. Contact \[MASKED_EMAIL\]\. Peer \[MASKED_IP\]\./,
  );
  assert.doesNotMatch(text, /192\.0\.2\.1|dana@example\.test|fe80::1/);
});

test('the audit ZIP capture masks an IPv6 address written in full with an IPv4 tail', async (t) => {
  const audit = baseAudit();
  audit.evidence[0].notes =
    'Mapped peer 0:0:0:0:0:ffff:192.0.2.1 and NAT64 64:ff9b:0:0:0:0:198.51.100.7.';
  const responses = {
    '/api/v1/audits/a1/report/download': ['final', { 'Content-Type': 'application/pdf' }],
    '/api/v1/artifacts/art1/download?sample_export=1': ['log', {}],
    '/api/v1/artifacts/art2/download?sample_export=1': ['png', {}],
  };
  const { captured } = await auditZipExport(t, responses, audit);
  const text = captured.join('\n');
  assert.match(text, /Mapped peer \[MASKED_IP\] and NAT64 \[MASKED_IP\]\./);
  assert.doesNotMatch(text, /ffff|ff9b/);
});

test('audit ZIP entries never contain dot segments from download names', async (t) => {
  // The server strips path separators from download names, but a bare "." or
  // ".." must still not become a parent-directory reference inside the archive.
  const responses = {
    '/api/v1/audits/a1/report/download': [
      'final',
      { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename=".."' },
    ],
    '/api/v1/artifacts/art1/download?sample_export=1': [
      'log',
      { 'Content-Disposition': 'attachment; filename="..."' },
    ],
    '/api/v1/artifacts/art2/download?sample_export=1': [
      'png',
      { 'Content-Disposition': 'attachment; filename="."' },
    ],
  };
  const { names } = await auditZipExport(t, responses);
  assert.deepEqual(names.slice(1), [
    'final-report/file',
    'sampled-evidence/001 2026-01-02T00-00-00Z auth ev1/file',
    'sampled-evidence/001 2026-01-02T00-00-00Z auth ev1/file (2)',
  ]);
  for (const name of names)
    assert.ok(!name.split('/').some((part) => /^\.+$/.test(part) || part === ''), name);
});

test('audit ZIP export reports a missing renderer without leaving the button disabled', async (t) => {
  const { ui } = await auditPage(t);
  ui.el('btnAuditZip').click();
  await settle(() => /html2canvas is not available/.test(ui.el('status').textContent));
  assert.equal(ui.el('btnAuditZip').disabled, false);
  assert.equal(ui.document.querySelector('.export-capture'), null);
});
