// A page shows a failed request through the design system's errorText, which
// reads the API's JSON detail, a validation list or a network failure as a
// sentence. A page that reads the error itself shows raw JSON, or
// "[object Object]" for a validation list, when the API's reply changes shape.
import test from 'node:test';
import assert from 'node:assert/strict';
import { firstPartyScripts } from './support/first-party-scripts.mjs';

// A caught error's message shown as it is, and a detail decoded by hand.
const RAW_MESSAGE = /\b(?:e|err|error|failure)\.message\b/;
const HAND_DECODED = /JSON\.parse\([^)]*\)\s*\??\.detail|\.msg\b/;

// Pages whose failures are never a reply from Keen's API helpers.
const OWN_MESSAGES = {
  // Sign-in and second-factor ceremonies call fetch themselves, before a
  // session exists, and throw sentences of their own.
  'pages/account-security.js': 2,
  'pages/mfa.js': 2,
  // The Mitigator's form check throws its own sentence before any request.
  'pages/mitigator.js': 1,
};

const count = (source, pattern) => source.split('\n').filter((line) => pattern.test(line)).length;

test('pages read failed requests through errorText', async () => {
  const scripts = await firstPartyScripts();
  assert.ok(scripts.length > 100, `expected the first-party scripts, found ${scripts.length}`);
  const raw = Object.fromEntries(
    scripts
      .map(({ path, source }) => [path, count(source, RAW_MESSAGE)])
      .filter(([, found]) => found),
  );
  assert.deepEqual(raw, OWN_MESSAGES);
  assert.deepEqual(
    scripts.filter(({ source }) => HAND_DECODED.test(source)).map((script) => script.path),
    [],
  );
});

test('the check finds a page that reads an error itself', () => {
  const sample = [
    'loadRules().catch((error) => status(error.message));',
    'const detail = JSON.parse(text).detail;',
    "text = d.map((x) => x.msg).join('; ');",
    'loadRules().catch((error) => status(errorText(error)));',
    'card.append(paragraph(item.message));',
  ];
  assert.deepEqual(
    sample.map((line) => [RAW_MESSAGE.test(line), HAND_DECODED.test(line)]),
    [
      [true, false],
      [false, true],
      [false, true],
      [false, false],
      [false, false],
    ],
  );
});
