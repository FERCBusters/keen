// Every first-party script is measured whole. tests-ui.sh requires full
// statement, branch and function coverage, and an ignore hint would meet that
// without a test.
import test from 'node:test';
import assert from 'node:assert/strict';
import { firstPartyScripts } from './support/first-party-scripts.mjs';

const IGNORE_HINT = /\b(?:c8|v8|istanbul)\s+ignore\b|node:coverage\s+(?:ignore|disable)\b/;

test('no first-party script excludes code from coverage', async () => {
  const scripts = await firstPartyScripts();
  assert.ok(scripts.length > 100, `expected the first-party scripts, found ${scripts.length}`);
  assert.deepEqual(
    scripts.filter(({ source }) => IGNORE_HINT.test(source)).map((script) => script.path),
    [],
  );
  // The check finds each hint c8 and Node honour.
  for (const hint of [
    '/* c8 ignore next */',
    '/* c8 ignore start */',
    '/* v8 ignore next 3 */',
    '/* istanbul ignore else */',
    '/* node:coverage ignore next */',
    '/* node:coverage disable */',
  ])
    assert.match(hint, IGNORE_HINT, hint);
  assert.doesNotMatch('// Ignore the c8 report directory.', IGNORE_HINT);
});
