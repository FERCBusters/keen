import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';
const event = {
  id: 'e1',
  source: 'keen-agent',
  summary: '<img src=x> alert',
  timestamp: '2026-01-01',
  system: 'server',
  actor: 'root',
  action: 'ossec.alert',
  severity: 0,
  controls: [],
  artifacts: [],
  normalized_payload: { fields: { 'ossec.rule.id': '31101' } },
  raw_pointer: { uri: 's3://redacted' },
};
async function fixture(t, options = {}) {
  const ui = await page(t, 'event', {
    query: '&id=e1',
    ...options,
    get: async (u) => {
      if (u.pathname === '/api/v1/events/e1') {
        if (options.fail) throw new Error('Unavailable');
        return { ...event, ...options.event };
      }
      if (u.pathname === '/api/v1/events/e1/questions') return { threads: [] };
      if (u.pathname === '/api/v1/events/e1/control-suggestions') {
        if (options.suggestionsFail) throw new Error('down');
        return {
          framework: u.searchParams.get('framework'),
          items: options.suggestions ?? [
            {
              id: 'c1',
              ref: 'A.1',
              title: 'Logging <b>&</b> monitoring',
              reason: 'Catalogue words: alert.',
            },
            { id: 'c2', ref: 'A.2', title: 'Second', reason: 'r2' },
            { id: 'c3', ref: 'A.3', title: 'Third', reason: 'r3' },
            { id: 'c4', ref: 'A.4', title: 'Fourth', reason: 'r4' },
          ],
        };
      }
      if (u.pathname === '/api/v1/stories/related/event/e1') return { items: [] };
      if (u.pathname === '/api/v1/stories') return { items: [] };
      if (u.pathname === '/api/v1/audits/by-event/e1') return { items: [] };
      if (u.pathname === '/api/v1/audits') return { items: options.audits || [] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      if (u.pathname.endsWith('/incident'))
        return { id: 'incident1', url: 'https://example.org/incident' };
      if (u.pathname.endsWith('/questions/mark-seen')) return { ok: true };
      throw new Error('Unexpected POST ' + u);
    },
  });
  await settle(() =>
    options.query === ''
      ? ui.el('status').textContent.includes('Missing')
      : options.fail
        ? ui.el('status').textContent.includes('Unavailable')
        : ui.el('normalized').textContent.includes('ossec.rule.id'),
  );
  return ui;
}
test('event detail renders structured payload and an event-scoped mapping shortcut', async (t) => {
  const ui = await fixture(t);
  assert.equal(ui.el('summary').querySelector('img'), null);
  assert.match(ui.el('summary').textContent, /<img src=x>/);
  assert.match(ui.el('controls').textContent, /unmapped/);
  assert.equal(ui.el('btnCreateMapping').hidden, false);
  assert.match(ui.el('btnCreateMapping').href, /framework=A&from_event=e1#evidence-config/);
  assert.match(ui.el('normalized').textContent, /31101/);
  assert.ok(ui.el('normalized').querySelector('.json-key'));
});
test('event severity zero remains visible', async (t) => {
  const ui = await fixture(t);
  assert.equal(ui.el('severity').textContent, '0');
});
test('non-administrators do not get a mapping-editor shortcut', async (t) => {
  const ui = await fixture(t, { me: { is_admin: false } });
  assert.equal(ui.el('btnCreateMapping').hidden, true);
});
test('unsafe source URLs are not enabled', async (t) => {
  const ui = await fixture(t, { event: { source_url: 'javascript:alert(1)' } });
  assert.equal(ui.el('openSource').style.display, 'none');
});
test('mapped controls and downloadable evidence are rendered', async (t) => {
  const ui = await fixture(t, {
    event: {
      controls: [
        { id: 'c1', ref: 'A1', title: 'Access control', framework: 'A', method: 'manual' },
      ],
      artifacts: [{ id: 'a1', kind: 'raw', content_type: 'application/json', size_bytes: 12 }],
    },
  });
  assert.match(ui.el('controls').textContent, /A1/);
  assert.ok(ui.document.querySelector('a[href="/api/v1/artifacts/a1/download"]'));
});
test('missing event IDs make no event request', async (t) => {
  const ui = await fixture(t, { query: '' });
  assert.match(ui.el('status').textContent, /Missing event id/);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/events/')));
});
test('event fetch failure is visible', async (t) => {
  const ui = await fixture(t, { fail: true });
  assert.match(ui.el('status').textContent, /Unavailable/);
});

test('event sampling offers only audits the auditor may edit', async (t) => {
  const ui = await fixture(t, {
    me: { can_manage_audits: true, can_view_events: true, user_id: 'one' },
    audits: [
      { id: 'own', title: 'Own audit', status: 'open', created_by_user_id: 'one' },
      { id: 'other', title: 'Other audit', status: 'open', created_by_user_id: 'two' },
    ],
  });
  ui.el('btnSampleAudit').click();
  await settle(() => ui.el('auditSampleSelect').textContent.includes('Own audit'));
  assert.equal(ui.el('auditSampleSelect').querySelectorAll('option').length, 1);
  assert.equal(ui.el('auditSampleSelect').value, 'own');
});

test('audit management alone does not expose event sampling', async (t) => {
  const ui = await fixture(t, { me: { can_manage_audits: true, user_id: 'one' } });
  assert.equal(ui.el('btnSampleAudit').style.display, 'none');
});

test('rule rationale links use escaped text and encoded IDs for administrators only', async (t) => {
  const rule = 'rule"><img src=x>';
  const data = {
    controls: [{ id: 'c1', ref: 'A.1', rationale: 'auto by rule ' + rule + ' (A)', rule_id: rule }],
  };
  const ui = await fixture(t, { event: data });
  const link = ui.el('rationale').querySelector('a');
  assert.ok(link);
  assert.equal(new URL(link.href).searchParams.get('rule'), rule);
  assert.equal(new URL(link.href).hash, '#evidence-config');
  assert.equal(ui.el('rationale').querySelector('img'), null);
  const reader = await fixture(t, { event: data, me: { is_admin: false } });
  assert.equal(reader.el('rationale').querySelector('a'), null);
  assert.match(reader.el('rationale').textContent, /auto by rule/);
});

test('an unmapped event shows suggested controls and a rule proposal with the ticked refs', async (t) => {
  const ui = await fixture(t);
  await settle(() => ui.el('suggestedControlsList').children.length === 4);
  assert.equal(ui.el('suggestedControls').hidden, false);
  assert.ok(ui.calls.some((c) => c.url === '/api/v1/events/e1/control-suggestions?framework=A'));
  const rows = [...ui.el('suggestedControlsList').children];
  assert.equal(rows[0].querySelector('b'), null);
  assert.match(rows[0].textContent, /A\.1.*Logging <b>&<\/b> monitoring.*Catalogue words: alert\./);
  assert.match(rows[0].querySelector('a').getAttribute('href'), /control\.html\?id=c1&framework=A/);
  const boxes = rows.map((r) => r.querySelector('input[type="checkbox"]'));
  assert.deepEqual(
    boxes.map((b) => b.checked),
    [true, true, true, false],
  );
  const propose = ui.el('suggestedControlsPropose');
  assert.equal(ui.el('suggestedControlsActions').hidden, false);
  assert.match(propose.href, /from_event=e1&targets=A\.1%2CA\.2%2CA\.3#evidence-config/);
  assert.equal(propose.textContent, 'Propose a rule with 3 ticked controls');
  boxes[0].checked = false;
  boxes[1].checked = false;
  boxes[2].checked = false;
  boxes[0].dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.equal(propose.textContent, 'Propose a rule from this event');
  assert.ok(!propose.href.includes('targets='));
});
test('a mapped event offers suggestions on request; readers see them without a proposal', async (t) => {
  const mapped = { controls: [{ id: 'c9', ref: 'Z.9', title: 'Mapped', framework: 'A' }] };
  const ui = await fixture(t, { event: mapped });
  assert.equal(ui.el('suggestedControlsLoad').hidden, false);
  assert.ok(!ui.calls.some((c) => c.url.includes('/control-suggestions')));
  ui.el('suggestedControlsLoad').click();
  await settle(() => ui.el('suggestedControlsList').children.length === 4);
  assert.equal(ui.el('suggestedControlsLoad').hidden, true);
  const reader = await fixture(t, { me: { is_admin: false, can_question_events: true } });
  await settle(() => reader.el('suggestedControlsList').children.length === 4);
  assert.equal(reader.el('suggestedControlsList').querySelector('input'), null);
  assert.equal(reader.el('suggestedControlsActions').hidden, true);
});
test('empty and failed suggestion loads are explained without a proposal', async (t) => {
  const none = await fixture(t, { suggestions: [] });
  await settle(() => none.el('suggestedControlsMeta').textContent.includes('No clear'));
  assert.equal(none.el('suggestedControlsActions').hidden, true);
  const failed = await fixture(t, { suggestionsFail: true });
  await settle(() => failed.el('suggestedControlsMeta').textContent.includes('unavailable'));
  assert.equal(failed.el('suggestedControlsList').children.length, 0);
});
