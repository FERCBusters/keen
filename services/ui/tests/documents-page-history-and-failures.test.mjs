// The Documents page with documents that have no history, unfiled documents,
// a document deleted in another session while it is being saved, and saves
// and comments the server refuses.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));
const doc = (id, extra = {}) => ({
  id,
  title: `Document ${id}`,
  document_type: 'policy',
  folder_id: 'f1',
  tags: [],
  description: '',
  content_html: '<p>Body</p>',
  content_version: 2,
  ...extra,
});

async function documents(t, { docs = [doc('d1')], revisions = [], comments = [], fail = {} } = {}) {
  const state = { docs: structuredClone(docs), writes: [], reads: [] };
  const ui = await page(t, 'documents', {
    get: async (u) => {
      state.reads.push(u.pathname);
      if (u.pathname === '/api/v1/isms/document-folders') return [{ id: 'f1', name: 'Policies' }];
      if (u.pathname === '/api/v1/isms/documents') return { items: structuredClone(state.docs) };
      if (u.pathname.endsWith('/revisions')) return revisions;
      if (u.pathname.endsWith('/comments')) return comments;
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      state.writes.push(['POST', u.pathname, plain(body)]);
      if (fail.post) throw new Error(fail.post);
      return { ok: true };
    },
    patch: async (u, body) => {
      state.writes.push(['PATCH', u.pathname, plain(body)]);
      if (fail.patch) throw new Error(fail.patch);
      return { id: u.pathname.split('/').pop() };
    },
  });
  await settle(() => ui.el('documentList').children.length);
  const open = async (id) => {
    ui.el('documentList').querySelector(`[data-document="${id}"]`).click();
    await settle(() => ui.el('historyCard').style.display === '' && ui.el('commentList').innerHTML);
  };
  const submit = (id) =>
    ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  return { ui, state, open, submit };
}

test('a document without revisions or comments says so, and the space between versions opens none', async (t) => {
  const { ui, state, open } = await documents(t, { docs: [doc('d1', { folder_id: null })] });
  await open('d1');
  assert.equal(ui.el('revisionList').textContent, 'No edited content yet.');
  assert.equal(ui.el('commentList').textContent, 'No comments yet.');
  // An unfiled document opens with no folder chosen.
  assert.equal(ui.el('folder').value, '');
  assert.equal(ui.el('folder').selectedOptions[0].textContent, 'Top level / unfiled');
  const reads = state.reads.length;
  ui.el('revisionList').querySelector('p').click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(state.reads.length, reads);
  assert.equal(ui.el('revisionPreview').innerHTML, '');
  assert.equal(ui.el('status').textContent, '');
});

test('a document deleted in another session while being saved leaves nothing selected', async (t) => {
  const { ui, state, open, submit } = await documents(t, { docs: [doc('d1'), doc('d2')] });
  await open('d1');
  // Another session deletes the document before this save's reload.
  state.docs = state.docs.filter((item) => item.id !== 'd1');
  submit('documentForm');
  await settle(() => ui.el('status').textContent === 'Document saved.');
  assert.deepEqual(state.writes[0].slice(0, 2), ['PATCH', '/api/v1/isms/documents/d1']);
  assert.deepEqual(
    [...ui.el('documentList').querySelectorAll('[data-document]')].map((b) => [
      b.dataset.document,
      b.classList.contains('active'),
    ]),
    [['d2', false]],
  );
  // With nothing selected a comment is not sent anywhere.
  ui.el('comment').value = 'Checked';
  submit('commentForm');
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(state.writes.length, 1);
  assert.equal(ui.el('comment').value, 'Checked');
  assert.equal(ui.el('status').textContent, 'Document saved.');
});

test('refused saves and comments are reported and keep what was typed', async (t) => {
  const { ui, state, open, submit } = await documents(t, {
    fail: { patch: 'Edited in another session; reload first', post: 'Comments are closed' },
  });
  await open('d1');
  ui.el('title').value = 'Renamed document';
  submit('documentForm');
  await settle(() => ui.el('status').textContent === 'Edited in another session; reload first');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('title').value, 'Renamed document');
  ui.el('comment').value = 'Looks right';
  submit('commentForm');
  await settle(() => ui.el('status').textContent === 'Comments are closed');
  assert.equal(ui.el('comment').value, 'Looks right');
  assert.deepEqual(
    state.writes.map(([method, path]) => [method, path]),
    [
      ['PATCH', '/api/v1/isms/documents/d1'],
      ['POST', '/api/v1/isms/documents/d1/comments'],
    ],
  );
});
