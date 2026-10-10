import test from 'node:test';
import assert from 'node:assert/strict';
import {csvEscape} from '../public/csv.js';

test('spreadsheet exports quote hostile text as literal cells', () => {
  for (const text of ['=1+1', '+1+1', '-1+1', '@SUM(1)', '  =1', '\t=1']) {
    assert.equal(csvEscape(text), "'" + text);
  }
  assert.equal(csvEscape('\n=1'), '"\'\n=1"');
  assert.equal(csvEscape('=HYPERLINK("https://example.invalid","x")'), '"\'=HYPERLINK(""https://example.invalid"",""x"")"');
  assert.equal(csvEscape(-2), '-2');
  assert.equal(csvEscape('ordinary'), 'ordinary');
  assert.equal(csvEscape('a,b'), '"a,b"');
});
