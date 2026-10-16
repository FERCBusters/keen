import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

function risk(overrides = {}) {
  return {
    id: 'r1',
    framework: 'A',
    asset: '<img src=x onerror=alert(1)>Payroll DB',
    threat_summary: 'Ransomware',
    updated_at: '2026-04-01T00:00:00Z',
    risk_types: ['Confidentiality', '<b>Integrity</b>'],
    category: { name: 'Technology' },
    subcategory: { name: 'Databases' },
    risk_owner: { username: 'dana' },
    risk_score: 16,
    register_likelihood: 4,
    register_impact: 4,
    residual_risk_score: 6,
    register_residual_likelihood: 2,
    register_residual_impact: 3,
    note: 'Review quarterly',
    controls: [
      { id: 'c1', ref: 'A1', title: '<i>Backups</i>', in_scope: true },
      { id: 'c2', ref: 'A2', title: 'Restore tests', in_scope: false },
    ],
    mitigator_context: {
      source: 'keen_mitigator',
      risk_weights: { Confidentiality: 3, Integrity: 2 },
      impact_flags: { personal_data: true, safety_impact: false },
      selected_control_ids: ['c1'],
      suggested_controls: [
        { ref: 'A1', confidence: 'high' },
        { ref: '<b>A9</b>', confidence: 'low' },
      ],
    },
    ...overrides,
  };
}

async function riskPage(t, options = {}) {
  const state = {
    risk: options.risk || risk(),
    posted: [],
    patched: [],
    removed: [],
    threads: options.threads || [
      {
        id: 't1',
        status: 'unanswered',
        created_by_username: 'erin',
        posts: [{ author_username: 'erin', body: 'Why <b>16</b>?' }],
      },
    ],
  };
  const fail = options.fail || {};
  const ui = await page(t, 'risk', {
    query: options.query ?? '&id=r1',
    me: options.me || { is_admin: true, user: 'root' },
    globals: options.globals,
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/risks/r1') {
        if (fail.risk) throw new Error('Risk offline');
        return structuredClone(state.risk);
      }
      if (p === '/api/v1/risks/r1/changelog') {
        if (fail.changelog) throw new Error('log');
        return {
          items: [
            {
              summary: 'Risk scored',
              changed_by_username: 'root',
              changes: [{ field: 'risk_score', before: 9, after: 16 }],
            },
          ],
        };
      }
      if (p === '/api/v1/questions/entities/risk/r1') {
        if (fail.questions) throw new Error('Questions <i>offline</i>');
        return { threads: state.threads };
      }
      if (p === '/api/v1/isms/swot') {
        if (fail.swot) throw new Error('denied');
        assert.equal(u.searchParams.get('target_type'), 'risk');
        assert.equal(u.searchParams.get('target_id'), 'r1');
        return {
          items: options.swot ?? [
            { id: 's1', title: 'Old <b>backups</b>', kind: 'weakness', significance: 4 },
          ],
          total: 1,
        };
      }
      if (p === '/api/v1/audits')
        return { items: [{ id: 'au1', title: 'Audit', status: 'in_progress' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Post refused');
      return { ok: true };
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Patch refused');
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.deleteFail) throw new Error('Delete refused');
      return { ok: true };
    },
  });
  await settle(() => ui.el('swotRelatedPane')?.textContent || ui.el('status').textContent);
  return { ui, state };
}

const tab = (ui, id) => ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));

test('risk details render scores, types, controls and mitigator context as text', async (t) => {
  const { ui } = await riskPage(t);
  assert.equal(
    ui.el('riskTitle').textContent,
    '<img src=x onerror=alert(1)>Payroll DB — Ransomware',
  );
  assert.equal(ui.el('riskMeta').textContent, 'A • updated 2026-04-01T00:00:00Z');
  assert.match(ui.el('editRisk').getAttribute('href'), /risks\.html\?edit=r1&framework=A/);
  assert.match(ui.el('riskVisualisation').getAttribute('href'), /tab=visualisations/);
  assert.equal(ui.el('riskTypes').querySelector('b'), null);
  assert.equal(ui.el('riskTypes').querySelectorAll('.badge').length, 2);
  assert.equal(ui.el('riskOwner').textContent, 'dana');
  assert.match(ui.el('riskScore').innerHTML, /text-bg-danger[^>]*>16</);
  assert.match(ui.el('riskScore').textContent, /Inherent likelihood 4 × impact 4/);
  assert.match(ui.el('residualScore').innerHTML, /text-bg-warning[^>]*>6</);
  assert.equal(ui.el('riskNote').textContent, 'Review quarterly');
  const ctx = ui.el('riskMitigatorContext');
  assert.equal(ui.el('riskMitigatorContextWrap').style.display, '');
  assert.match(ctx.textContent, /Confidentiality 3 \/ Integrity 2 \/ Availability 0/);
  assert.match(ctx.textContent, /Impact prompts: personal data/);
  assert.doesNotMatch(ctx.textContent, /safety impact/);
  assert.match(ctx.textContent, /Suggested controls: 2 \(1 selected when created\)/);
  assert.equal(ctx.querySelector('b'), null);
  assert.equal(ui.el('controlsCount').textContent, '2');
  assert.equal(ui.el('linkedControls').querySelector('i'), null);
  assert.match(ui.el('linkedControls').textContent, /In scope[\s\S]*Out of scope/);
  // Related SWOT items are mounted as an extra tab.
  const swot = ui.el('swotRelatedPane');
  assert.equal(swot.querySelector('b'), null);
  assert.match(swot.textContent, /Old <b>backups<\/b> weakness · 4\/5/);
  assert.match(
    swot.querySelector('a').getAttribute('href'),
    /swot\.html\?id=s1&tab=related&framework=A/,
  );
  assert.match(swot.textContent, /Explore in SWOT analysis \(1\)/);
});

test('risks without optional data show fallbacks and readers lose edit and sampling actions', async (t) => {
  const { ui } = await riskPage(t, {
    me: { can_view_risks: true },
    risk: {
      id: 'r1',
      asset_entity: { name: 'Laptop' },
      risk_score: 2,
      residual_risk_score: 0,
      controls: [],
    },
    swot: [],
  });
  assert.equal(ui.el('riskTitle').textContent, 'Laptop');
  assert.equal(ui.el('threatSummary').textContent, 'No threat summary.');
  assert.equal(ui.el('riskNote').textContent, 'No note.');
  assert.equal(ui.el('riskCategory').textContent, '—');
  assert.match(ui.el('riskTypes').textContent, /No risk type/);
  assert.match(ui.el('riskScore').innerHTML, /text-bg-success/);
  assert.equal(ui.el('riskMitigatorContextWrap').style.display, 'none');
  assert.match(ui.el('linkedControls').textContent, /No controls are mapped/);
  assert.equal(ui.el('editRisk').style.display, 'none');
  assert.equal(ui.el('sampleRiskAudit').style.display, 'none');
  assert.equal(ui.el('riskAskQuestion').style.display, 'none');
  assert.match(ui.el('swotRelatedPane').textContent, /No SWOT items linked/);
});

test('related SWOT items explain when access is unavailable', async (t) => {
  const { ui } = await riskPage(t, { fail: { swot: true } });
  assert.equal(
    ui.el('swotRelatedPane').textContent,
    'Related SWOT items are unavailable with your current access.',
  );
});

test('risk questions support asking, replying, status changes and deletion', async (t) => {
  const { ui, state } = await riskPage(t, { globals: { confirm: () => true } });
  tab(ui, 'riskQuestionsTab');
  await settle(() => ui.el('thread-t1'));
  assert.match(ui.window.location.search, /tab=questions/);
  assert.equal(ui.el('riskQuestions').querySelector('b'), null);
  ui.el('riskAskQuestion').click();
  assert.equal(ui.el('status').textContent, 'Please enter a question');
  ui.el('riskQuestionBody').value = 'Owner?';
  ui.el('riskAskQuestion').click();
  await settle(() => ui.el('status').textContent === 'Question submitted');
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/questions/entities/risk/r1',
    body: { body: 'Owner?' },
  });
  ui.document.querySelector('[data-action="reply-risk-question"]').click();
  assert.equal(ui.el('status').textContent, 'Please enter a reply');
  ui.el('riskReplyBody-t1').value = 'Because';
  ui.document.querySelector('[data-action="reply-risk-question"]').click();
  await settle(() => ui.el('status').textContent === 'Reply sent');
  assert.deepEqual(state.posted[1], {
    path: '/api/v1/questions/t1/posts',
    body: { body: 'Because' },
  });
  const sel = ui.document.querySelector('[data-action="set-risk-question-status"]');
  sel.value = 'reviewing';
  sel.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/questions/t1',
    body: { status: 'reviewing' },
  });
  ui.document.querySelector('[data-action="delete-risk-question"]').click();
  await settle(() => ui.el('status').textContent === 'Question deleted');
  assert.deepEqual(state.removed, ['/api/v1/questions/t1']);
});

test('question mutation failures are reported and controls re-enabled', async (t) => {
  const { ui } = await riskPage(t, {
    postFail: true,
    patchFail: true,
    deleteFail: true,
    globals: { confirm: () => true },
  });
  tab(ui, 'riskQuestionsTab');
  await settle(() => ui.el('thread-t1'));
  ui.el('riskQuestionBody').value = 'Q';
  ui.el('riskAskQuestion').click();
  await settle(() => ui.el('status').textContent === 'Error: Post refused');
  assert.equal(ui.el('riskAskQuestion').disabled, false);
  ui.el('riskReplyBody-t1').value = 'R';
  ui.document.querySelector('[data-action="reply-risk-question"]').click();
  await settle(
    () =>
      ui.document.querySelector('[data-action="reply-risk-question"]').textContent === 'Send reply',
  );
  const sel = ui.document.querySelector('[data-action="set-risk-question-status"]');
  sel.value = 'answered';
  sel.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  await settle(() => ui.el('status').textContent === 'Error: Patch refused');
  const del = ui.document.querySelector('[data-action="delete-risk-question"]');
  del.click();
  await settle(() => ui.el('status').textContent === 'Error: Delete refused');
  assert.equal(del.disabled, false);
});

test('non-admin question authors reply, mark threads seen and focus linked threads', async (t) => {
  const { ui, state } = await riskPage(t, {
    me: { can_view_risks: true, can_question_events: true, user: 'erin' },
    query: '&id=r1&tab=questions&thread=t1',
    threads: [
      { id: 't1', status: 'answered', created_by_username: 'erin', posts: [] },
      { id: 't2', created_by_username: 'zed', posts: [] },
    ],
  });
  await settle(() => state.posted.some((p) => p.path.endsWith('/mark-seen')));
  assert.ok(ui.el('thread-t1').classList.contains('question-thread-focus'));
  assert.ok(ui.el('riskReplyBody-t1'));
  assert.equal(ui.document.getElementById('riskReplyBody-t2'), null);
  assert.match(ui.el('thread-t2').textContent, /Only admins or the original author can reply/);
  assert.equal(ui.document.querySelector('[data-action="delete-risk-question"]'), null);
});

test('changelog and question load failures stay inside their tabs', async (t) => {
  const { ui } = await riskPage(t, { fail: { changelog: true, questions: true } });
  tab(ui, 'riskChangelogTab');
  await settle(() => /Failed to load changelog/.test(ui.el('riskChangelog').textContent));
  tab(ui, 'riskQuestionsTab');
  await settle(() => /Failed to load questions/.test(ui.el('riskQuestions').textContent));
  assert.equal(ui.el('riskQuestions').querySelector('i'), null);
  tab(ui, 'riskDetailsTab');
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  const log = await riskPage(t, { query: '&id=r1&tab=changelog' });
  assert.match(log.ui.el('riskChangelog').textContent, /Risk scored/);
});

test('risks can be sampled into audits with a descriptive title', async (t) => {
  const { ui, state } = await riskPage(t);
  ui.el('sampleRiskAudit').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].path, '/api/v1/audits/au1/evidence');
  assert.equal(state.posted[0].body.entity_type, 'risk');
  assert.equal(state.posted[0].body.title, '<img src=x onerror=alert(1)>Payroll DB — Ransomware');
});

test('load failures and a missing id are reported', async (t) => {
  const failed = await riskPage(t, { fail: { risk: true } });
  assert.match(failed.ui.el('status').textContent, /Failed to load risk: Error: Risk offline/);
  assert.match(failed.ui.el('linkedControls').textContent, /Failed to load linked controls/);
  const missing = await page(t, 'risk', {
    get: async (u) => {
      throw new Error('Unexpected GET ' + u);
    },
  });
  assert.equal(missing.el('status').textContent, 'Missing risk id.');
  missing.el('riskAskQuestion').click();
  assert.equal(missing.el('status').textContent, 'Risk not loaded yet.');
});

test('audit managers without event access can sample a risk', async (t) => {
  const { ui } = await riskPage(t, {
    me: {
      can_view_risks: true,
      can_manage_audits: true,
      features: { enabled: { event_sampling: false } },
    },
  });
  assert.equal(ui.el('sampleRiskAudit').style.display, '');
});
