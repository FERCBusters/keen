import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

// ---------------------------------------------------------------- Documents
const folders = [
  { id: 'f1', name: 'Policies <b>' },
  { id: 'f2', name: 'Procedures' },
];
function doc(id, overrides = {}) {
  return {
    id,
    title: `<script>Doc ${id}</script>`,
    document_type: 'policy',
    folder_id: 'f1',
    tags: ['iso', 'core'],
    description: 'Main policy',
    content_html: '<p>Body</p>',
    content_version: 3,
    ...overrides,
  };
}

async function documentsPage(t, options = {}) {
  const state = {
    docs: options.docs || [
      doc('d1'),
      doc('d2', { folder_id: 'f2', tags: [], title: 'Backup procedure' }),
    ],
    posted: [],
    patched: [],
  };
  const ui = await page(t, 'documents', {
    me: options.me || { is_admin: true },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/isms/document-folders') {
        if (options.fail) throw new Error('Folders offline');
        return folders;
      }
      if (p === '/api/v1/isms/documents') return { items: structuredClone(state.docs) };
      if (p.endsWith('/revisions'))
        return [
          { version: 2, created_at: '2026-01-02T03:04:05Z' },
          { version: 1, created_at: '2026-01-01T00:00:00Z' },
        ];
      if (p.endsWith('/comments'))
        return [
          { author: { username: 'dana' }, created_at: '2026-01-03', body: '<img src=x>Looks good' },
          { author: {}, created_at: '2026-01-04', body: 'Plain' },
        ];
      if (p.endsWith('/revisions/2')) {
        if (options.revisionFail) throw new Error('Revision gone');
        return {
          version: 2,
          sha256: 'abc123',
          content_html: '<p>Old</p><script>alert(1)</script>',
        };
      }
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Rejected');
      if (u.pathname === '/api/v1/isms/documents') {
        state.docs.push({ ...b, id: 'd9', content_version: 1 });
        return { id: 'd9' };
      }
      return { ok: true };
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      return { id: 'd1' };
    },
  });
  await settle(() => ui.el('documentList').children.length || ui.el('status').textContent);
  return { ui, state };
}

test('documents list folders and documents as text and filter by folder and search', async (t) => {
  const { ui } = await documentsPage(t);
  assert.deepEqual(
    [...ui.el('folderList').children].map((b) => b.textContent),
    ['All folders', 'Policies <b>', 'Procedures'],
  );
  assert.equal(ui.el('folderList').querySelector('b'), null);
  assert.equal(ui.el('documentList').querySelector('script'), null);
  assert.equal(ui.el('documentList').children.length, 2);
  assert.match(ui.el('documentList').textContent, /policy · iso, core/);
  ui.el('folderList').querySelector('[data-folder="f2"]').click();
  assert.equal(ui.el('documentList').children.length, 1);
  assert.ok(ui.el('folderList').querySelector('[data-folder="f2"]').classList.contains('active'));
  ui.el('search').value = 'nothing';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('documentList').textContent, /No documents found/);
  ui.el('search').value = 'CORE';
  ui.el('folderList').querySelector('[data-folder=""]').click();
  assert.equal(ui.el('documentList').children.length, 1);
});

test('opening a document shows its fields, revisions and comments safely', async (t) => {
  const { ui } = await documentsPage(t);
  ui.el('documentList').querySelector('[data-document="d1"]').click();
  await settle(() => ui.el('commentList').children.length === 2);
  assert.equal(ui.el('editorTitle').textContent, '<script>Doc d1</script>');
  assert.equal(ui.el('tags').value, 'iso, core');
  assert.equal(ui.el('folder').value, 'f1');
  assert.equal(ui.el('content').value, '<p>Body</p>');
  assert.equal(ui.el('historyCard').style.display, '');
  assert.deepEqual(
    [...ui.el('revisionList').querySelectorAll('button')].map((b) => b.textContent),
    ['Version 2 · 2026-01-02', 'Version 1 · 2026-01-01'],
  );
  assert.equal(ui.el('commentList').querySelector('img'), null);
  assert.match(ui.el('commentList').textContent, /dana[\s\S]*<img src=x>Looks good[\s\S]*User/);
  ui.el('revisionList').querySelector('[data-revision="2"]').click();
  await settle(() => ui.document.getElementById('revisionContent'));
  assert.match(ui.el('revisionPreview').textContent, /Version 2 · SHA-256 abc123/);
  // Stored revision HTML passes through the shared sanitiser.
  assert.equal(ui.el('revisionPreview').querySelector('script'), null);
  assert.equal(ui.document.getElementById('revisionContent').querySelector('p').textContent, 'Old');
});

test('saving an existing document sends the content version for conflict detection', async (t) => {
  const { ui, state } = await documentsPage(t);
  ui.el('documentList').querySelector('[data-document="d1"]').click();
  await settle(() => ui.el('commentList').children.length === 2);
  ui.el('tags').value = ' a, , b ';
  ui.el('folder').value = '';
  ui.el('content').value = '<p>New</p>';
  ui.el('documentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Document saved.');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/isms/documents/d1',
    body: {
      title: '<script>Doc d1</script>',
      document_type: 'policy',
      folder_id: null,
      tags: ['a', 'b'],
      description: 'Main policy',
      content_html: '<p>New</p>',
      expected_content_version: 3,
    },
  });
});

test('new documents, folders and comments are created by managers', async (t) => {
  const { ui, state } = await documentsPage(t);
  ui.el('folderList').querySelector('[data-folder="f2"]').click();
  ui.el('newDocument').click();
  assert.equal(ui.el('editorTitle').textContent, 'New document');
  assert.equal(ui.el('historyCard').style.display, 'none');
  // The active folder is preselected and survives a redraw while drafting.
  assert.equal(ui.el('folder').value, 'f2');
  ui.el('title').value = 'Incident response';
  ui.el('folderParent').value = 'f1';
  ui.el('search').value = 'incident';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('folder').value, 'f2');
  assert.equal(ui.el('folderParent').value, 'f1');
  ui.el('search').value = '';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  ui.el('documentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Document saved.');
  assert.equal(state.posted[0].path, '/api/v1/isms/documents');
  assert.equal(state.posted[0].body.folder_id, 'f2');
  assert.equal(ui.el('editorTitle').textContent, 'Incident response');

  ui.el('comment').value = 'Reviewed';
  ui.el('commentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Comment added.');
  assert.deepEqual(state.posted[1], {
    path: '/api/v1/isms/documents/d9/comments',
    body: { body: 'Reviewed' },
  });

  ui.el('newFolder').value = 'Records';
  ui.el('folderParent').value = 'f1';
  ui.el('folderForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Folder added.');
  assert.deepEqual(state.posted[2], {
    path: '/api/v1/isms/document-folders',
    body: { name: 'Records', parent_id: 'f1' },
  });
});

test('document readers cannot edit and failures are reported', async (t) => {
  const { ui, state } = await documentsPage(t, { me: { can_view_isms: true } });
  assert.equal(ui.el('folderForm').hidden, true);
  assert.equal(ui.el('commentForm').hidden, true);
  assert.equal(ui.el('newDocument').hidden, true);
  assert.ok([...ui.el('documentForm').querySelectorAll('input, button')].every((x) => x.disabled));
  ui.el('documentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('folderForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.posted.length + state.patched.length, 0);
  const failed = await documentsPage(t, { fail: true });
  assert.match(
    failed.ui.el('status').textContent,
    /Could not load documents: Error: Folders offline/,
  );
  const rejected = await documentsPage(t, { postFail: true, revisionFail: true });
  rejected.ui
    .el('folderForm')
    .dispatchEvent(new rejected.ui.window.Event('submit', { cancelable: true }));
  await settle(() => rejected.ui.el('status').textContent === 'Error: Rejected');
  rejected.ui.el('documentList').querySelector('[data-document="d1"]').click();
  await settle(() => rejected.ui.el('revisionList').querySelector('button'));
  rejected.ui.el('revisionList').querySelector('button').click();
  await settle(() => rejected.ui.el('status').textContent === 'Error: Revision gone');
});

// ---------------------------------------------------------- BookStack sections
async function bookstack(t, options = {}) {
  const state = { posted: [], patched: [] };
  const ui = await page(t, 'bookstack-sections', {
    htmlName: 'isms',
    frameworks: [{ slug: 'A', name: 'Framework A' }, { slug: 'B' }],
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/isms/documents')
        return { items: [{ id: 'd1', title: 'Access policy <b>' }] };
      if (p === '/api/v1/isms/bookstack-catalog' && u.searchParams.get('kind') === 'books') {
        if (options.catalogFail) throw new Error('BookStack down');
        return { items: [{ id: 7, name: 'Handbook' }] };
      }
      if (p === '/api/v1/isms/bookstack-catalog' && u.searchParams.get('kind') === 'pages') {
        const offset = Number(u.searchParams.get('offset'));
        return offset
          ? { items: [{ id: 12, name: 'Page 12' }], next_offset: null }
          : { items: [{ id: 11, name: 'Page <i>11</i>' }], next_offset: 1 };
      }
      if (p === '/api/v1/controls') return { items: [{ id: 'c1', ref: 'A1', title: 'Access' }] };
      if (p === '/api/v1/clauses') return { items: [{ id: 'k5', ref: '5.2', title: 'Policy' }] };
      if (p === '/api/v1/isms/bookstack-sections')
        return {
          items: options.sections ?? [
            {
              id: 's1',
              document_title: '<img src=x>Access policy',
              permalink: 'https://bookstack.example/link/11#bkmrk-x',
              page_title: 'Page 11',
              anchor: 'bkmrk-x',
              target_framework: 'A',
              target_ref: 'A1',
              captured_at: '2026-01-01',
              revision_count: 4,
              sha256: 'abcdef0123456789',
              archived: false,
            },
            {
              id: 's2',
              document_title: 'Old',
              permalink: 'https://bookstack.example/link/3',
              page_title: 'P3',
              captured_at: '2025',
              sha256: 'ffffffffffffffff',
              archived: true,
            },
          ],
        };
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Capture refused');
      return { ok: true };
    },
    patch: async (u) => {
      state.patched.push(u.pathname + u.search);
      return { ok: true };
    },
  });
  const root = ui.el('bookstackDocumentContent');
  await ui.api.initBookstackDocuments(
    options.me || { is_admin: true, bookstack_enabled: true },
    root,
  );
  return { ui, state, root };
}

test('BookStack policy capture links a page version to a framework requirement', async (t) => {
  const { ui, state } = await bookstack(t);
  assert.deepEqual(
    [...ui.el('policyDocument').options].map((o) => o.textContent),
    ['Choose policy or process…', 'Access policy <b>'],
  );
  assert.equal(ui.el('targetFramework').value, 'A');
  assert.deepEqual(
    [...ui.el('targetRequirement').options].map((o) => o.value),
    ['', 'control:c1', 'clause:k5'],
  );
  ui.el('bookSelect').value = '7';
  ui.el('bookSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('pageSelect').options.length === 2);
  assert.equal(ui.el('pageSelect').querySelector('i'), null);
  assert.equal(ui.el('morePages').style.display, '');
  ui.el('morePages').click();
  await settle(() => ui.el('pageSelect').options.length === 3);
  assert.equal(ui.el('morePages').style.display, 'none');
  ui.el('policyDocument').value = 'd1';
  ui.el('pageSelect').value = '12';
  ui.el('pageAnchor').value = 'bkmrk-scope';
  ui.el('targetRequirement').value = 'clause:k5';
  ui.el('sectionForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /captured and linked/.test(ui.el('bookstackStatus').textContent));
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/isms/bookstack-sections',
    body: {
      document_id: 'd1',
      page_id: 12,
      anchor: 'bkmrk-scope',
      target_control_id: null,
      target_clause_id: 'k5',
    },
  });
  ui.el('targetFramework').value = '';
  ui.el('targetFramework').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('targetRequirement').options.length, 1);
});

test('captured BookStack sections render safely and can be archived, restored and sampled', async (t) => {
  const { ui, state } = await bookstack(t);
  const rows = [...ui.el('sectionRows').querySelectorAll('tr')];
  assert.equal(ui.el('sectionRows').querySelector('img'), null);
  assert.match(rows[0].textContent, /Page 11 #bkmrk-x/);
  assert.match(rows[0].textContent, /SHA-256 abcdef012345…/);
  assert.match(rows[1].textContent, /Deleted framework · Deleted requirement/);
  assert.ok(rows[1].classList.contains('text-muted'));
  assert.equal(
    rows[0].querySelector('a[href*="snapshot"]').getAttribute('href'),
    '/api/v1/isms/bookstack-sections/s1/snapshot',
  );
  ui.el('sectionRows').querySelector('[data-archive="s1"]').click();
  await settle(() => state.patched.length === 1);
  ui.el('sectionRows').querySelector('[data-archive="s2"]').click();
  await settle(() => state.patched.length === 2);
  assert.deepEqual(state.patched, [
    '/api/v1/isms/bookstack-sections/s1?archived=true',
    '/api/v1/isms/bookstack-sections/s2?archived=false',
  ]);
  ui.el('sectionRows').querySelector('[data-sample="s1"]').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.entity_type, 'bookstack_section');
  assert.equal(state.posted[0].body.evidence_url, '/api/v1/isms/bookstack-sections/s1/snapshot');
});

test('BookStack readers only see captured versions; disabled or failing catalogues are handled', async (t) => {
  const reader = await bookstack(t, { me: { can_view_isms: true, bookstack_enabled: true } });
  assert.equal(reader.ui.el('sectionForm').style.display, 'none');
  assert.equal(reader.ui.document.querySelector('[data-archive]'), null);
  assert.ok(!reader.ui.calls.some((c) => c.url.includes('kind=books')));
  const off = await bookstack(t, { me: { is_admin: true } });
  assert.equal(off.ui.calls.filter((c) => c.url !== '/api/v1/frameworks').length, 0);
  const failing = await bookstack(t, { catalogFail: true });
  assert.match(failing.ui.el('bookstackStatus').textContent, /Could not load BookStack catalogue/);
  const empty = await bookstack(t, { sections: [], postFail: true });
  assert.match(empty.ui.el('sectionRows').textContent, /No policy versions captured yet/);
  empty.ui
    .el('sectionForm')
    .dispatchEvent(new empty.ui.window.Event('submit', { cancelable: true }));
  await settle(() =>
    /Could not capture policy version: Error: Capture refused/.test(
      empty.ui.el('bookstackStatus').textContent,
    ),
  );
});

// ------------------------------------------------------------------- Agents
const naive = (ms) => new Date(Date.now() + ms).toISOString().replace('Z', '');
function agentsFixture(overrides = {}) {
  return {
    demo_mode: false,
    agents: [
      {
        id: 'a1',
        name: '<b>web-1</b>',
        enabled: true,
        expires_at: naive(86400000),
        last_seen: naive(-1000),
        health: {
          queued: 2,
          queue_bytes: 10,
          sources: { journal: 'ok' },
          filtered_by_source: { journal: 3 },
          version: '1.2',
        },
      },
      {
        id: 'a2',
        name: 'old',
        enabled: true,
        expires_at: naive(-1000),
        last_seen: null,
        enrollment_profile_id: 'p1',
      },
      {
        id: 'a3',
        name: 'paused',
        enabled: true,
        expires_at: naive(86400000),
        last_seen: naive(-1000),
        health: { delivery_blocked: true },
      },
      {
        id: 'a4',
        name: 'quiet',
        enabled: true,
        expires_at: naive(86400000),
        last_seen: naive(-600000),
      },
      {
        id: 'a5',
        name: 'gone',
        enabled: false,
        expires_at: naive(86400000),
        enrollment_profile_id: 'missing',
      },
    ],
    ...overrides,
  };
}

async function agentsPage(t, options = {}) {
  const state = { posted: [], data: options.data || agentsFixture() };
  const ui = await page(t, 'agents', {
    globals: {
      setInterval: () => 0,
      confirm: () => options.confirm ?? true,
      navigator: options.navigator || { clipboard: { writeText: async () => {} } },
      ...options.globals,
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/admin/agents') {
        if (options.fail) throw new Error(JSON.stringify({ detail: 'Agents unavailable' }));
        return structuredClone(state.data);
      }
      if (u.pathname === '/api/v1/admin/agent-enrollment-profiles')
        return {
          profiles: [
            {
              id: 'p1',
              name: 'Fleet',
              state: 'active',
              enrollment_count: 1,
              token_days: 90,
              allowed_cidrs: [],
            },
          ],
        };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error(JSON.stringify({ detail: [{ msg: 'bad' }] }));
      return { token: 'keen-agent-secret-' + state.posted.length };
    },
  });
  const $ = (id) => ui.el('ka-' + id);
  await settle(() => $('agents').children.length || !$('status').hidden);
  return { ui, state, $ };
}

test('agents show health states, provenance and source details as text', async (t) => {
  const { $ } = await agentsPage(t);
  assert.equal($('endpoint').textContent, 'https://keen.example/api/v1/otlp/logs');
  const cards = [...$('agents').querySelectorAll('article.card')];
  assert.deepEqual(
    cards.map((c) => c.querySelector('.badge').textContent),
    ['Connected', 'Credential expired', 'Delivery paused', 'Awaiting contact', 'Revoked'],
  );
  assert.equal(cards[0].querySelector('h2').textContent, '<b>web-1</b>');
  assert.match(cards[0].textContent, /Queued: 2 · Queue bytes: 10/);
  assert.match(cards[0].textContent, /journal: ok/);
  assert.match(cards[0].textContent, /Filtered before delivery: journal: 3/);
  assert.match(cards[0].textContent, /Individually registered/);
  assert.match(cards[1].textContent, /Enrollment profile: Fleet/);
  assert.match(cards[4].textContent, /Enrollment profile: missing/);
  assert.match(cards[1].textContent, /Last contact: Never/);
  // Revoked agents have no actions.
  assert.equal(cards[4].querySelectorAll('.agent-actions button').length, 0);
  assert.equal(cards[0].querySelectorAll('.agent-actions button').length, 2);
});

test('registering an agent reveals its credential once and dismissal clears it', async (t) => {
  const { ui, state, $ } = await agentsPage(t);
  $('agent-name').value = 'db-1';
  $('expiry').value = '30';
  $('register').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => $('credential').hidden === false);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/admin/agents',
    body: { name: 'db-1', expires_days: 30 },
  });
  assert.equal($('token').value, 'keen-agent-secret-1');
  assert.equal($('token').type, 'password');
  assert.equal($('agent-name').value, '');
  $('reveal').click();
  assert.equal($('token').type, 'text');
  assert.equal($('reveal').textContent, 'Hide credential');
  $('reveal').click();
  assert.equal($('token').type, 'password');
  $('copy').click();
  await settle(() => /Credential copied/.test($('status').textContent));
  $('dismiss').click();
  assert.equal($('token').value, '');
  assert.equal($('credential').hidden, true);
  // Leaving the page also clears a displayed credential.
  $('register').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => $('token').value === 'keen-agent-secret-2');
  ui.window.dispatchEvent(new ui.window.Event('pagehide'));
  assert.equal($('token').value, '');
});

test('agent credentials can be rotated or revoked after confirmation', async (t) => {
  const { state, $ } = await agentsPage(t);
  const [rotate, revoke] = $('agents')
    .querySelector('article.card .agent-actions')
    .querySelectorAll('button');
  rotate.click();
  await settle(() => $('token').value === 'keen-agent-secret-1');
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/admin/agents/a1/rotate',
    body: { expires_days: 90 },
  });
  const [, revoke2] = $('agents')
    .querySelector('article.card .agent-actions')
    .querySelectorAll('button');
  revoke2.click();
  await settle(() => state.posted.length === 2);
  assert.deepEqual(state.posted[1], { path: '/api/v1/admin/agents/a1/revoke', body: {} });
  await settle(() => $('token').value === '');
  assert.ok(revoke);
});

test('declined confirmations, demo mode and failures leave agents unchanged', async (t) => {
  const declined = await agentsPage(t, { confirm: false });
  declined.$('agents').querySelector('.agent-actions button').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(declined.state.posted.length, 0);
  const demo = await agentsPage(t, { data: agentsFixture({ demo_mode: true }) });
  assert.equal(demo.$('register-button').disabled, true);
  assert.match(demo.$('status').textContent, /demo environment/);
  assert.equal(demo.$('agents').querySelector('.agent-actions button'), null);
  const failing = await agentsPage(t, { fail: true });
  assert.equal(failing.$('status').textContent, 'Agents unavailable');
  assert.match(failing.$('status').className, /alert-danger/);
  const rejected = await agentsPage(t, { postFail: true, navigator: {} });
  rejected
    .$('register')
    .dispatchEvent(new rejected.ui.window.Event('submit', { cancelable: true }));
  await settle(
    () => rejected.$('status').textContent === 'Request failed; check the entered values.',
  );
  assert.equal(rejected.$('register-button').disabled, false);
  rejected.$('copy').click();
  await settle(() => /Clipboard unavailable/.test(rejected.$('status').textContent));
  const empty = await agentsPage(t, { data: { demo_mode: false, agents: [] } });
  assert.match(empty.$('agents').textContent, /No agents registered yet/);
  empty.$('refresh').click();
  await settle(() => empty.ui.calls.filter((c) => c.url === '/api/v1/admin/agents').length === 2);
});

test('the agent YAML template downloads with the server endpoint', async (t) => {
  let blob;
  class CaptureUrl extends URL {
    static createObjectURL(b) {
      blob = b;
      return 'blob:x';
    }
    static revokeObjectURL() {}
  }
  const { $ } = await agentsPage(t, { globals: { URL: CaptureUrl } });
  $('yaml').click();
  const text = await blob.text();
  assert.match(
    text,
    /^endpoint: "https:\/\/keen\.example\/api\/v1\/otlp\/logs"\ntoken_file: \/etc\/keen-agent\/token/,
  );
  assert.equal(blob.type, 'application/yaml');
});
