import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadUi} from './support/load-ui.mjs';
const html=await readFile(new URL('../public/admin.html',import.meta.url),'utf8');
const id='11111111-1111-4111-8111-111111111111';
const event={id,source:'keen-agent',action:'ossec.alert',system:'server',outcome:'info',severity:3,
 normalized_payload:{source:'ossec',fields:{parser:'ossec','ossec.rule.id':'31101','log.file.path':'/var/ossec/alerts.json'}}};
const sample={id:'other',source:'keen-agent',action:'syslog.record',summary:'Journal entry',fields:[{path:['fields','service.name'],value:'sshd'}]};
async function until(check){for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setTimeout(r,5));}assert.fail('Editor did not reach expected state');}
async function fixture(t,{fail=false}={}){
 const calls=[];
 async function apiGet(raw){
  calls.push(raw);const url=new URL(raw,'https://keen.example');
  if(fail)throw new Error('<img src=x onerror=alert(1)>');
  if(url.pathname==='/api/v1/admin/evidence-event-samples')return {sources:['keen-agent'],items:[sample]};
  if(url.pathname==='/api/v1/admin/source-catalogue')return {items:[{adapter:'keen-agent',enabled:true}]};
  if(url.pathname==='/api/v1/admin/integrations')return {collectors:[]};
  if(url.pathname==='/api/v1/admin/evidence-definitions')return {rules:[],collectors:[],rules_version:1};
  if(url.pathname.startsWith('/api/v1/admin/adapter-settings/'))return {settings:{},version:1};
  if(url.pathname==='/api/v1/frameworks')return {items:[{slug:'A',name:'Framework A'}]};
  if(url.pathname==='/api/v1/controls')return {items:[{ref:'A1',title:'Control A'}]};
  if(url.pathname==='/api/v1/events/'+id)return event;
  throw new Error('Unexpected GET '+raw);
 }
 const ui=await loadUi('pages/evidence-config.js',{html,url:'https://keen.example/admin.html?from_event='+id,vendor:{apiGet}});
 t.after(()=>ui.close());
 await until(()=>fail?ui.document.getElementById('ec-status').textContent.includes('onerror'):!ui.document.getElementById('ec-draft-notice').hidden);
 return {...ui,calls};
}
test('draft from an OSSEC event exposes its fields before other samples',async t=>{
 const {document,window}=await fixture(t);
 document.getElementById('ec-field-add').click();
 const row=document.getElementById('ec-field-rows').lastElementChild;
 const select=row.querySelector('select');
 assert.equal(select.querySelector('optgroup').label,'Fields in this event');
 const option=[...select.options].find(o=>o.value==='["fields","ossec.rule.id"]');
 assert.equal(option.parentElement.label,'Fields in this event');
 assert.equal([...select.options].find(o=>o.value==='["fields","service.name"]').parentElement.label,'Fields seen in other samples');
 select.value=option.value;select.dispatchEvent(new window.Event('change'));
 assert.equal(row.querySelector('input').value,'31101');
 assert.ok(!window.location.search.includes('from_event'));
});
test('choosing a sample regroups existing fields without changing the rule',async t=>{
 const {document,window}=await fixture(t);
 document.getElementById('ec-field-add').click();
 const row=document.getElementById('ec-field-rows').lastElementChild,select=row.querySelector('select');
 select.value='["fields","ossec.rule.id"]';select.dispatchEvent(new window.Event('change'));
 const samples=document.getElementById('ec-sample');samples.value='0';samples.dispatchEvent(new window.Event('change'));
 assert.equal(select.value,'["fields","ossec.rule.id"]');assert.equal(row.querySelector('input').value,'31101');
 assert.equal(select.selectedOptions[0].parentElement.label,'Fields seen in other samples');
 assert.equal(select.querySelector('optgroup').label,'Fields in this event');
 assert.equal(select.querySelector('optgroup option').value,'["fields","service.name"]');
});
test('starting a new rule clears draft-only observations and conditions',async t=>{
 const {document}=await fixture(t);
 document.getElementById('ec-new-rule').click();
 await until(()=>document.getElementById('ec-draft-notice').hidden);
 await new Promise(r=>setTimeout(r,10));
 document.getElementById('ec-field-add').click();
 const rows=document.getElementById('ec-field-rows');assert.equal(rows.children.length,1);
 assert.ok(![...rows.querySelector('select').options].some(o=>o.value==='["fields","ossec.rule.id"]'));
});
test('initialisation errors are exposed accessibly without interpreting markup',async t=>{
 const {document}=await fixture(t,{fail:true});
 const status=document.getElementById('ec-status');assert.equal(status.getAttribute('role'),'alert');
 assert.equal(status.hidden,false);assert.equal(status.querySelector('img'),null);assert.match(status.textContent,/<img/);
});

test('source-wide rules explain collection scope and offer a setup shortcut', async t => {
 const {document,window}=await fixture(t);
 const select=document.getElementById('ec-definition-adapter');
 select.add(new window.Option('Forgejo','forgejo'));select.value='forgejo';
 // Select the source-wide scope without requesting a new adapter configuration.
 document.getElementById('ec-definition-section').value='source';
 document.getElementById('ec-definition-section').dispatchEvent(new window.Event('change'));
 const help=document.getElementById('ec-source-scope-help');
 assert.equal(help.hidden,false);
 assert.match(help.textContent,/does not discover or start collecting/);
 assert.match(help.textContent,/No collections are configured for forgejo/);
 let requested;
 window.addEventListener('keen-manage-source',event=>{requested=event.detail.source;});
 document.getElementById('ec-manage-collections').click();
 assert.equal(requested,'forgejo');
 await new Promise(resolve=>setTimeout(resolve,10));
});

test('Next never adds the dropdown control or restores a removed target', async t => {
 const {document,window}=await fixture(t);
 const control=document.getElementById('ec-control');
 assert.equal(control.value,'');
 document.getElementById('ec-next-step').click();
 document.getElementById('ec-description').value='Selected evidence';
 control.add(new window.Option('A.5.1 — Policies','A.5.1'));
 control.value='A.5.1';
 document.getElementById('ec-next-step').click();
 assert.equal(document.getElementById('ec-step-number').textContent,'2');
 assert.equal(document.getElementById('ec-targets').children.length,0);
 control.value='A1';document.getElementById('ec-add-target').click();
 control.value='A.5.1';document.getElementById('ec-add-target').click();
 const targets=document.getElementById('ec-targets');
 [...targets.children].find(row=>row.textContent.includes('A.5.1')).querySelector('button').click();
 document.getElementById('ec-next-step').click();
 assert.equal(document.getElementById('ec-step-number').textContent,'3');
 assert.equal(targets.children.length,1);
 assert.match(targets.textContent,/A1/);
 assert.doesNotMatch(targets.textContent,/A\.5\.1/);
 document.getElementById('ec-prev-step').click();
 document.getElementById('ec-next-step').click();
 assert.equal(targets.children.length,1);
});
