// Sample-export masking in the browser. Audit ZIP captures and evidence PDF
// captures share these rules, and the cases follow the server-side contract in
// services/api/tests/test_redaction_contracts.py so both sides hide the same
// addresses.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { loadUi } from './support/load-ui.mjs';

const NodeFilter = { SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2 };

async function masking(t, html = '') {
  const ui = await loadUi('pages/evidence-export/masking.js', { html, globals: { NodeFilter } });
  t.after(ui.close);
  return ui;
}

const cases = [
  ['contact a@example.test', 'contact [MASKED_EMAIL]'],
  ['from 192.0.2.1', 'from [MASKED_IP]'],
  ['from 2001:db8::1', 'from [MASKED_IP]'],
  ['at 13:40:19', 'at 13:40:19'],
  ['version 1.2.3', 'version 1.2.3'],
  ['bad 999.999.999.999', 'bad 999.999.999.999'],
  // Sentence punctuation does not let an address escape masking.
  ['Login from 192.0.2.1.', 'Login from [MASKED_IP].'],
  ['Mail a@example.test.', 'Mail [MASKED_EMAIL].'],
  ['Peers: 192.0.2.1, 198.51.100.7.', 'Peers: [MASKED_IP], [MASKED_IP].'],
  ['from fe80::1.', 'from [MASKED_IP].'],
  // A colon after an address introduces the rest of a log message.
  [
    'address 2001:db8::1: Name or service not known',
    'address [MASKED_IP]: Name or service not known',
  ],
  ['peer 2001:db8:1:2:3:4:5:6: refused', 'peer [MASKED_IP]: refused'],
  ['at 13:40:19: done', 'at 13:40:19: done'],
  ['mapped 0:0:0:0:0:ffff:192.0.2.1.', 'mapped [MASKED_IP].'],
  ['route via 2001:db8::.', 'route via [MASKED_IP].'],
  ['(a@example.test)', '([MASKED_EMAIL])'],
  ['a@mail.example.co.uk', '[MASKED_EMAIL]'],
  // A display name identifies the person as much as the address does.
  ['Alice Smith <alice@example.test>', '[MASKED_EMAIL]'],
  [
    'To: "Smith, Alice" <alice@example.test>, Bob <bob@example.test>',
    'To: [MASKED_EMAIL], [MASKED_EMAIL]',
  ],
  ['Dr. J. R. R. Tolkien-Smith <jrr@example.test>', '[MASKED_EMAIL]'],
  ['Ünal Yılmaz <unal@example.test>', '[MASKED_EMAIL]'],
  ['Reply-To: <noreply@example.test>', 'Reply-To: [MASKED_EMAIL]'],
  ['Version 1.2.3 sent by bob <b@example.test>', 'Version 1.2.3 [MASKED_EMAIL]'],
  ['ticket #42: Bob <bob@example.test>', 'ticket #42: [MASKED_EMAIL]'],
  ['see https://x.example/<alice@example.test>', 'see https://x.example/[MASKED_EMAIL]'],
  // Longer dotted tokens are versions or host names.
  ['build 1.2.3.4.5', 'build 1.2.3.4.5'],
  ['host 192.0.2.1a', 'host 192.0.2.1a'],
  // Colon-separated tokens that are not addresses.
  ['ratio 1:2:3:4', 'ratio 1:2:3:4'],
  ['id ab::cd::ef', 'id ab::cd::ef'],
  ['node 1:2:3:4:5:6:7:8:9', 'node 1:2:3:4:5:6:7:8:9'],
];

test('text is masked as the server masks sample exports', async (t) => {
  const { api } = await masking(t);
  for (const [text, expected] of cases) assert.equal(api.maskEventDataString(text), expected, text);
  assert.equal(api.maskEventDataString(null), null);
  assert.equal(api.maskEventDataString(undefined), undefined);
  assert.equal(api.maskEventDataString(42), '42');
});

test('an address written in full with an IPv4 tail is masked whole', async (t) => {
  const { api } = await masking(t);
  for (const address of [
    '0:0:0:0:0:ffff:192.0.2.1',
    '64:ff9b:0:0:0:0:192.0.2.1',
    '2001:db8:0:0:0:0:0:1',
    '::ffff:192.0.2.1',
    'fe80::1%eth0',
    // The longest forms: every group written out, with a zone or an IPv4 tail.
    'fe80:0000:0000:0000:0204:61ff:fe9d:f156%eth0',
    '0000:0000:0000:0000:0000:ffff:255.255.255.255',
  ]) {
    assert.equal(api.maskEventDataString(`peer ${address}.`), 'peer [MASKED_IP].', address);
  }
  // A tail that is not an IPv4 address leaves the token alone.
  for (const text of ['0:0:0:0:0:ffff:300.0.2.1', '::ffff:192.0.2']) {
    assert.equal(api.maskEventDataString(text), text);
  }
  // Seven groups and a tail make no IPv6 address; the IPv4 address in it is
  // still masked.
  assert.equal(api.maskEventDataString('1:2:3:4:5:6:7:192.0.2.1'), '1:2:3:4:5:6:7:[MASKED_IP]');
});

test('IPv6 addresses are read as Python’s ipaddress module reads them', async (t) => {
  const { api } = await masking(t);
  const masked = [
    '::',
    '1::',
    '::1.2.3.4',
    '1:2:3:4:5::1.2.3.4',
    '1:2:3:4:5:6:1.2.3.4',
    '1:2:3:4:5:6:7::',
    'a::b:c:d:e:f:1',
  ];
  for (const address of masked)
    assert.equal(api.maskEventDataString(`x ${address}`), 'x [MASKED_IP]', address);
  const kept = [
    'class Foo::Bad',
    'digest sha1:beef::1',
    'token x1::2',
    'key :::1',
    'x 1:::2',
    'x ::1.2.3',
    'x 1::2::3',
    'x 1:2:3::4:5::6:7:8',
    'x 1:2:3:4:5:6:7:8::',
    'x 01234::1',
    'x :1:2:3:4:5:6:7:8',
  ];
  for (const text of kept) assert.equal(api.maskEventDataString(text), text);
  // An IPv4 address before "::" or after six groups and "::" is masked alone.
  assert.equal(api.maskEventDataString('x 1.2.3.4::'), 'x [MASKED_IP]::');
  assert.equal(api.maskEventDataString('x 1:2:3:4:5:6::1.2.3.4'), 'x 1:2:3:4:5:6::[MASKED_IP]');
  // A zone names the interface the address belongs to and is masked with it.
  assert.equal(api.maskEventDataString('x fe80::1%eth0.5, then'), 'x [MASKED_IP], then');
});

test('a DOM capture is masked in its text, form values and descriptive attributes', async (t) => {
  const { api, document } = await masking(
    t,
    `<main id="capture">
      <p>Login from 192.0.2.1 by a@example.test.</p>
      <input id="field" value="2001:db8::1" placeholder="b@example.test">
      <textarea id="notes">Peer 198.51.100.7</textarea>
      <img id="shot" alt="c@example.test" title="203.0.113.9" aria-label="fe80::1">
      <script>const peer = '192.0.2.1';</script>
      <style>/* 192.0.2.1 */</style>
      <noscript>Shown without scripts: 192.0.2.1</noscript>
    </main>
    <p id="outside">192.0.2.1</p>`,
  );

  api.maskDomText(document.getElementById('capture'));

  assert.equal(
    document.querySelector('p').textContent,
    'Login from [MASKED_IP] by [MASKED_EMAIL].',
  );
  const field = document.getElementById('field');
  assert.equal(field.value, '[MASKED_IP]');
  assert.equal(field.getAttribute('placeholder'), '[MASKED_EMAIL]');
  assert.equal(document.getElementById('notes').value, 'Peer [MASKED_IP]');
  const shot = document.getElementById('shot');
  assert.deepEqual(
    ['alt', 'title', 'aria-label'].map((name) => shot.getAttribute(name)),
    ['[MASKED_EMAIL]', '[MASKED_IP]', '[MASKED_IP]'],
  );
  // Script, style and noscript text never appears in the capture, and nodes
  // outside the capture are untouched.
  assert.equal(document.querySelector('script').textContent, "const peer = '192.0.2.1';");
  assert.equal(document.querySelector('style').textContent, '/* 192.0.2.1 */');
  assert.equal(document.querySelector('noscript').textContent, 'Shown without scripts: 192.0.2.1');
  assert.equal(document.getElementById('outside').textContent, '192.0.2.1');
});

test('long runs of address characters are masked in bounded time', () => {
  // A killable child keeps a backtracking regression from hanging the runner.
  const module = new URL('../public/pages/evidence-export/masking.js', import.meta.url).href;
  const script = `
    const { maskEventDataString } = await import(${JSON.stringify(module)});
    const hostile = [
      ':'.repeat(65536) + 'z',
      '1:'.repeat(32768) + 'z',
      '.'.repeat(65536) + ':1',
      'a:'.repeat(32768) + '.x',
    ];
    for (const text of hostile) {
      if (maskEventDataString(' ' + text) !== ' ' + text) throw new Error('changed');
    }
    // Long runs before a bracketed address finish, and the address is masked.
    for (const text of ['Ab '.repeat(30000) + '<a@b.co>', '<'.repeat(30000) + 'a@b.co>']) {
      if (!maskEventDataString(text).endsWith('[MASKED_EMAIL]')) throw new Error('address kept');
    }
    const tail = '1:'.repeat(32768) + 'z then 2001:db8::1.';
    if (maskEventDataString(tail) !== '1:'.repeat(32768) + 'z then [MASKED_IP].') {
      throw new Error('address after a long run left unmasked');
    }
  `;
  const run = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    encoding: 'utf8',
    timeout: 5000,
  });
  assert.equal(run.error, undefined);
  assert.equal(run.status, 0, run.stderr);
});
