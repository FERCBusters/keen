import test from 'node:test';
import assert from 'node:assert/strict';
import {loadUi} from './support/load-ui.mjs';

test('connection editor separates endpoint and secrets and preserves inputs on edit', async t => {
  let saved;
  const ui = await loadUi('pages/source-connection-editor.js', {setup(window) {
    window.HTMLDialogElement.prototype.showModal = function() {this.open = true;};
    window.HTMLDialogElement.prototype.close = function() {this.open = false;};
  }, vendor: {apiPut: async (url, body) => {saved = {url, body};return {...body,id:'one'};}}});
  t.after(ui.close);
  const connection={id:'one',source:'loki',name:'Production',enabled:true,configuration:{base_url:'https://logs.example'},inputs:{queries:[{name:'auth',logql:'{app="auth"}'}]},version:3};
  const dialog=ui.api.editSourceConnection({types:[{source:'loki',enabled:true,fields:['base_url','username'],credential_fields:['password']}],connection,onSaved:async()=>{}});
  assert.equal(dialog.querySelector('[data-key="password"]').value,'');
  dialog.querySelector('[data-key="base_url"]').value='https://other.example';
  dialog.querySelector('[data-key="password"]').value='new-secret';
  await dialog.querySelector('form').onsubmit({preventDefault(){}});
  assert.equal(saved.url,'/api/v1/admin/source-connections/one');
  assert.equal(saved.body.configuration.base_url,'https://other.example');
  assert.equal(saved.body.credentials.password,'new-secret');
  assert.equal(saved.body.inputs.queries[0].name,'auth');
  assert.equal(saved.body.version,3);
});

const rssType={source:'rss',enabled:true,fields:['base_url','username','header_name'],credential_fields:['password','header_value']};
test('RSS source saves one feed and credentials together with an origin restriction',async t=>{
 let saved;
 const ui=await loadUi('pages/source-connection-editor.js',{setup(w){w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};},vendor:{apiPost:async(url,body)=>{saved=body;return {...body,id:'rss-one'};}}});
 t.after(ui.close);
 const dialog=ui.api.editSourceConnection({types:[rssType],onSaved:async()=>{}});
 assert.equal(dialog.querySelector('[data-key="base_url"]'),null);
 dialog.querySelector('[data-feed-url]').value='https://news.example:8443/private/feed.xml?format=rss';
 dialog.querySelector('[data-key="password"]').value='secret';
 await dialog.querySelector('form').onsubmit({preventDefault(){}});
 assert.equal(saved.configuration.base_url,'https://news.example:8443');
 assert.equal(saved.inputs.feeds.length,1);
 assert.equal(saved.inputs.feeds[0].url,'https://news.example:8443/private/feed.xml?format=rss');
 assert.equal(saved.credentials.password,'secret');
});

test('RSS edit preserves feed options and rejects credentials embedded in URL',async t=>{
 let saved;
 const ui=await loadUi('pages/source-connection-editor.js',{setup(w){w.HTMLDialogElement.prototype.showModal=function(){};w.HTMLDialogElement.prototype.close=function(){};},vendor:{apiPut:async(url,body)=>{saved=body;return body;}}});t.after(ui.close);
 const connection={id:'rss',source:'rss',name:'News',configuration:{base_url:'https://news.example'},inputs:{feeds:[{url:'https://news.example/rss',label:'Security',max_items:12}]},version:2};
 const dialog=ui.api.editSourceConnection({types:[rssType],connection,onSaved:async()=>{}});
 dialog.querySelector('[data-feed-url]').value='https://user:secret@news.example/rss';
 await dialog.querySelector('form').onsubmit({preventDefault(){}});assert.equal(saved,undefined);
 dialog.querySelector('[data-feed-url]').value='https://other.example/rss';
 await dialog.querySelector('form').onsubmit({preventDefault(){}});
 assert.equal(saved.inputs.feeds[0].max_items,12);assert.equal(saved.inputs.feeds[0].label,'Security');
 assert.equal(saved.configuration.base_url,'https://other.example');
 assert.equal(saved.credentials.password,'');
});

test('existing multi-feed source retains all inputs',async t=>{
 let saved;
 const ui=await loadUi('pages/source-connection-editor.js',{setup(w){w.HTMLDialogElement.prototype.showModal=function(){};w.HTMLDialogElement.prototype.close=function(){};},vendor:{apiPut:async(url,body)=>{saved=body;return body;}}});t.after(ui.close);
 const connection={id:'rss',source:'rss',name:'News',configuration:{base_url:'https://news.example'},inputs:{feeds:[{url:'https://news.example/a'},{url:'https://news.example/b'}]},version:2};
 const dialog=ui.api.editSourceConnection({types:[rssType],connection,onSaved:async()=>{}});
 assert.equal(dialog.querySelector('[data-feed-url]'),null);
 await dialog.querySelector('form').onsubmit({preventDefault(){}});assert.equal(saved.inputs.feeds.length,2);
});
