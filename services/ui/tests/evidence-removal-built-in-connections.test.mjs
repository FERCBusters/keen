// Removing a built-in connection's evidence setup from Admin: its impact
// preview names collections, and its environment credentials stay in place.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');

test('clearing a built-in connection previews its collections and keeps server credentials', async (t) => {
  const posts = [];
  let removed = 0;
  const ui = await loadUi('pages/evidence-removal.js', {
    html,
    setup(window) {
      // jsdom has no modal dialogs; these open and close as a browser would.
      window.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      window.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
    },
    vendor: {
      apiGet: async (url) => {
        assert.equal(url, '/api/v1/admin/evidence-connections/builtin%3Aforgejo/removal');
        return {
          kind: 'builtin',
          name: 'Forgejo <b>main</b>',
          collections: 3,
          definitions: [],
          automatic_mappings: 12,
          collectors: [],
          fingerprint: 'plan-1',
        };
      },
      apiPost: async (url, body) => posts.push(JSON.parse(JSON.stringify({ url, body }))),
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(`hub-${id}`);
  await ui.api.confirmEvidenceRemoval(
    { key: 'builtin:forgejo' },
    { onRemoved: async () => removed++, onError: (error) => assert.fail(error) },
  );
  assert.equal(el('confirm').open, true);
  assert.equal(el('confirm-heading').textContent, 'Clear connection');
  assert.equal(el('delete').textContent, 'Clear connection');
  assert.equal(
    el('impact').textContent,
    'Forgejo <b>main</b>: remove 3 collection(s), 0 mapping rule(s) and 12 automatic evidence-to-control link(s).',
  );
  assert.equal(el('impact').querySelector('b'), null);
  assert.equal(
    el('retained').textContent,
    'Collected events, evidence artifacts, measurements, manual/imported mappings and shared cross-source rules remain. Server environment credentials remain configured.',
  );
  el('typed-name').value = 'Forgejo <b>main</b>';
  el('typed-name').oninput();
  await el('delete').onclick();
  assert.deepEqual(posts, [
    {
      url: '/api/v1/admin/evidence-connections/builtin%3Aforgejo/remove',
      body: { fingerprint: 'plan-1' },
    },
  ]);
  assert.equal(removed, 1);
  assert.equal(el('confirm').open, false);
});
