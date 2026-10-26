import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => (v instanceof FormData ? v : JSON.parse(JSON.stringify(v)));

// Minimal stand-in for Bootstrap's modal API used by the page.
class FakeModal {
  static instances = new Map();
  constructor(el) {
    this.el = el;
    FakeModal.instances.set(el, this);
  }
  static getOrCreateInstance(el) {
    return FakeModal.instances.get(el) || new FakeModal(el);
  }
  show() {
    this.el.dataset.shown = '1';
  }
  hide() {
    this.el.dataset.shown = '0';
  }
}
const bootstrap = { Modal: FakeModal };

function diaryEvent(overrides = {}) {
  return {
    id: 'e1',
    source: 'diary',
    summary: 'diary: Quarterly <b>review</b>',
    timestamp: '2026-02-01T10:00:00Z',
    connection_id: 'conn-1',
    connection_name: 'Primary <i>diary</i>',
    controls: [
      {
        id: 'c1',
        ref: 'A1',
        title: 'Access',
        framework: 'A',
        upstream_url: 'https://guide.example/a1',
        rationale: 'manual',
      },
      {
        id: 'c2',
        ref: 'A2',
        title: 'Backup',
        framework: 'A',
        upstream_url: 'javascript:alert(1)',
        rationale: 'manual',
      },
      { id: 'c3', ref: 'A3', rationale: 'auto by rule r9', rule_id: 'r9' },
    ],
    normalized_payload: {
      summary: 'Quarterly review',
      details: { notes: 'Line one\n<script>alert(1)</script>' },
      links: [
        { url: 'https://tickets.example/1', text: 'Ticket <b>1</b>' },
        { url: 'javascript:alert(1)', text: 'Bad' },
        { url: '/event.html?id=e2' },
        { url: '' },
      ],
    },
    incidents: [
      {
        id: 'i1',
        title: 'Outage <b>1</b>',
        created_by: 'dana',
        created_at: '2026-02-02',
        text: 'x'.repeat(400),
      },
      { id: 'i2', title: '' },
    ],
    artifacts: [
      {
        id: 'img1',
        content_type: 'image/png',
        kind: 'upload',
        size_bytes: 10,
        captured_at: '2026-02-01',
      },
      { id: 'pdf1', content_type: 'application/pdf', kind: 'upload' },
    ],
    ...overrides,
  };
}

async function eventPage(t, options = {}) {
  const state = {
    patched: [],
    posted: [],
    removed: [],
    event: options.event || diaryEvent(),
    threads: options.threads,
  };
  const ui = await page(t, 'event', {
    query: options.query ?? '&id=e1',
    me: options.me || {
      is_admin: true,
      user: 'root',
      can_create_incidents: true,
      can_delete_incidents: true,
    },
    globals: { bootstrap, ...options.globals },
    confirm: options.confirm,
    setup(window) {
      window.bootstrap = bootstrap;
      options.setup?.(window);
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/me') return structuredClone(options.me || { is_admin: true });
      if (p === '/api/v1/events/e1') {
        if (options.eventFail) throw new Error('Event <b>offline</b>');
        return structuredClone(state.event);
      }
      if (p === '/api/v1/events/e1/questions') {
        if (options.questionsFail) throw new Error('<b>Q offline</b>');
        return {
          threads: state.threads ?? [
            {
              id: 't1',
              status: 'answered',
              created_by_user_id: 'u-erin',
              created_by_username: 'erin',
              posts: [
                {
                  author_username: 'erin',
                  body: 'Why?\n<img src=x>',
                  attachments: [
                    { download_url: '/api/v1/artifacts/q1/download', filename: '<b>log</b>.txt' },
                    { download_url: '/api/v1/artifacts/q2/download', artifact_id: 'abcdef123456' },
                    { filename: 'no-url' },
                  ],
                },
              ],
            },
            {
              id: 't2',
              status: 'reviewing',
              created_by_user_id: 'u-zed',
              created_by_username: 'zed',
              posts: [],
            },
          ],
        };
      }
      if (p === '/api/v1/stories/related/event/e1') return { items: options.stories || [] };
      if (p === '/api/v1/stories') return { items: [] };
      if (p === '/api/v1/audits/by-event/e1') {
        if (options.samplesFail) throw new Error('hidden');
        return {
          items: options.samples ?? [
            {
              audit: {
                id: 'a1',
                title: 'Annual <b>audit</b>',
                framework_slug: 'A',
                start_date: '2026-01-01',
                status: 'in_progress',
              },
              evidence: {
                added_at: '2026-02-03',
                added_by_username: 'dana',
                notes: 'Checked\n<i>ok</i>',
              },
            },
            { audit: {}, evidence: {} },
          ],
        };
      }
      if (p === '/api/v1/audits') {
        if (options.auditsFail) throw new Error('Audits offline');
        return {
          items: options.audits ?? [
            {
              id: 'a1',
              title: 'Annual',
              status: 'open',
              start_date: '2026-01-01',
              end_date: '2026-12-31',
            },
            { id: 'a2', title: 'Spring', status: 'in_progress' },
          ],
        };
      }
      if (p === '/api/v1/controls') {
        if (options.controlsFail) throw new Error('Controls offline');
        return {
          items: [
            { id: 'c1', ref: 'A1', title: 'Access' },
            { id: 'c5', ref: 'B5', title: 'Logging <b>' },
            { id: 'k1', ref: 'A1', title: 'Access clause', type: 'clause' },
            { id: 'x', title: 'no ref' },
          ],
        };
      }
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Patch refused');
      return { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Post refused');
      return options.postResult ? options.postResult(u, plain(b)) : { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.deleteFail) throw new Error('Delete refused');
      return { ok: true };
    },
  });
  await settle(() => ui.el('normalized').textContent || ui.el('status').textContent);
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
}

test('diary evidence shows escaped notes and only safe links', async (t) => {
  const { ui } = await eventPage(t);
  const block = ui.el('diaryBlock');
  assert.equal(block.style.display, '');
  assert.equal(block.querySelector('script'), null);
  assert.match(block.innerHTML, /Line one<br>&lt;script&gt;/);
  const links = [...block.querySelectorAll('a')];
  assert.deepEqual(
    links.map((a) => a.getAttribute('href')),
    ['https://tickets.example/1', '/event.html?id=e2'],
  );
  assert.equal(links[0].textContent.trim(), 'Ticket <b>1</b>');
  assert.equal(links[1].textContent.trim(), '/event.html?id=e2');
  // Connection identity is shown as text.
  const origin = ui.el('srcBadge').querySelector('.badge');
  assert.equal(origin.textContent, 'Primary <i>diary</i>');
  assert.equal(origin.title, 'Connection: conn-1');
  // Control guidance links require absolute HTTP(S) URLs.
  const guidance = [...ui.el('controls').querySelectorAll('a.link-subtle')].map((a) =>
    a.getAttribute('href'),
  );
  assert.deepEqual(guidance, ['https://guide.example/a1']);
  assert.match(ui.el('controls').textContent, /Backup/);
  // Several rationales are listed once each, with a rule link for administrators.
  assert.equal(ui.el('rationale').querySelectorAll('li').length, 2);
  assert.match(
    ui.el('rationale').querySelector('a').getAttribute('href'),
    /rule=r9#evidence-config$/,
  );
  assert.equal(ui.el('btnEditDiary').style.display, '');
});

test('diary entries without notes show structured details and hide empty blocks', async (t) => {
  const { ui } = await eventPage(t, {
    event: diaryEvent({ normalized_payload: { details: { reviewer: '<b>dana</b>' }, links: [] } }),
  });
  assert.match(ui.el('diaryBlock').querySelector('pre').textContent, /"reviewer": "<b>dana<\/b>"/);
  const empty = await eventPage(t, { event: diaryEvent({ normalized_payload: {} }) });
  assert.equal(empty.ui.el('diaryBlock').style.display, 'none');
  const other = await eventPage(t, {
    event: diaryEvent({ source: 'aws', controls: [{ id: 'c1', ref: 'A1', rationale: 'single' }] }),
  });
  assert.equal(other.ui.el('diaryBlock').style.display, 'none');
  assert.equal(other.ui.el('btnEditDiary').style.display, 'none');
  assert.equal(other.ui.el('rationale').querySelector('.mapping-rationale').textContent, 'single');
});

test('image and PDF attachments are previewed and listed for download', async (t) => {
  const { ui } = await eventPage(t);
  const previews = ui.el('imagePreviews');
  assert.equal(previews.style.display, '');
  assert.equal(
    previews.querySelector('img.evidence-img').getAttribute('src'),
    '/api/v1/artifacts/img1/download',
  );
  assert.equal(
    previews.querySelector('img.evidence-pdf-thumb').getAttribute('src'),
    '/api/v1/artifacts/pdf1/preview',
  );
  assert.equal(ui.el('artifacts').querySelectorAll('tbody tr').length, 2);
  assert.match(ui.el('artifacts').textContent, /application\/pdf/);
  assert.equal(ui.el('btnZip').style.display, '');
});

test('administrators edit diary entries, including links, controls, details and timestamp', async (t) => {
  const { ui, state } = await eventPage(t);
  ui.el('btnEditDiary').click();
  await settle(() => ui.el('editDiaryModal').dataset.shown === '1');
  assert.equal(ui.el('editDiarySummary').value, 'Quarterly review');
  assert.equal(ui.el('editDiaryDetails').value, 'Line one\n<script>alert(1)</script>');
  assert.equal(ui.el('editDiaryTs').placeholder, '2026-02-01T10:00:00Z');
  assert.equal(ui.el('editDiaryLinks').querySelectorAll('[data-link-row]').length, 4);
  // Choices are kept by id: a clause and a control can share a reference.
  assert.deepEqual(
    [...ui.el('editControlSelected').querySelectorAll('[data-remove]')].map(
      (b) => b.dataset.remove,
    ),
    ['c1', 'c2', 'c3'],
  );
  // Remove a link row and a control; add a new control and a clause from search.
  ui.el('editDiaryLinks').querySelectorAll('[data-link-remove]')[1].click();
  ui.el('editControlSelected').querySelector('[data-remove="c2"]').click();
  ui.el('editControlSearch').value = 'logg';
  ui.el('editControlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('editControlResults').querySelector('b'), null);
  ui.el('editControlResults').querySelector('[data-add="c5"]').click();
  ui.el('editControlSearch').value = 'access';
  ui.el('editControlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(
    [...ui.el('editControlResults').querySelectorAll('[data-add]')].map((b) => [
      b.dataset.add,
      b.querySelector('.mono').textContent,
    ]),
    [
      ['c1', 'A1'],
      ['k1', 'Clause A1'],
    ],
  );
  ui.el('editControlResults').querySelector('[data-add="k1"]').click();
  assert.deepEqual(
    [...ui.el('editControlSelected').querySelectorAll('.badge')].map((b) =>
      b.textContent.replace('×', '').trim(),
    ),
    ['A1', 'A3', 'B5', 'Clause A1'],
  );
  ui.el('editControlSearch').value = '';
  ui.el('editControlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('editControlResults').innerHTML, '');
  ui.el('editDiaryAddLink').click();
  const rows = ui.el('editDiaryLinks').querySelectorAll('[data-link-row]');
  rows[rows.length - 1].querySelector('[data-link-url]').value = ' https://new.example ';
  ui.el('editDiaryDetails').value = '{"reviewed": true}';
  ui.el('editDiaryTs').value = '2026-02-05T09:30';
  ui.el('editDiarySave').click();
  await settle(() => state.patched.length === 1);
  const body = state.patched[0].body;
  assert.equal(state.patched[0].path, '/api/v1/admin/diary/e1');
  assert.equal(body.summary, 'Quarterly review');
  assert.deepEqual(body.details, { reviewed: true });
  assert.deepEqual(body.links, [
    { url: 'https://tickets.example/1', text: 'Ticket <b>1</b>' },
    { url: '/event.html?id=e2', text: '' },
    { url: 'https://new.example', text: '' },
  ]);
  assert.deepEqual(body.controls, ['c1', 'c3', 'c5', 'k1']);
  assert.equal(body.framework, 'A');
  assert.equal(body.timestamp, new Date('2026-02-05T09:30').toISOString());
  await settle(() => ui.el('status').textContent === 'Diary entry updated');
  assert.equal(ui.el('editDiaryModal').dataset.shown, '0');
});

test('diary edits require a summary and keep invalid JSON details as text', async (t) => {
  const { ui, state } = await eventPage(t, { patchFail: true });
  ui.el('btnEditDiary').click();
  await settle(() => ui.el('editDiaryModal').dataset.shown === '1');
  ui.el('editDiarySummary').value = '  ';
  ui.el('editDiarySave').click();
  await settle(() => ui.el('editDiaryStatus').textContent === 'Missing summary');
  ui.el('editDiarySummary').value = 'S';
  ui.el('editDiaryDetails').value = '{not json';
  ui.el('editDiarySave').click();
  await settle(() => ui.el('editDiaryStatus').textContent === 'Patch refused');
  assert.equal(state.patched[0].body.details, '{not json');
});

test('incident references render safely and can be deleted after confirmation', async (t) => {
  const { ui, state } = await eventPage(t);
  const incidents = ui.el('incidents');
  assert.equal(ui.el('incidentsCard').style.display, '');
  assert.equal(incidents.querySelector('b'), null);
  assert.match(incidents.textContent, /Outage <b>1<\/b>[\s\S]*by dana/);
  assert.match(incidents.textContent, /x{350}…/);
  assert.match(incidents.textContent, /Incident/);
  incidents.querySelector('[data-incident-delete="i1"]').click();
  await settle(() => ui.el('status').textContent === 'Incident deleted.');
  assert.deepEqual(state.removed, ['/api/v1/events/e1/incidents/i1']);
  assert.equal(incidents.querySelectorAll('.incident-ref').length, 1);
});

test('incident deletion respects permission, confirmation and failures', async (t) => {
  const reader = await eventPage(t, { me: { can_view_events: true } });
  assert.equal(reader.ui.document.querySelector('[data-incident-delete]'), null);
  assert.equal(reader.ui.el('btnCreateIncident').style.display, 'none');
  reader.ui.el('btnCreateIncident').click();
  assert.equal(reader.ui.el('status').textContent, 'incident.create permission required.');
  const declined = await eventPage(t, { confirm: () => false });
  declined.ui.el('incidents').querySelector('[data-incident-delete="i1"]').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(declined.state.removed, []);
  const failed = await eventPage(t, { deleteFail: true });
  failed.ui.el('incidents').querySelector('[data-incident-delete="i1"]').click();
  await settle(() =>
    /Failed to delete incident: Delete refused/.test(failed.ui.el('status').textContent),
  );
});

test('the incident form is prefilled from the event and counts characters', async (t) => {
  const { ui } = await eventPage(t, { event: diaryEvent({ summary: 'Incident on prod' }) });
  ui.el('btnCreateIncident').click();
  assert.equal(ui.el('incidentModal').dataset.shown, '1');
  // An existing "Incident" prefix is not repeated.
  assert.equal(ui.el('incidentTitle').value, 'Incident on prod');
  ui.el('incidentText').value = 'abc';
  ui.el('incidentText').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('incidentTextCount').textContent, '3');
  const other = await eventPage(t);
  other.ui.el('btnCreateIncident').click();
  assert.equal(other.ui.el('incidentTitle').value, 'Incident: diary: Quarterly <b>review</b>');
});

test('audits that sampled the event are listed and the event can be sampled into another', async (t) => {
  const { ui, state } = await eventPage(t);
  const samples = ui.el('auditSamples');
  assert.equal(ui.el('auditSamplesCard').style.display, '');
  assert.equal(samples.querySelector('b'), null);
  assert.equal(samples.querySelector('i'), null);
  assert.match(samples.textContent, /A · 2026-01-01 · sampled 2026-02-03 · by dana/);
  assert.match(samples.innerHTML, /Checked<br>&lt;i&gt;ok/);
  assert.match(samples.textContent, /Sampled into this audit/);
  assert.match(samples.textContent, /in progress/);
  ui.el('btnSampleAudit').click();
  await settle(() => ui.el('auditSampleSelect').options.length === 2);
  assert.match(
    ui.el('auditSampleSelect').options[0].textContent,
    /Annual · 2026-01-01 → 2026-12-31 · Open/,
  );
  assert.match(ui.el('auditSampleSelect').options[1].textContent, /Spring · In progress/);
  ui.el('auditSampleSelect').value = 'a2';
  ui.el('auditSampleNotes').value = 'Reviewed';
  ui.el('auditSampleSubmit').click();
  await settle(() => ui.el('status').textContent === 'Evidence sampled into audit');
  assert.deepEqual(state.posted.at(-1), {
    path: '/api/v1/audits/a2/evidence',
    body: {
      event_id: 'e1',
      evidence_url: '/event.html?id=e1&framework=A',
      notes: 'Reviewed',
      title: 'diary: Quarterly <b>review</b>',
    },
  });
  assert.equal(ui.el('auditSampleModal').dataset.shown, '0');
});

test('sampling explains missing or unavailable audits', async (t) => {
  const none = await eventPage(t, { audits: [] });
  none.ui.el('btnSampleAudit').click();
  await settle(() =>
    /No open or in-progress audits are available/.test(none.ui.el('auditSampleStatus').textContent),
  );
  assert.equal(none.ui.el('auditSampleSubmit').disabled, true);
  none.ui.el('auditSampleSubmit').disabled = false;
  none.ui.el('auditSampleSubmit').click();
  assert.match(none.ui.el('auditSampleStatus').textContent, /Start an Audit first/);
  const failed = await eventPage(t, { auditsFail: true, samplesFail: true });
  assert.equal(failed.ui.el('auditSamplesCard').style.display, 'none');
  failed.ui.el('btnSampleAudit').click();
  await settle(() =>
    /Failed to load audits: Audits offline/.test(failed.ui.el('auditSampleStatus').textContent),
  );
  const rejected = await eventPage(t, { postFail: true });
  rejected.ui.el('btnSampleAudit').click();
  await settle(() => rejected.ui.el('auditSampleSelect').value === 'a1');
  rejected.ui.el('auditSampleSubmit').click();
  await settle(() => /Failed: Post refused/.test(rejected.ui.el('auditSampleStatus').textContent));
  const noSamples = await eventPage(t, { samples: [] });
  assert.equal(noSamples.ui.el('auditSamplesCard').style.display, 'none');
});

// The question panel is shared by every record that takes questions; the
// event page adds attachments, the tab count and opening a linked thread.
const submitAsk = (ui) =>
  ui
    .el('questions')
    .querySelector('[data-question-ask]')
    .dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

test('event questions render attachments safely and support ask, reply, status and delete', async (t) => {
  const prompts = [];
  const { ui, state } = await eventPage(t, {
    confirm: (asked) => (prompts.push(asked), true),
  });
  const q = ui.el('questions');
  assert.equal(q.querySelector('img'), null);
  assert.equal(q.querySelector('b'), null);
  // Line breaks survive; markup is shown as text.
  assert.match(ui.el('thread-t1').innerHTML, /Why\?<br>&lt;img src=x&gt;/);
  assert.match(ui.el('thread-t2').textContent, /No posts yet\./);
  assert.equal(ui.el('eventQuestionsCount').textContent, '2');
  // Administrators leave unread replies for the author.
  assert.ok(!state.posted.some((p) => p.path.endsWith('/mark-seen')));
  const attachments = [...q.querySelectorAll('.question-attachments a')];
  assert.deepEqual(
    attachments.map((a) => a.getAttribute('href')),
    ['/api/v1/artifacts/q1/download', '/api/v1/artifacts/q2/download'],
  );
  assert.match(attachments[0].textContent, /<b>log<\/b>\.txt/);
  assert.match(attachments[1].textContent, /attachment-abcdef12/);
  // Ask a new question with an attachment.
  const ask = q.querySelector('[data-question-ask]');
  assert.equal(ask.hidden, false);
  assert.equal(ui.el('questionBody').placeholder, 'e.g. What was this about?');
  submitAsk(ui);
  assert.equal(ui.el('status').textContent, 'Please enter a question');
  ui.el('questionBody').value = ' Is this complete? ';
  Object.defineProperty(ask.elements.attachments, 'files', {
    value: [new File(['a'], 'a.txt')],
    configurable: true,
  });
  submitAsk(ui);
  await settle(() => ui.el('status').textContent === 'Question submitted');
  const asked = state.posted.find((p) => p.path === '/api/v1/events/e1/questions').body;
  assert.equal(asked.get('body'), 'Is this complete?');
  assert.equal(asked.getAll('attachments')[0].name, 'a.txt');
  // Reply, change status, delete.
  const replyButton = () => ui.el('thread-t1').querySelector('[data-question-action="reply"]');
  replyButton().click();
  assert.equal(ui.el('status').textContent, 'Please enter a reply');
  ui.el('questionReply-t1').value = 'Yes';
  replyButton().click();
  await settle(() => ui.el('status').textContent === 'Reply sent');
  assert.equal(
    state.posted.find((p) => p.path === '/api/v1/questions/t1/posts').body.get('body'),
    'Yes',
  );
  const select = ui.el('thread-t2').querySelector('[data-question-action="status"]');
  select.value = 'answered';
  select.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/questions/t2',
    body: { status: 'answered' },
  });
  ui.el('thread-t2').querySelector('[data-question-action="delete"]').click();
  await settle(() => ui.el('status').textContent === 'Question deleted');
  assert.deepEqual(state.removed, ['/api/v1/questions/t2']);
  assert.deepEqual(
    prompts.map((p) => p.title),
    ['Delete question thread'],
  );
});

test('question authors reply, mark threads seen and focus a linked thread', async (t) => {
  const shown = [];
  const { ui, state } = await eventPage(t, {
    me: { can_view_events: true, can_create_questions: true, user: 'erin', user_id: 'u-erin' },
    query: '&id=e1&thread=t1',
    setup(window) {
      window.bootstrap = {
        ...bootstrap,
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  await settle(() => state.posted.some((p) => p.path === '/api/v1/events/e1/questions/mark-seen'));
  assert.ok(ui.el('thread-t1').classList.contains('question-thread-focus'));
  // A linked thread opens the Questions tab.
  assert.deepEqual(shown, ['eventQuestionsTab']);
  assert.ok(ui.document.getElementById('questionReply-t1'));
  assert.equal(ui.document.getElementById('questionReply-t2'), null);
  assert.match(ui.el('thread-t2').textContent, /Only admins or the original author can reply\./);
  assert.equal(ui.document.querySelector('[data-question-action="status"]'), null);
  assert.equal(ui.document.querySelector('[data-question-action="delete"]'), null);
  assert.equal(ui.el('questions').querySelector('[data-question-ask]').hidden, false);
});

test('a tab named in the address opens; unknown or inherited names keep the snapshot', async (t) => {
  for (const [name, expected] of [
    ['questions', 'eventQuestionsTab'],
    ['constructor', 'eventSnapshotTab'],
    ['__proto__', 'eventSnapshotTab'],
  ]) {
    const shown = [];
    const { ui } = await eventPage(t, {
      query: `&id=e1&tab=${name}`,
      setup(window) {
        window.bootstrap = {
          ...bootstrap,
          Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
        };
      },
    });
    await settle(() => shown.length > 0);
    assert.deepEqual(shown, [expected], name);
    assert.ok(ui.el('eventTabs'));
  }
});

test('question failures are contained in the questions panel', async (t) => {
  const failed = await eventPage(t, { questionsFail: true });
  assert.match(failed.ui.el('questions').textContent, /Failed to load questions/);
  assert.equal(failed.ui.el('questions').querySelector('b'), null);
  const empty = await eventPage(t, {
    threads: [],
    postFail: true,
    patchFail: true,
    deleteFail: true,
  });
  assert.match(empty.ui.el('questions').textContent, /No questions yet\./);
  empty.ui.el('questionBody').value = 'Q';
  submitAsk(empty.ui);
  await settle(() => empty.ui.el('status').textContent === 'Post refused');
  // The form keeps the question for another try.
  assert.equal(empty.ui.el('questionBody').value, 'Q');
  const askButton = empty.ui.el('questions').querySelector('[data-question-ask] [type="submit"]');
  assert.equal(askButton.disabled, false);
  assert.equal(askButton.textContent, 'Ask question');
  const actions = await eventPage(t, { postFail: true, patchFail: true, deleteFail: true });
  actions.ui.el('questionReply-t1').value = 'R';
  const reply = actions.ui.el('thread-t1').querySelector('[data-question-action="reply"]');
  reply.click();
  await settle(() => actions.ui.el('status').textContent === 'Post refused');
  assert.equal(reply.disabled, false);
  assert.equal(reply.textContent, 'Send reply');
  const sel = actions.ui.el('questions').querySelector('[data-question-action="status"]');
  sel.value = 'reviewing';
  sel.dispatchEvent(new actions.ui.window.Event('change', { bubbles: true }));
  await settle(() => actions.ui.el('status').textContent === 'Patch refused');
  const remove = actions.ui.el('questions').querySelector('[data-question-action="delete"]');
  remove.click();
  await settle(() => actions.ui.el('status').textContent === 'Delete refused');
  assert.equal(remove.disabled, false);
});

test('an incident is created from the event with sanitised plain text', async (t) => {
  const { ui, state } = await eventPage(t, {
    postResult: (u, body) => ({
      incident: { id: 'i9', title: body.title, created_by: 'root', created_at: '2026-02-05' },
    }),
  });
  ui.el('btnCreateIncident').click();
  assert.equal(ui.el('incidentModal').dataset.shown, '1');
  assert.equal(ui.el('incidentTitle').value, 'Incident: diary: Quarterly <b>review</b>');
  ui.el('incidentTitle').value = '  Outage\u0000 <script>x</script>  ';
  ui.el('incidentText').value = 'Line one\r\nLine two\u0007' + 'y'.repeat(6000);
  ui.el('incidentText').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('incidentTextCount').textContent, String(ui.el('incidentText').value.length));
  ui.el('incidentSubmit').click();
  await settle(() => ui.el('status').textContent === 'Incident created from event.');
  const [sent] = state.posted.filter((p) => p.path === '/api/v1/events/e1/incident');
  // Control characters are dropped, newlines normalised and the text capped.
  assert.equal(sent.body.title, 'Outage <script>x</script>');
  assert.ok(sent.body.text.startsWith('Line one\nLine two' + 'y'));
  assert.equal(sent.body.text.length, 5000);
  assert.equal(ui.el('incidentModal').dataset.shown, '0');
  assert.equal(ui.el('incidents').querySelector('script'), null);
  assert.match(ui.el('incidents').textContent, /Outage <script>x<\/script>/);
  assert.equal(ui.el('incidentSubmit').disabled, false);
});

test('incident creation requires a title and reports failures', async (t) => {
  const { ui, state } = await eventPage(t, { postFail: true });
  ui.el('btnCreateIncident').click();
  ui.el('incidentTitle').value = ' \u0001 ';
  ui.el('incidentSubmit').click();
  await settle(() => ui.el('incidentStatus').textContent === 'Title is required.');
  assert.equal(state.posted.length, 0);
  ui.el('incidentTitle').value = 'Outage';
  ui.el('incidentSubmit').click();
  await settle(
    () => ui.el('incidentStatus').textContent === 'Failed to create incident: Post refused',
  );
  assert.equal(ui.el('incidentSubmit').disabled, false);
});

test('actions on an event that failed to load are refused with a warning', async (t) => {
  const { ui, state } = await eventPage(t, { eventFail: true });
  assert.equal(ui.el('status').textContent, 'Failed to load event: Event <b>offline</b>');
  assert.equal(ui.el('status').querySelector('b'), null);
  for (const [button, message] of [
    ['btnSampleAudit', 'Event not loaded yet'],
    ['btnCreateIncident', 'Event not loaded yet.'],
  ]) {
    ui.el('status').textContent = '';
    ui.el(button).click();
    assert.equal(ui.el('status').textContent, message, button);
    assert.equal(ui.el('status').dataset.alert, 'warning');
  }
  ui.el('incidentSubmit').click();
  assert.equal(ui.el('incidentStatus').textContent, 'Event not loaded yet.');
  assert.deepEqual([state.posted, state.patched, state.removed], [[], [], []]);
});

test('sampling into an audit needs the audits.manage permission', async (t) => {
  const { ui, state } = await eventPage(t, {
    me: { can_view_events: true, can_create_incidents: true },
  });
  assert.equal(ui.el('btnSampleAudit').style.display, 'none');
  ui.el('btnSampleAudit').click();
  assert.equal(ui.el('status').textContent, 'audits.manage permission required.');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.ok(!state.posted.some((p) => p.path.includes('/audits')));
});

test('an event without a source or a framework in the address still links its register', async (t) => {
  const { ui } = await eventPage(t, {
    event: diaryEvent({
      source: '',
      source_url: 'https://upstream.example/items/1',
      controls: [{ id: 'c1', ref: 'A1', title: 'Access', framework: 'B', rationale: 'manual' }],
    }),
    setup(window) {
      window.history.replaceState(null, '', '/event.html?id=e1');
    },
  });
  assert.equal(ui.el('srcBadge').textContent, '-');
  assert.equal(ui.el('bySource').getAttribute('href'), '/events.html?framework=A');
  // The framework comes from the mapped control when the address has none.
  ui.el('btnSampleAudit').click();
  assert.equal(ui.el('auditSampleCreate').getAttribute('href'), '/audit-wizard.html?framework=B');
  assert.equal(ui.el('openSource').getAttribute('href'), 'https://upstream.example/items/1');
  assert.equal(ui.el('openSource').style.display, '');

  const unsafe = await eventPage(t, {
    event: diaryEvent({ source_url: 'javascript:alert(1)' }),
  });
  assert.equal(unsafe.ui.el('openSource').style.display, 'none');
  assert.notEqual(unsafe.ui.el('openSource').getAttribute('href'), 'javascript:alert(1)');
});

test('the diary editor falls back to the stored summary and JSON details', async (t) => {
  const { ui } = await eventPage(t, {
    event: diaryEvent({
      summary: 'DIARY: Weekly <b>check</b>',
      normalized_payload: { details: { reviewer: 'dana', score: 3 } },
    }),
    controlsFail: true,
  });
  ui.el('btnEditDiary').click();
  await settle(() => ui.el('editDiaryModal').dataset.shown === '1');
  assert.equal(ui.el('editDiarySummary').value, 'Weekly <b>check</b>');
  assert.deepEqual(JSON.parse(ui.el('editDiaryDetails').value), { reviewer: 'dana', score: 3 });
  // Without the control catalogue, the search offers nothing and stays usable.
  ui.el('editControlSearch').value = 'acc';
  ui.el('editControlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('editControlResults').querySelectorAll('[data-add]').length, 0);

  const plain = await eventPage(t, {
    event: diaryEvent({ summary: 'diary:', normalized_payload: { details: {} } }),
    globals: {
      bootstrap: {
        Modal: {
          getOrCreateInstance() {
            throw new Error('No modal API');
          },
        },
      },
    },
  });
  plain.ui.el('btnEditDiary').click();
  await settle(() => plain.ui.el('editDiaryModal').style.display === '');
  assert.equal(plain.ui.el('editDiarySummary').value, '');
  assert.equal(plain.ui.el('editDiaryDetails').value, '');
});

test('a thread that is neither answered nor reviewing is shown as unanswered', async (t) => {
  const { ui } = await eventPage(t, {
    threads: [{ id: 't9', status: 'open', created_by_username: 'erin', posts: [] }],
  });
  const badge = ui.el('thread-t9').querySelector('.badge.text-bg-secondary');
  assert.equal(badge.textContent, 'unanswered');
});

test('the print view loads the viewer directly, masks the page and prints once', async (t) => {
  let prints = 0;
  const { ui, state } = await eventPage(t, {
    query: '&id=e1&print=1',
    me: { is_admin: true, user: 'root', sample_pdf_footer: 'Confidential <b>' },
    event: diaryEvent({ artifacts: [] }),
    setup(window) {
      window.print = () => prints++;
    },
  });
  assert.ok(ui.document.body.classList.contains('print-view'));
  assert.ok(ui.calls.some((c) => c.method === 'GET' && c.url === '/api/v1/me'));
  // The print window never marks questions seen on the viewer's behalf.
  assert.ok(!state.posted.some((p) => p.path.endsWith('/mark-seen')));
  await settle(() => prints === 1);
  assert.match(ui.document.title, /^Keen Event - e1/);
  assert.equal(ui.el('pdfFooter').textContent, 'Confidential <b>');
  assert.equal(ui.el('pdfFooter').querySelector('b'), null);
  assert.equal(ui.el('pdfHeader').dataset.enabled, '1');
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(prints, 1);
});

test('the stories that include the event are listed for readers and offered to authors', async (t) => {
  const listed = await eventPage(t, {
    me: { can_view_events: true, can_view_stories: true },
    stories: [
      {
        id: 's1',
        title: 'Backup <b>failure</b>',
        status: 'open',
        item_count: 3,
        updated_at: '2026-05-01',
        can_edit: false,
        version: 1,
      },
    ],
  });
  await settle(() => /Backup <b>failure<\/b>/.test(listed.ui.el('eventStories').textContent));
  assert.equal(listed.ui.el('storiesCard').style.display, '');
  assert.equal(listed.ui.el('eventStories').querySelector('b'), null);
  assert.equal(listed.ui.el('eventStories').querySelector('h2'), null);
  assert.equal(listed.ui.document.querySelector('[data-story-add]'), null);

  // A reader sees no card for an event that is in no story.
  const empty = await eventPage(t, { me: { can_view_events: true, can_view_stories: true } });
  await settle(() => empty.ui.calls.some((c) => c.url.includes('/stories/related/event/e1')));
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(empty.ui.el('storiesCard').style.display, 'none');

  // Authors can always add the event, so the card stays for them.
  const author = await eventPage(t);
  await settle(() => author.ui.el('storiesCard').style.display === '');
  assert.match(author.ui.el('eventStories').textContent, /This event is not in a story yet/);
  assert.ok(author.ui.document.querySelector('[data-story-add]'));

  // Without story access nothing is requested.
  const none = await eventPage(t, { me: { can_view_events: true } });
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(none.ui.el('storiesCard').style.display, 'none');
  assert.ok(!none.ui.calls.some((c) => c.url.includes('/stories/')));
});
