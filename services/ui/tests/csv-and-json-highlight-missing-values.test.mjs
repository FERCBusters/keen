// Values that are absent: a CSV row without a column another row has, and an
// event without a normalised payload, which JSON.stringify turns into undefined.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { csvEscape } from '../public/csv.js';
import { renderJson } from '../public/json-highlight.js';

test('a missing spreadsheet value is an empty cell', () => {
  assert.equal(csvEscape(undefined), '');
  assert.equal(csvEscape(null), '');
  // Present values that look empty keep their text.
  assert.equal(csvEscape(0), '0');
  assert.equal(csvEscape(false), 'false');
});

test('a missing JSON document clears the view', () => {
  const dom = new JSDOM('<pre></pre>');
  try {
    const pre = dom.window.document.querySelector('pre');
    for (const missing of [JSON.stringify(undefined), null]) {
      renderJson(pre, '{"earlier": true}');
      assert.equal(pre.children.length, 2);
      renderJson(pre, missing);
      assert.equal(pre.textContent, '');
      assert.equal(pre.children.length, 0);
    }
  } finally {
    dom.window.close();
  }
});
