import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { renderJson, MAX_HIGHLIGHT_CHARS } from '../public/json-highlight.js';

test('JSON highlighting preserves hostile text without creating active markup', () => {
  const dom = new JSDOM('<pre></pre>');
  try {
    const pre = dom.window.document.querySelector('pre');
    const text = JSON.stringify(
      {
        '<img src=x onerror=alert(1)>': '</span><script>alert(1)</script>',
        n: -1.2e20,
        yes: true,
        missing: null,
        escaped: '\\"',
      },
      null,
      2,
    );
    renderJson(pre, text);
    assert.equal(pre.textContent, text);
    assert.equal(pre.querySelector('img,script'), null);
    for (const kind of ['key', 'string', 'number', 'literal'])
      assert.ok(pre.querySelector('.json-' + kind));
    assert.equal(pre.querySelector('[onclick],[onerror]'), null);
  } finally {
    dom.window.close();
  }
});
test('large or token-dense JSON falls back to exact plain text', () => {
  const dom = new JSDOM('<pre></pre>');
  try {
    const pre = dom.window.document.querySelector('pre');
    for (const text of ['x'.repeat(MAX_HIGHLIGHT_CHARS + 1), JSON.stringify(Array(5000).fill(1))]) {
      renderJson(pre, text);
      assert.equal(pre.textContent, text);
      assert.equal(pre.children.length, 0);
    }
    renderJson(pre, '{"ok":true}');
    assert.ok(pre.children.length > 0);
  } finally {
    dom.window.close();
  }
});
