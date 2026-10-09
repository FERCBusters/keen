import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadUi} from './support/load-ui.mjs';
import {settle} from './support/page-fixture.mjs';
const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const plan = {kind:'collector', name:'Test ingester', collections:1, definitions:[{id:'rule', name:'Test rule'}], automatic_mappings:2, collectors:[{id:'collector',name:'Test ingester'}], fingerprint:'preview-version'};
function setup(window) {
  window.HTMLDialogElement.prototype.showModal = function() {this.open=true;};
  window.HTMLDialogElement.prototype.close = function() {this.open=false;};
}
for (const kind of ['collector','api']) test(`builder can delete ${kind} through a reviewed impact preview`, async t => {
  let removed=false, posts=[];
  const ui=await loadUi('pages/integrations.js',{html, setup, globals:{setInterval:()=>0}, vendor:{
    apiGet:async url=> {
      if(url==='/api/v1/me')return {is_admin:true};
      if(url.endsWith('/removal'))return {...plan,kind};
      if(url==='/api/v1/admin/integrations')return {connections:removed&&kind==='api'?[]:[{id:'connection',name:'Test connection',base_url:'https://example.org',auth_kind:'none'}], collectors:removed?[]:[{id:'collector',name:'Test ingester',connection_id:'connection',version:1}], runs:[],templates:[],measures:[]};
      throw Error(url);
    },
    apiPost:async (url, body)=> {posts.push({url,body});removed=true;},
  }});
  t.after(ui.close);
  const label=kind==='collector'?'Delete ingester':'Delete connection';
  [...ui.document.querySelectorAll('button')].find(b=>b.textContent===label).click();
  await settle(()=>ui.document.getElementById('hub-confirm').open);
  assert.match(ui.document.getElementById('hub-impact').textContent,/Test ingester.*Test rule/s);
  const input=ui.document.getElementById('hub-typed-name'), button=ui.document.getElementById('hub-delete');
  assert.equal(button.disabled,true);
  input.value='wrong';input.dispatchEvent(new ui.window.Event('input'));assert.equal(button.disabled,true);
  input.value=plan.name;input.dispatchEvent(new ui.window.Event('input'));assert.equal(button.disabled,false);
  await button.onclick();
  await settle(()=>!ui.document.getElementById('ig-list').textContent.includes('Test ingester'));
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,`/api/v1/admin/evidence-connections/${encodeURIComponent(kind+':'+(kind==='collector'?'collector':'connection'))}/remove`);
  assert.equal(posts[0].body.fingerprint,plan.fingerprint);
  assert.equal(ui.document.getElementById('hub-confirm').open,false);
});

test('cancel, blocked runs and stale previews keep the item intact',async t=>{
  let posts=0, blocked=false, failure;
  const ui=await loadUi('pages/evidence-removal.js',{html,setup,vendor:{apiGet:async()=>({...plan,blocked:blocked?'Run active':null}),apiPost:async()=>{posts++;throw Error('Preview changed');}}});
  t.after(ui.close);
  const options={onRemoved:()=>assert.fail('Must retain item'),onError:e=>failure=e};
  await ui.api.confirmEvidenceRemoval({key:'collector:c'},options);
  ui.document.getElementById('hub-cancel').click();
  assert.equal(posts,0);assert.equal(ui.document.getElementById('hub-confirm').open,false);
  blocked=true;
  await assert.rejects(ui.api.confirmEvidenceRemoval({key:'collector:c'},options),/Run active/);
  blocked=false;
  await ui.api.confirmEvidenceRemoval({key:'collector:c'},options);
  await ui.document.getElementById('hub-delete').onclick();
  assert.match(failure.message,/Preview changed/);
  assert.equal(ui.document.getElementById('hub-confirm').open,false);
});
