import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

test('person quiz history escapes content, paginates both lists, and retries failed reads', async (t) => {
  const calls = [];
  let fail = false;
  const ui = await loadUi('pages/person-quizzes.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async (url) => {
        calls.push(url);
        if (fail) throw new Error('Offline');
        const later = url.includes('offset=50');
        return {
          eligible: false,
          assignments: [{ id: 'q1', title: '<img src=x>' }],
          assignments_total: 51,
          assignments_next_offset: later ? null : 50,
          items: [
            {
              quiz_id: 'q1',
              title: '<b>Quiz</b>',
              opens_on: '2026-01-01',
              due_on: '2026-02-01',
              status: 'complete',
              round_status: 'closed',
              completed_at: '2026-01-02',
              score: 85,
              passed: null,
            },
          ],
          total: 51,
          next_offset: later ? null : 50,
        };
      },
    },
  });
  t.after(() => ui.close());
  const host = ui.document.getElementById('host');
  const controller = ui.api.personQuizzes(host, 'person-1');
  await controller.load();
  assert.equal(host.querySelector('img,b'), null);
  assert.match(host.textContent, /85% · No pass mark/);
  host.querySelector('[data-assignment-pages] button').click();
  await settle(() => calls.length === 2 && host.querySelector('[data-result-pages]'));
  assert.match(calls[1], /assignments_offset=50/);
  host.querySelector('[data-assignment-pages] button').click();
  await settle(() => calls.length === 3 && host.querySelector('[data-result-pages]'));
  host.querySelector('[data-result-pages] button').click();
  await settle(() => calls.length === 4 && host.querySelector('[data-result-pages]'));
  assert.match(calls[3], /\?offset=50/);
  fail = true;
  await controller.load();
  assert.match(host.textContent, /could not be loaded/);
  fail = false;
  host.querySelector('button').click();
  await settle(() => host.querySelector('table'));
});
