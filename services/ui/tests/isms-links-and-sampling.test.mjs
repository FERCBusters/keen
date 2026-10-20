// ISMS register entry points: links that open a record for editing or for a new
// metric entry, sampling a register row into an audit, table filters, the
// objectives board when a move fails, and BookStack documents on demand.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { page, settle } from './support/page-fixture.mjs';

const meta = {
  framework: 'A',
  users: [{ id: 'u1', username: 'alice' }],
  org_nodes: [{ id: 'n1', name: 'Security team', node_type: 'role' }],
  documents: [],
  document_types: ['policy'],
  assets: [],
  asset_categories: [],
  licenses: [],
  aws_accounts: [],
  business_processes: [],
  controls: [{ id: 'c1', ref: 'A.5.1', title: 'Policies' }],
  clauses: [{ id: 'k1', ref: '5.1', title: 'Leadership' }],
  access_statuses: ['Pending Approval', 'Approved'],
  effectiveness_metric_sources: [
    { id: 'ci', source: 'ci', label: 'Build server' },
    { id: 'ci', source: 'ci', label: 'Duplicate' },
    { source: 'other' },
  ],
};

function records() {
  return {
    objectives: [
      {
        id: 'o1',
        goal: 'Train operators',
        requirement: 'Awareness',
        metric: 'Completion rate',
        status: 'not_started',
        owner_user_id: 'u1',
        start_date: '2026-01-01',
        completion_target_date: '2026-06-30',
        completion_method: 'Course records',
        evaluation_method: 'Quarterly review',
        resource_requirements_text: 'Two trainers',
        resource_user_ids: ['u1'],
        controls: [{ id: 'c1', ref: 'A.5.1' }],
        clauses: [{ id: 'k1', ref: '5.1' }],
      },
      {
        id: 'o2',
        goal: 'Patch servers',
        requirement: 'Maintenance',
        status: 'completed',
        controls: [],
        clauses: [],
      },
    ],
    effectiveness_measures: [
      {
        id: 'e1',
        summary: 'Agent coverage',
        target_value: 95,
        target_unit: '%',
        threshold_operator: 'gte',
        controls: [],
        metric_entries: [],
      },
    ],
  };
}

async function isms(t, { query = '', me = { is_admin: true }, get, patch, post } = {}) {
  const sent = [];
  const ui = await page(t, 'isms', {
    query,
    me,
    setup(window) {
      window.d3 = d3;
    },
    get: async (u) => {
      sent.push({ method: 'GET', url: u.pathname + u.search });
      const answer = await get?.(u);
      if (answer !== undefined) return answer;
      if (u.pathname === '/api/v1/isms/meta') return structuredClone(meta);
      if (u.pathname === '/api/v1/isms/summary')
        return { framework: 'A', counts: {}, ...records() };
      if (u.pathname === '/api/v1/isms/document-folders') return [];
      if (u.pathname === '/api/v1/isms/people') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, body) => {
      sent.push({ method: 'PATCH', url: u.pathname + u.search, body });
      return patch ? patch(u, body) : body;
    },
    post: async (u, body) => {
      sent.push({
        method: 'POST',
        url: u.pathname + u.search,
        body: JSON.parse(JSON.stringify(body)),
      });
      return post ? post(u, body) : { ok: true };
    },
  });
  return { ...ui, sent };
}

test('an edit link opens the record in its form with every saved value', async (t) => {
  const ui = await isms(t, { query: '&edit=objective:o1' });

  await settle(() => ui.el('objGoal').value === 'Train operators');

  assert.equal(ui.el('objRequirement').value, 'Awareness');
  assert.equal(ui.el('objMetric').value, 'Completion rate');
  assert.equal(ui.el('objOwner').value, 'u1');
  assert.equal(ui.el('objStart').value, '2026-01-01');
  assert.equal(ui.el('objTarget').value, '2026-06-30');
  // A record not yet started appears as defined.
  assert.equal(ui.el('objStatus').value, 'defined');
  assert.equal(ui.el('objCompletion').value, 'Course records');
  assert.equal(ui.el('objEvaluation').value, 'Quarterly review');
  assert.equal(ui.el('objResourceText').value, 'Two trainers');
  const chosen = (id) => [...ui.el(id).selectedOptions].map((o) => o.value);
  assert.deepEqual(chosen('objResources'), ['u1']);
  assert.deepEqual(chosen('objControls'), ['c1']);
  assert.deepEqual(chosen('objClauses'), ['k1']);
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), null);
});

test('a record link opens a new metric entry for that measure', async (t) => {
  const ui = await isms(t, { query: '&record=e1' });

  await settle(() => ui.el('metricEntryFormCard').style.display === '');

  assert.equal(ui.el('metricMeasureId').value, 'e1');
  assert.equal(ui.el('metricUnit').value, '%');
  assert.equal(ui.el('metricSourceType').value, 'other');
  // Each collected source is offered once, after which comes Other.
  assert.deepEqual(
    [...ui.el('metricSourceType').options].map((o) => [o.value, o.textContent]),
    [
      ['ci', 'Build server (ci)'],
      ['other', 'Other'],
    ],
  );
});

test('a measure link highlights that measure in the register', async (t) => {
  const scrolled = [];
  const ui = await isms(t, { query: '&measure=e1' });
  ui.window.HTMLElement.prototype.scrollIntoView = function () {
    scrolled.push(this.dataset.ismsId);
  };

  await settle(() =>
    ui.document
      .querySelector('[data-isms-row="effectiveness"][data-isms-id="e1"]')
      ?.classList.contains('table-warning'),
  );
  assert.deepEqual(scrolled, ['e1']);
});

test('a register row samples its record into an audit with a link back to it', async (t) => {
  const ui = await isms(t, {
    query: '&tab=objectives',
    get: (u) =>
      u.pathname === '/api/v1/audits'
        ? { items: [{ id: 'a1', title: 'Annual audit', status: 'open' }] }
        : undefined,
  });
  await settle(() => ui.document.querySelector('[data-audit-sample-entity="objective"]'));

  ui.document.querySelector('[data-audit-sample-entity="objective"][data-isms-id="o1"]').click();

  await settle(() => ui.el('entityAuditSampleSelect')?.value === 'a1');
  assert.equal(ui.el('entityAuditSampleTitle').textContent, 'Sample “Train operators” into audit');
  ui.el('entityAuditSampleSubmit').click();
  await settle(() => ui.sent.some((c) => c.method === 'POST'));
  const post = ui.sent.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/v1/audits/a1/evidence');
  assert.deepEqual(post.body, {
    entity_type: 'isms_objective',
    entity_id: 'o1',
    evidence_url: '/isms-objective.html?id=o1&framework=A',
    title: 'Train operators',
    notes: '',
  });
});

test('Reset clears an objectives search and status filter', async (t) => {
  const ui = await isms(t, { query: '&tab=objectives' });
  await settle(() => ui.el('objectivesRows').querySelectorAll('[data-isms-row]').length === 2);
  const visible = () =>
    [...ui.el('objectivesRows').querySelectorAll('tr')]
      .filter((row) => row.style.display !== 'none')
      .map((row) => row.dataset.ismsId);
  ui.el('objectivesStatus').value = 'completed';
  ui.el('objectivesStatus').dispatchEvent(new ui.window.Event('change'));
  ui.el('objectivesTableFilter').value = 'patch';
  ui.el('objectivesTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.deepEqual(visible(), ['o2']);

  ui.document.querySelector('[data-isms-filter-clear="objectivesTableFilter"]').click();

  assert.equal(ui.el('objectivesTableFilter').value, '');
  assert.equal(ui.el('objectivesStatus').value, '');
  assert.deepEqual(visible(), ['o1', 'o2']);
  assert.equal(ui.document.activeElement, ui.el('objectivesTableFilter'));
});

test('a refused move on the objectives board is reported and the board reloaded', async (t) => {
  let summaries = 0;
  let failRefresh = false;
  const ui = await isms(t, {
    query: '&tab=objectives&view=kanban',
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/summary') return undefined;
      summaries += 1;
      if (failRefresh) throw new Error('Summary unavailable');
      return undefined;
    },
    patch: () => {
      throw new Error('Objective changed elsewhere');
    },
  });
  await settle(() => ui.el('objectivesPlanningView').querySelector('[data-status-id]'));
  const before = summaries;
  const move = () => {
    const select = ui.el('objectivesPlanningView').querySelector('[data-status-id="o1"]');
    select.value = 'in_progress';
    select.dispatchEvent(new ui.window.Event('change'));
  };

  move();

  await settle(() => /Objective changed elsewhere/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Error: Objective changed elsewhere');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  await settle(() => summaries === before + 1);
  assert.equal(ui.el('objectivesPlanningView').hasAttribute('aria-busy'), false);

  // When the board cannot be refreshed either, the message says to reload.
  failRefresh = true;
  move();
  await settle(() => /reload before trying again/.test(ui.el('status').textContent));
  assert.equal(
    ui.el('status').textContent,
    'Error: Objective changed elsewhere Unable to refresh objectives; reload before trying again.',
  );
});

test('BookStack documents load when first opened and open from a link', async (t) => {
  const me = { can_view_isms: true, bookstack_enabled: true };
  const snapshots = [];
  const ui = await isms(t, {
    me,
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/bookstack-sections') return undefined;
      snapshots.push(u);
      return { items: [] };
    },
  });
  const details = ui.el('bookstackDocuments');
  assert.equal(details.hidden, false);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(snapshots.length, 0);

  details.open = true;
  details.dispatchEvent(new ui.window.Event('toggle'));
  await settle(() => snapshots.length === 1);
  details.open = false;
  details.dispatchEvent(new ui.window.Event('toggle'));
  details.open = true;
  details.dispatchEvent(new ui.window.Event('toggle'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(snapshots.length, 1);

  const linked = await isms(t, {
    me,
    query: '&bookstack=1',
    get: (u) => (u.pathname === '/api/v1/isms/bookstack-sections' ? { items: [] } : undefined),
  });
  assert.equal(linked.el('bookstackDocuments').open, true);
});

test('BookStack documents that fail to open are reported and retried on the next opening', async (t) => {
  const me = { can_view_isms: true, bookstack_enabled: true };
  const snapshots = [];
  const ui = await isms(t, {
    me,
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/bookstack-sections') return undefined;
      snapshots.push(u);
      return { items: [] };
    },
  });
  const details = ui.el('bookstackDocuments');
  const toggle = (open) => {
    details.open = open;
    details.dispatchEvent(new ui.window.Event('toggle'));
  };
  // Without its content area the documents module cannot start.
  const content = ui.el('bookstackDocumentContent');
  content.remove();

  toggle(true);

  await settle(() => /Could not open BookStack documents/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').dataset.alert, 'danger');
  // Closing the section leaves it alone; opening it again tries again.
  details.append(content);
  toggle(false);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(snapshots.length, 0);
  toggle(true);
  await settle(() => snapshots.length === 1);
});

test('without BookStack the documents section stays hidden', async (t) => {
  const ui = await isms(t, { me: { can_view_isms: true } });
  assert.equal(ui.el('bookstackDocuments').hidden, true);
});
