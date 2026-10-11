import test from 'node:test';
import assert from 'node:assert/strict';
import { safeNext } from '../public/pages/navigation-security.js';
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
