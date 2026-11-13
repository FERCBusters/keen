import test from 'node:test';
import assert from 'node:assert/strict';
import { csvEscape } from '../public/csv.js';

test('spreadsheet exports quote hostile text as literal cells', () => {
  for (const text of ['=1+1', '+1+1', '-1+1', '@SUM(1)', '  =1']) {
    assert.equal(csvEscape(text), "'" + text);
  }
  // A reader that splits on semicolons or tabs starts a cell after each one
  // and after a line break, whatever the file's delimiter and quoting.
  assert.equal(csvEscape('\t=1'), "'\t'=1");
  assert.equal(csvEscape('\n=1'), '"\'\n\'=1"');
  assert.equal(csvEscape('x;=1+1;y'), "x;'=1+1;y");
  assert.equal(csvEscape('a\t @SUM(1)'), "a\t '@SUM(1)");
  assert.equal(csvEscape('x\n=1+1;y'), '"x\n\'=1+1;y"');
  assert.equal(csvEscape('a; b\nline = one'), '"a; b\nline = one"');
  assert.equal(
    csvEscape('=HYPERLINK("https://example.invalid","x")'),
    '"\'=HYPERLINK(""https://example.invalid"",""x"")"',
  );
  assert.equal(csvEscape(-2), '-2');
  assert.equal(csvEscape('ordinary'), 'ordinary');
  assert.equal(csvEscape('a,b'), '"a,b"');
});
