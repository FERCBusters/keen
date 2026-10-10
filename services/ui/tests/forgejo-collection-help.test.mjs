import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadUi} from './support/load-ui.mjs';
import {settle} from './support/page-fixture.mjs';

test('Defined Forgejo source saves its first user input', async t => {
  const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
  let saved;
  const ui = await loadUi('pages/connection-hub.js', {html, globals:{structuredClone}, setup(window) {
    window.HTMLDialogElement.prototype.showModal = function() {this.open = true;};
    window.HTMLDialogElement.prototype.close = function() {this.open = false;};
  }, vendor:{
    apiGet: async url => {
      if (url.endsWith('/source-connections')) return {connections:[{id:'forge',source:'forgejo',name:'My Forgejo',enabled:true,configuration:{base_url:'https://git.example'},inputs:{users:[],organizations:[],feeds:[]},version:1,managed_by:'database'}],types:[{source:'forgejo',enabled:true,fields:['base_url','username','auth_mode'],credential_fields:['token','cookie']}]};
      if (url.endsWith('/evidence-definitions')) return {collectors:[], rules:[{id:'any', when:{source:'forgejo'}}]};
      if (url.endsWith('/integrations')) return {connections:[], collectors:[], demo_mode:false};
      if (url.endsWith('/managed-configurations')) return {items:[{name:'forgejo'}]};
      if (url.endsWith('/managed-configurations/forgejo')) return {version:1, document:{users:[], organizations:[], feeds:[]}};
      throw new Error('Unexpected URL '+url);
    },
    apiPut: async (url, body) => {saved = {url, body};},
  }});
  t.after(ui.close);
  await settle(() => ui.document.getElementById('hub-list').textContent.includes('Add input'));
  ui.document.getElementById('hub-start-setup').click();
  assert.match(ui.document.querySelector('dialog[open]').textContent,/Define a source/);
  ui.document.querySelector('dialog[open]').close();
  ui.window.dispatchEvent(new ui.window.CustomEvent('keen-manage-source', {detail:{source:'forgejo'}}));
  assert.equal(ui.document.getElementById('hub-search').value, 'forgejo');
  assert.equal(ui.document.getElementById('evidence-management-view').hidden, false);
  const list = ui.document.getElementById('hub-list');
  assert.match(list.textContent, /Add an input/);
  [...list.querySelectorAll('button')].find(b => b.textContent === 'Add input').click();
  await settle(() => ui.document.getElementById('hub-collection').open);
  const type = ui.document.getElementById('hub-collection-type');
  type.value = 'users'; type.dispatchEvent(new ui.window.Event('change'));
  ui.document.querySelector('[data-field="user"]').value = 'alice';
  await ui.document.getElementById('hub-collection-save').onclick();
  // The connection-change notification starts a final asynchronous refresh.
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(saved.url, '/api/v1/admin/source-connections/forge');
  assert.match(ui.document.getElementById('hub-status').textContent,/Input saved for My Forgejo/);
  assert.ok([...ui.document.querySelectorAll('#hub-status button')].some(b=>b.textContent==='Collect now'));
  assert.ok([...ui.document.querySelectorAll('#hub-status button')].some(b=>b.textContent==='Add mapping rule'));
  assert.equal(saved.body.version,1);
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.inputs)), {users:[{user:'alice'}], organizations:[], feeds:[]});
});
