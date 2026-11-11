// A document's Revisions and Comments tabs: saved versions of the text written
// in Keen, previewed through the shared sanitiser, and the discussion managers
// add to it. Each tab reports its own failure, so one that cannot load leaves
// the other readable.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const hostile = '<img src=x onerror=alert(1)>';
const base = '/api/v1/isms/documents/d1';

const documentRow = {
  id: 'd1',
  title: 'Access policy',
  document_type: 'policy',
  description: '',
  tags: [],
  content_html: '<p>Current</p>',
  controls: [],
  clauses: [],
  updated_at: '2026-02-01T00:00:00',
};

async function documentPage(
  t,
  {
    me = { is_admin: true },
    query = '',
    revisions = [],
    comments = [],
    fail = {},
    tabs = null,
  } = {},
) {
  const state = { comments: structuredClone(comments), posted: [] };
  const ui = await page(t, 'isms-document', {
    me,
    query: '&id=d1' + query,
    setup(window) {
      // Bootstrap's tab component, recording which tab was shown.
      if (tabs)
        window.bootstrap = {
          Tab: { getOrCreateInstance: (el) => ({ show: () => tabs.push(el.id) }) },
        };
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === base) return structuredClone(documentRow);
      if (p === `${base}/revisions`) {
        if (fail.revisions) throw new Error(fail.revisions);
        return structuredClone(revisions);
      }
      if (p.startsWith(`${base}/revisions/`)) {
        if (fail.revision) throw new Error(fail.revision);
        const version = Number(decodeURIComponent(p.split('/').pop()));
        return {
          version,
          sha256: `sha-${version}${hostile}`,
          content_html: `<p>Version ${version} text</p><script>alert(1)</script>`,
          created_at: '2026-01-01T00:00:00',
        };
      }
      if (p === `${base}/comments`) {
        if (fail.comments) throw new Error(fail.comments);
        return structuredClone(state.comments);
      }
      if (p === '/api/v1/isms/document/d1/changelog') return { items: [] };
      if (p === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (p === '/api/v1/stories') return { items: [], total: 0 };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      state.posted.push({ path: u.pathname, body: plain(body) });
      if (fail.post) throw new Error(fail.post);
      state.comments.push({
        id: 'c9',
        body: body.body,
        author: { username: 'alice' },
        created_at: '2026-03-01T00:00:00',
      });
      return { id: 'c9' };
    },
  });
  await settle(
    () =>
      !/^Loading/.test(ui.el('revisionList').textContent) &&
      !/^Loading/.test(ui.el('commentList').textContent),
  );
  return { ui, state };
}

const submitComment = (ui) =>
  ui.el('commentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

test('revisions are listed newest first and a chosen version is previewed safely', async (t) => {
  const { ui } = await documentPage(t, {
    revisions: [
      { version: 2, sha256: 'b', created_at: '2026-01-02T03:04:05' },
      { version: 1, sha256: 'a', created_at: '2026-01-01T00:00:00' },
    ],
  });
  const buttons = [...ui.el('revisionList').querySelectorAll('[data-revision]')];
  assert.deepEqual(
    buttons.map((button) => [button.textContent, button.getAttribute('aria-pressed')]),
    [
      ['Version 2 · 2026-01-02T03:04:05', 'false'],
      ['Version 1 · 2026-01-01T00:00:00', 'false'],
    ],
  );
  // A click beside the buttons opens nothing.
  ui.el('revisionList').click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(ui.el('revisionPreview').textContent, '');

  buttons[1].click();
  await settle(() => ui.el('revisionPreview').textContent.includes('Version 1 text'));
  const preview = ui.el('revisionPreview');
  assert.equal(preview.querySelector('.fw-semibold').textContent, 'Version 1');
  // The checksum is text, and stored revision HTML passes through the sanitiser.
  assert.equal(preview.querySelector('code').textContent, `sha-1${hostile}`);
  assert.equal(preview.querySelector('img'), null);
  assert.equal(preview.querySelector('script'), null);
  assert.deepEqual(
    buttons.map((button) => button.getAttribute('aria-pressed')),
    ['false', 'true'],
  );
  // The current document is left as it is.
  assert.equal(ui.el('documentContent').textContent, 'Current');
});

test('a document without revisions or comments says so', async (t) => {
  const { ui } = await documentPage(t);
  assert.equal(ui.el('revisionList').textContent, 'No text written in Keen has been saved yet.');
  assert.equal(ui.el('commentList').textContent, 'No comments yet.');
  assert.equal(ui.el('status').textContent, '');
});

test('comments show their author, time and text, and a former user is named as such', async (t) => {
  const { ui } = await documentPage(t, {
    comments: [
      {
        id: 'c1',
        body: `${hostile}Looks good\nSecond line`,
        author: { username: 'dana' },
        created_at: '2026-01-03T00:00:00',
      },
      { id: 'c2', body: 'Plain', author: null, created_at: '2026-01-04T00:00:00' },
    ],
  });
  const rows = [...ui.el('commentList').children];
  assert.deepEqual(
    rows.map((row) => [
      row.querySelector('strong').textContent,
      row.querySelector('small').textContent,
      row.querySelector('div').textContent,
    ]),
    [
      ['dana', '2026-01-03T00:00:00', `${hostile}Looks good\nSecond line`],
      ['Former user', '2026-01-04T00:00:00', 'Plain'],
    ],
  );
  assert.equal(ui.el('commentList').querySelector('img'), null);
});

test('managers add a comment, which is listed and the box cleared', async (t) => {
  const { ui, state } = await documentPage(t);
  assert.equal(ui.el('commentForm').hidden, false);
  ui.el('commentBody').value = 'Reviewed for Q1';
  submitComment(ui);
  await settle(() => ui.el('status').textContent === 'Comment added.');
  assert.deepEqual(state.posted, [{ path: `${base}/comments`, body: { body: 'Reviewed for Q1' } }]);
  assert.equal(ui.el('commentBody').value, '');
  assert.equal(
    ui.el('commentList').querySelector('.whitespace-pre-wrap').textContent,
    'Reviewed for Q1',
  );
});

test('a refused comment is reported and keeps the text typed', async (t) => {
  const { ui } = await documentPage(t, { fail: { post: 'Comment cannot be empty' } });
  ui.el('commentBody').value = 'Draft';
  submitComment(ui);
  await settle(() => ui.el('status').textContent.includes('Comment cannot be empty'));
  assert.equal(ui.el('status').textContent, 'Could not add the comment: Comment cannot be empty');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('commentBody').value, 'Draft');
});

test('readers see revisions and comments without the comment form', async (t) => {
  const { ui } = await documentPage(t, {
    me: { can_view_isms: true },
    comments: [{ id: 'c1', body: 'Noted', author: { username: 'dana' }, created_at: '' }],
  });
  assert.equal(ui.el('commentForm').hidden, true);
  assert.equal(ui.el('commentList').children.length, 1);
});

test('revisions and comments that cannot load are each reported, leaving the other', async (t) => {
  const revisionsDown = await documentPage(t, {
    fail: { revisions: 'Revisions offline' },
    comments: [{ id: 'c1', body: 'Still here', author: { username: 'dana' }, created_at: '' }],
  });
  assert.equal(revisionsDown.ui.el('revisionList').textContent, 'Revisions could not be loaded.');
  assert.equal(
    revisionsDown.ui.el('status').textContent,
    'Could not load revisions: Revisions offline',
  );
  assert.equal(revisionsDown.ui.el('status').dataset.alert, 'warning');
  assert.equal(revisionsDown.ui.el('commentList').textContent.includes('Still here'), true);

  const commentsDown = await documentPage(t, {
    fail: { comments: 'Comments offline' },
    revisions: [{ version: 1, sha256: 'a', created_at: '2026-01-01T00:00:00' }],
  });
  assert.equal(commentsDown.ui.el('commentList').textContent, 'Comments could not be loaded.');
  assert.equal(
    commentsDown.ui.el('status').textContent,
    'Could not load comments: Comments offline',
  );
  assert.equal(commentsDown.ui.el('revisionList').querySelectorAll('[data-revision]').length, 1);
});

test('a revision that cannot be opened is reported and the preview is unchanged', async (t) => {
  const { ui } = await documentPage(t, {
    revisions: [{ version: 3, sha256: 'c', created_at: '2026-01-03T00:00:00' }],
    fail: { revision: 'Revision gone' },
  });
  ui.el('revisionList').querySelector('[data-revision="3"]').click();
  await settle(() => ui.el('status').textContent.includes('Revision gone'));
  assert.equal(ui.el('status').textContent, 'Could not open the revision: Revision gone');
  assert.equal(ui.el('revisionPreview').textContent, '');
});

test('the tab parameter and the address hash open the Revisions, Comments or Changelog tab', async (t) => {
  const tabs = [];
  const { ui } = await documentPage(t, { query: '&tab=comments', tabs });
  await settle(() => tabs.length === 1);
  assert.deepEqual(tabs, ['documentCommentsTab']);
  const revisions = [];
  const second = await documentPage(t, { query: '&tab=revisions', tabs: revisions });
  await settle(() => revisions.length === 1);
  assert.deepEqual(revisions, ['documentRevisionsTab']);
  ui.window.dispatchEvent(new ui.window.HashChangeEvent('hashchange'));
  assert.deepEqual(tabs, ['documentCommentsTab', 'documentCommentsTab']);
  second.ui.close();
});
