import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');

for (const enabled of [true, false]) {
  test(`IMAP runner visibility and queued feedback (enabled=${enabled})`, async () => {
    const writes = [];
    let finish;
    const ui = await loadUi('pages/admin.js', {
      html,
      globals: { setInterval: () => 0, clearInterval: () => {} },
      url: 'https://keen.example/admin.html#ingest',
      vendor: {
        initNavbar: async () => ({ is_admin: true }),
        initCollapsibleFilterSections: () => {},
        apiGet: async (path) => {
          if (path.endsWith('/ingest/status')) return { enabled: { imap: enabled } };
          return { items: [], users: [], groups: [], permissions: [] };
        },
        apiPost: (path, body) => {
          writes.push({ path, body });
          return new Promise((resolve) => {
            finish = resolve;
          });
        },
        apiPatch: async () => ({}),
        apiPostForm: async () => ({}),
        debounce: (fn) => fn,
        withFramework: (path) => path,
        entityChangelogHtml: () => '',
        userPillHtml: () => '',
        toast: (el, message) => {
          el.textContent = message;
        },
      },
    });
    try {
      const doc = ui.window.document;
      const button = doc.querySelector('[data-run="/api/v1/admin/ingest/imap/run"]');
      assert.ok(button);
      assert.equal(button.style.display, enabled ? '' : 'none');
      if (!enabled) return;
      button.click();
      button.click();
      assert.equal(writes.length, 1);
      assert.equal(writes[0].path, '/api/v1/admin/ingest/imap/run');
      assert.equal(button.disabled, true);
      finish({ queued: true, note: 'IMAP polling queued.' });
      await settle(() => !button.disabled);
      assert.equal(doc.getElementById('status').textContent, 'IMAP polling queued.');
      assert.match(doc.getElementById('out').textContent, /queued/);
    } finally {
      ui.close();
    }
  });
}
