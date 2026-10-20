import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
const ui = await loadUi('app.js');
const a = ui.api;
test.after(() => ui.close());
for (const payload of [
  '<p onclick="alert(1)">text</p>',
  '<img src=x onerror=alert(1)>',
  '<svg onload="alert(1)"><a href="javascript:alert(1)">click</a></svg>',
  '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
  '<p style="background:url(javascript:alert(1))">text</p>',
  '<a href="jav&#x61;script:alert(1)">click</a>',
  '<a href="data:text/html,evil">click</a>',
  '<a href="vbscript:evil">click</a>',
])
  test('sanitises ' + payload, () => {
    const result = a.sanitizeRichTextHtml(payload);
    ui.document.body.innerHTML = result;
    assert.equal(
      ui.document.querySelector('img,svg,iframe,script,[onclick],[onerror],[onload],[style]'),
      null,
    );
    for (const link of ui.document.querySelectorAll('a[href]'))
      assert.doesNotMatch(link.href, /^(javascript|data|vbscript):/);
  });
for (const tag of ['p', 'strong', 'em', 'u', 'blockquote', 'h2', 'pre', 'code', 'ul', 'ol', 'li'])
  test('preserves formatting ' + tag, () => {
    assert.equal(
      a.sanitizeRichTextHtml(`<${tag}>hello &amp; goodbye</${tag}>`),
      `<${tag}>hello &amp; goodbye</${tag}>`,
    );
  });
test('external links isolate opener and strip arbitrary attributes', () => {
  ui.document.body.innerHTML = a.sanitizeRichTextHtml(
    '<a href="https://example.test" target="_self" class="custom">Example</a>',
  );
  const link = ui.document.querySelector('a');
  assert.equal(link.target, '_blank');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(link.className, '');
});
test('plain text is shown as escaped paragraphs; Markdown is not interpreted', () => {
  const output = a.plainTextToRichTextHtml(
    '# Heading\r\nSecond line\n\n- **One**\n- `Two`\n\n\n<img src=x>',
  );
  assert.equal(
    output,
    '<p># Heading<br>Second line</p>\n<p>- **One**<br>- `Two`</p>\n<p>&lt;img src=x&gt;</p>',
  );
  ui.document.body.innerHTML = output;
  assert.equal(ui.document.querySelector('h1,li,strong,code,img'), null);
  assert.equal(a.plainTextToRichTextHtml('  \n ', 'Nothing'), 'Nothing');
  assert.equal('legacyRichTextMarkdownToHtml' in a, false);
});
test('plain text summaries decode entities and remove formatting', () => {
  assert.equal(
    a.plainTextFromRichText('<p>Hello &amp; <strong>world</strong></p><p>Next&nbsp;line</p>'),
    'Hello & world Next line',
  );
});
test('plain text summaries decode the ampersand entity last', () => {
  assert.equal(
    a.plainTextFromRichText('<p>a &amp;lt; b &amp;quot; c &amp;amp; d</p>'),
    'a &lt; b &quot; c &amp; d',
  );
});
test('editor values sanitise HTML-like input and wrap plain text', () => {
  const hostile = a.richTextEditorValue(
    '<p onclick="alert(1)">Reviewed</p><img src=x onerror=alert(1)>',
  );
  ui.document.body.innerHTML = hostile;
  assert.equal(ui.document.querySelector('img,[onclick],[onerror]'), null);
  assert.equal(ui.document.body.textContent, 'Reviewed');
  assert.equal(a.richTextEditorValue('**Reviewed**'), '<p>**Reviewed**</p>');
  assert.equal(a.renderRichTextContent('a < b\nc'), '<p>a &lt; b<br>c</p>');
});
test('empty content uses caller fallback', () =>
  assert.equal(a.renderRichTextContent(' ', 'Empty'), 'Empty'));
for (const href of ['//evil.example', '/\\evil.example', '/\t/evil.example'])
  test('rich text rejects ambiguous links ' + JSON.stringify(href), () => {
    ui.document.body.innerHTML = a.sanitizeRichTextHtml(`<a href="${href}">link</a>`);
    assert.equal(ui.document.querySelector('a').hasAttribute('href'), false);
  });
