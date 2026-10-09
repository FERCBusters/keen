import test from 'node:test';
import assert from 'node:assert/strict';
import {page,settle} from './support/page-fixture.mjs';
async function fixture(t,options={}){
 let items=[{id:'a1',title:'<img src=x> Annual audit',framework_slug:'A',status:'open',audit_type:'internal',start_date:'2026-01-01',end_date:'2026-01-02',control_count:2,evidence_count:3}];
 return page(t,'audits',{...options,get:async u=>{
  if(u.pathname==='/api/v1/audits/users')return {items:[]};
  if(u.pathname==='/api/v1/audits'){if(options.fail)throw new Error('Offline');return {items:options.empty?[]:items};}
  if(u.pathname==='/api/v1/audits/scheduled')return {items:[]};
  if(['/api/v1/controls','/api/v1/clauses','/api/v1/isms/documents'].includes(u.pathname))return {items:[]};
  if(u.pathname==='/api/v1/graph/clause_control')return {nodes:[],links:[]};
  throw new Error('Unexpected GET '+u);
 },remove:async u=>{assert.equal(u.pathname,'/api/v1/audits/a1');items=[];return {ok:true};},
 post:async(u,b)=>{assert.equal(u.pathname,'/api/v1/audits');return {id:'new',framework_slug:b.framework_slug};}});
}
test('audit list renders scope counts and safe titles',async t=>{
 const ui=await fixture(t);assert.match(ui.el('rows').textContent,/Annual audit/);assert.equal(ui.el('rows').querySelector('img'),null);
 assert.match(ui.el('rows').querySelector('a').href,/id=a1.*framework=A/);
});
test('audit delete confirms and refreshes the list',async t=>{
 const ui=await fixture(t);ui.document.querySelector('[data-delete-audit="a1"]').click();
 await settle(()=>ui.calls.some(c=>c.method==='DELETE'));await settle(()=>!ui.el('rows').textContent.includes('Annual audit'));
 assert.equal(ui.el('status').textContent,'Audit deleted');
});
test('cancelling audit deletion sends no mutation',async t=>{
 const ui=await fixture(t,{globals:{confirm:()=>false}});ui.document.querySelector('[data-delete-audit="a1"]').click();assert.ok(!ui.calls.some(c=>c.method==='DELETE'));
});
test('audit search and status filter are sent to the backend',async t=>{
 const ui=await fixture(t);ui.el('q').value='Annual';ui.el('q').dispatchEvent(new ui.window.Event('input'));
 await settle(()=>ui.calls.filter(c=>c.url.startsWith('/api/v1/audits?')).length===2);
 assert.match(ui.calls.filter(c=>c.url.startsWith('/api/v1/audits?')).at(-1).url,/q=Annual/);
});
for(const state of ['empty','reader','error'])test('audit '+state+' state',async t=>{
 const ui=await fixture(t,{empty:state==='empty',fail:state==='error',me:state==='reader'?{can_view_audits:true}:{is_admin:true}});
 if(state==='empty')assert.match(ui.el('rows').textContent,/No audits/);
 if(state==='error')assert.match(ui.el('rows').textContent,/Offline/);
 if(state==='reader')assert.equal(ui.document.querySelector('[data-delete-audit]'),null);
});
