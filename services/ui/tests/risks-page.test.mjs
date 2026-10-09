import test from 'node:test';
import assert from 'node:assert/strict';
import {page,settle} from './support/page-fixture.mjs';
const low={id:'r1',asset:'Database',threat_summary:'Exposure',risk_types:['Confidentiality'],risk_score:5,residual_risk_score:2,control_count:1,category:{id:'cat',name:'Information'},subcategory:{id:'sub',name:'Records'},risk_owner:{username:'alice'}};
const high={...low,id:'r2',asset:'<img src=x>',risk_score:20,control_count:3};
async function fixture(t,options={}){
 let items=options.empty?[]:[low,high];
 return page(t,'risks',{...options,get:async u=>{
  if(u.pathname==='/api/v1/risks/categories')return {items:[{id:'cat',name:'Information',subcategories:[{id:'sub',name:'Records'}]}]};
  if(u.pathname==='/api/v1/risks/assets')return {items:[{id:'asset1',name:'Database',category:low.category,subcategory:low.subcategory}]};
  if(u.pathname==='/api/v1/risks/users')return {items:[{id:'user1',username:'alice'}],roles:[]};
  if(u.pathname==='/api/v1/controls')return {items:[{id:'c1',ref:'A1',title:'Control'}]};
  if(u.pathname==='/api/v1/risks')return {items,total:items.length,framework:'A'};
  if(u.pathname==='/api/v1/risks/r1')return {...low,asset_id:'asset1',controls:[],register_likelihood:1,register_impact:5,register_residual_likelihood:1,register_residual_impact:2};
  if(u.pathname==='/api/v1/risks/controls-without-risks')return {items:[{id:'c2',ref:'A2',title:'Uncovered',in_scope:false}],total:1};
  throw new Error('Unexpected GET '+u);
 },patch:async(u,b)=>{assert.equal(u.pathname,'/api/v1/risks/r1');items=items.map(r=>r.id==='r1'?{...r,...b}:r);return items[0];}});
}
test('risk register sorts highest score first and escapes asset names',async t=>{
 const ui=await fixture(t);
 assert.match(ui.el('rows').querySelector('tr').textContent,/<img src=x>/);
 assert.equal(ui.el('rows').querySelector('img'),null);
 assert.match(ui.el('resultMeta').textContent,/2 risk scenarios/);
 assert.match(ui.el('rows').textContent,/1 control/);
});
test('risk editor loads a record and sends changed values to its own endpoint',async t=>{
 const ui=await fixture(t);ui.document.querySelector('[data-edit-risk="r1"]').click();
 await settle(()=>ui.el('riskId').value==='r1');
 ui.el('threatSummary').value='Changed threat';
 ui.el('riskForm').dispatchEvent(new ui.window.Event('submit',{cancelable:true}));
 await settle(()=>ui.calls.some(c=>c.method==='PATCH'));
 assert.equal(ui.calls.find(c=>c.method==='PATCH').body.threat_summary,'Changed threat');
 await settle(()=>!ui.el('saveRisk').disabled);
});
test('new risk clears the existing editor identity',async t=>{
 const ui=await fixture(t);ui.document.querySelector('[data-edit-risk="r1"]').click();
 await settle(()=>ui.el('riskId').value==='r1');ui.el('newRisk').click();
 assert.equal(ui.el('riskId').value,'');assert.equal(ui.el('threatSummary').value,'');
});
test('read-only risk users cannot see editing actions',async t=>{
 const ui=await fixture(t,{me:{can_view_risks:true}});
 assert.equal(ui.document.querySelector('[data-edit-risk]'),null);assert.equal(ui.el('newRisk').style.display,'none');
});
test('uncovered controls tab shows scope and framework-specific links',async t=>{
 const ui=await fixture(t,{query:'&tab=controls-without-risks'});
 assert.match(ui.el('controlsWithoutRisksRows').textContent,/A2.*Uncovered.*Out of scope/s);
 assert.match(ui.el('controlsWithoutRisksRows').querySelector('a').href,/framework=A/);
});
test('empty risk register gives a clear empty state',async t=>{
 const ui=await fixture(t,{empty:true});assert.match(ui.el('rows').textContent,/No risks match/);
});
