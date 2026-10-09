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
