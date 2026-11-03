import test from 'node:test';
import assert from 'node:assert/strict';
import { localPath, safeNext } from '../public/pages/navigation-security.js';
import { loadUi } from './support/load-ui.mjs';
const origin = 'https://keen.example';
for (const value of [
  '//evil.example',
  '/\\evil.example',
  '/\t/evil.example',
  '/\n/evil.example',
  'https://evil.example',
  'javascript:alert(1)',
  '/login.html?q=x',
  '/mfa.html',
]) {
  test('post-login navigation rejects ' + JSON.stringify(value), () =>
    assert.equal(safeNext(value, origin), '/'),
  );
}
for (const value of ['/events.html?source=github#results', '/account.html#security', '/']) {
  test('post-login navigation retains ' + value, () =>
    assert.equal(safeNext(value, origin), value),
  );
}

test('post-authentication navigation drops accidental login credentials', () => {
  const destination = new URL(
    safeNext('/events.html?password=synthetic-secret&username=test&source=loki', origin),
    origin,
  );
  assert.equal(destination.searchParams.has('password'), false);
  assert.equal(destination.searchParams.has('username'), false);
  assert.equal(destination.searchParams.get('source'), 'loki');
});

// The URL parser resolves dot segments, decodes "%2e" as ".", reads "\" as "/"
// and drops tabs and newlines, so each of these can resolve to the path
// "//evil.example/phish", which a browser reads as another host.
const OFFSITE = [
  '/.//evil.example/phish',
  '/././/evil.example/phish',
  '/%2e//evil.example/phish',
  '/%2E//evil.example/phish',
  '/%2e%2E//evil.example/phish',
  '/x/..//evil.example/phish',
  '/x/%2e%2e//evil.example/phish',
  '/X/%2E%2E//evil.example/phish?next=/',
  '/..//evil.example/phish',
  '/.//evil.example/phish#frag',
  '  /.//evil.example/phish  ',
  '/./\\evil.example/phish',
  '/.\\/evil.example/phish',
  '/\\/evil.example/phish',
  '/.//\tevil.example/phish',
  '/\t/evil.example/phish',
  '/\r\n/evil.example/phish',
  '/.\n//evil.example/phish',
  'javascript:alert(1)//',
  'JaVaScRiPt:alert(1)',
  'data:text/html,<script>alert(1)</script>',
  'DATA:text/html;base64,PHNjcmlwdD4=',
  'HTTPS://evil.example/phish',
  'https:evil.example/phish',
];
for (const value of OFFSITE) {
  test('post-login navigation stays on Keen for ' + JSON.stringify(value), () => {
    const out = safeNext(value, origin);
    assert.equal(out, '/');
  });
}

test('post-login navigation keeps encoded slashes and other same-origin paths', () => {
  for (const [value, expected] of [
    ['/%2f/evil.example/phish', '/%2f/evil.example/phish'],
    ['/%2F%2Fevil.example', '/%2F%2Fevil.example'],
    ['/%5c/evil.example', '/%5c/evil.example'],
    ['/x/../events.html?q=1', '/events.html?q=1'],
    ['/./account.html#security', '/account.html#security'],
  ]) {
    const out = safeNext(value, origin);
    assert.equal(out, expected, value);
    assert.equal(new URL(out, origin).origin, origin, value);
  }
});

test('local paths are returned as given and anything that could leave Keen is refused', () => {
  for (const value of ['/', '/events.html?q=ssh#results', '/.//evil.example', '/%2f/x'])
    assert.equal(localPath(value), value);
  // As given, a path beginning with one slash keeps the page's own host.
  assert.equal(new URL(localPath('/.//evil.example'), origin).origin, origin);
  for (const value of [
    '',
    null,
    undefined,
    '//evil.example',
    '/\\evil.example',
    '/x\\y',
    '/\t/evil.example',
    '/\n/evil.example',
    '/\u0000x',
    '/\u007fx',
    ' /events.html',
    'events.html',
    '#top',
    'javascript:alert(1)',
    'data:text/html,x',
    'https://evil.example/',
  ])
    assert.equal(localPath(value), '', JSON.stringify(value));
});

// Behind a front proxy that rewrites the path, nginx never sees the "//" a
// browser sent, so a page can open at "//host/". Whichever module a page
// starts from, it gives way to the home page before page code runs.
test('a page opened at a path beginning with // gives way to the home page', async () => {
  for (const [href, expected] of [
    ['https://keen.example//evil.example/', ['/']],
    ['https://keen.example///evil.example/phish?framework=A#part', ['/']],
    ['https://keen.example/\\evil.example/', ['/']],
    ['https://keen.example/', []],
    ['https://keen.example/controls.html?next=//evil.example/', []],
    ['https://keen.example/x//evil.example/', []],
    ['https://keen.example/%2f/evil.example/', []],
  ]) {
    for (const entry of ['pages/navigation-security.js', 'app.js']) {
      const replaced = [];
      const { pathname } = new URL(href);
      const ui = await loadUi(entry, {
        url: href,
        globals: { location: { pathname, replace: (target) => replaced.push(target) } },
      });
      ui.close();
      assert.deepEqual(replaced, expected, `${entry} at ${href}`);
    }
  }
});
