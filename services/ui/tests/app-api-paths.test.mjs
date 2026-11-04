import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// Pages build API paths from identifiers taken from the page address. The
// browser resolves "." and ".." segments, encoded or not, before sending, so
// each API helper refuses them rather than reach another route.
const sent = [];
const record =
  (method) =>
  async (path, ...rest) => {
    sent.push([method, path, rest.length]);
    return { ok: true };
  };
const ui = await loadUi('app.js', {
  vendor: Object.fromEntries(
    ['apiGet', 'apiPost', 'apiPut', 'apiPatch', 'apiDelete', 'apiPostForm', 'apiRequest'].map(
      (name) => [name, record(name)],
    ),
  ),
});
const a = ui.api;
test.after(() => ui.close());

const helpers = [
  'apiGet',
  'apiPost',
  'apiPut',
  'apiPatch',
  'apiDelete',
  'apiPostForm',
  'apiRequest',
];

for (const path of [
  '/api/v1/me/quizzes/rounds/../attempt',
  '/api/v1/me/quizzes/rounds/%2e%2e/attempt',
  '/api/v1/me/quizzes/rounds/.%2E/attempt',
  '/api/v1/sources/./me',
  '/api/v1/sources/%2E',
  '/api/v1/sources/..',
])
  test('API helpers refuse a dot segment: ' + path, async () => {
    sent.length = 0;
    for (const name of helpers) await assert.rejects(a[name](path, {}), /Invalid API path/, name);
    assert.deepEqual(sent, []);
  });

test('ordinary API paths, queries and arguments pass through unchanged', async () => {
  sent.length = 0;
  const paths = [
    '/api/v1/me',
    '/api/v1/sources/a.b..c/insights',
    '/api/v1/admin/frameworks/iso/nodes/control/A%2F1',
    '/api/v1/events?q=..&next=./x',
    '/api/v1/sources/..x',
  ];
  for (const path of paths) await a.apiGet(path, { quiet: true });
  await a.apiPost('/api/v1/audits/a1/evidence', { id: 1 }, { 'X-Test': '1' });
  assert.deepEqual(sent, [
    ...paths.map((path) => ['apiGet', path, 1]),
    ['apiPost', '/api/v1/audits/a1/evidence', 2],
  ]);
});
