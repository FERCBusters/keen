import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadUi} from './support/load-ui.mjs';
const html=await readFile(new URL('../public/admin.html',import.meta.url),'utf8');
async function fixture(t,inputs=[]){
 const ui=await loadUi('pages/connection-hub.js',{html,globals:{structuredClone},vendor:{apiGet:async url=>{
  if(url.endsWith('/evidence-definitions'))return {collectors:inputs,rules:[]};
  if(url.endsWith('/integrations'))return {connections:[],collectors:[]};
  if(url.endsWith('/managed-configurations'))return {items:[{name:'forgejo'},{name:'loki'}]};
  if(url.endsWith('/source-connections'))return {types:[{source:'forgejo',enabled:true},{source:'loki',enabled:false}],connections:[{id:'one',source:'forgejo',name:'My Forgejo',enabled:true,configuration:{base_url:'https://git.example'},inputs:{},managed_by:'database'},{id:'disabled',source:'loki',name:'Hidden logs',configuration:{},inputs:{},managed_by:'database'}]};
  throw Error(url);
 }}});
 t.after(ui.close);
 for(let i=0;i<50&&!ui.document.getElementById('hub-list').textContent;i++)await new Promise(r=>setTimeout(r,5));
 return ui;
}
test('sources start with named enabled configurations and require inputs before mapping',async t=>{
 const {document}=await fixture(t);
 assert.equal(document.getElementById('hub-connection'),null);
 assert.equal(document.getElementById('evidence-management-view').hidden,false);
 const list=document.getElementById('hub-list');
 assert.match(list.textContent,/My Forgejo/);
 assert.doesNotMatch(list.textContent,/Environment default|Hidden logs|Add mapping rule/);
 assert.ok([...list.querySelectorAll('button')].some(b=>b.textContent==='Add input'));
});
test('mapping from an input carries its source identity and input',async t=>{
 const input={adapter:'forgejo',connection_id:'one',section:'users',key:'alice',collector:'collector-alice',entry:{user:'alice'}};
 const {document,window}=await fixture(t,[input]);let detail;
 window.addEventListener('keen-collection-create',event=>detail=event.detail);
 [...document.querySelectorAll('#hub-list button')].find(b=>b.textContent==='Add mapping rule').click();
 assert.equal(detail.source,'forgejo');assert.equal(detail.connection_id,'one');assert.equal(detail.input.collector,'collector-alice');
 assert.equal(document.getElementById('evidence-mapping-view').hidden,false);
});
