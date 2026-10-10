import test from 'node:test';
import assert from 'node:assert/strict';
import {loadUi} from './support/load-ui.mjs';
const ui=await loadUi('app.js');const a=ui.api;test.after(()=>ui.close());
for(const payload of [
 '<p onclick="alert(1)">text</p>','<img src=x onerror=alert(1)>',
 '<svg onload="alert(1)"><a href="javascript:alert(1)">click</a></svg>',
 '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
 '<p style="background:url(javascript:alert(1))">text</p>',
 '<a href="jav&#x61;script:alert(1)">click</a>',
 '<a href="data:text/html,evil">click</a>',
 '<a href="vbscript:evil">click</a>',
])test('sanitises '+payload,()=>{
 const result=a.sanitizeRichTextHtml(payload);ui.document.body.innerHTML=result;
 assert.equal(ui.document.querySelector('img,svg,iframe,script,[onclick],[onerror],[onload],[style]'),null);
 for(const link of ui.document.querySelectorAll('a[href]'))assert.doesNotMatch(link.href,/^(javascript|data|vbscript):/);
});
for(const tag of ['p','strong','em','u','blockquote','h2','pre','code','ul','ol','li'])test('preserves formatting '+tag,()=>{
 assert.equal(a.sanitizeRichTextHtml(`<${tag}>hello &amp; goodbye</${tag}>`),`<${tag}>hello &amp; goodbye</${tag}>`);
});
test('external links isolate opener and strip arbitrary attributes',()=>{
 ui.document.body.innerHTML=a.sanitizeRichTextHtml('<a href="https://example.test" target="_self" class="custom">Example</a>');
 const link=ui.document.querySelector('a');assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');assert.equal(link.className,'');
});
test('legacy Markdown supports headings lists and inline formatting while escaping HTML',()=>{
 const output=a.legacyRichTextMarkdownToHtml('# Heading\n\n- **One**\n- `Two`\n\n<img src=x>');
 ui.document.body.innerHTML=output;
 assert.equal(ui.document.querySelector('h1').textContent,'Heading');assert.equal(ui.document.querySelectorAll('li').length,2);
 assert.equal(ui.document.querySelector('strong').textContent,'One');assert.equal(ui.document.querySelector('img'),null);
});
test('plain text summaries decode entities and remove formatting',()=>{
 assert.equal(a.plainTextFromRichText('<p>Hello &amp; <strong>world</strong></p><p>Next&nbsp;line</p>'),'Hello & world Next line');
});
test('empty content uses caller fallback',()=>assert.equal(a.renderRichTextContent(' ','Empty'),'Empty'));
for (const href of ['//evil.example', '/\\evil.example', '/\t/evil.example']) test('rich text rejects ambiguous links '+JSON.stringify(href),()=>{
 ui.document.body.innerHTML=a.sanitizeRichTextHtml(`<a href="${href}">link</a>`);
 assert.equal(ui.document.querySelector('a').hasAttribute('href'),false);
});
