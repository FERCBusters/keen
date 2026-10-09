import test from 'node:test';
import assert from 'node:assert/strict';
import {page,settle} from './support/page-fixture.mjs';

const party={id:'p1',name:{id:'n1',name:'<script>customer</script>'},nature:{id:'t1',name:'External'},note:'Review annually',controls:[{id:'c1',ref:'A1'}],communications:[{event:'Incident',with_whom:'Customers'}]};
async function parties(t,options={}) {
 const names=[{id:'n1',name:'Customers'}],natures=[{id:'t1',name:'External'}];
 return page(t,'interested_parties',{...options,get:async u=>{
  switch(u.pathname){
   case '/api/v1/interested-parties/meta':return {events:[],when:[],with_whom:[],methods:[]};
   case '/api/v1/interested-parties/names':return {items:names};
   case '/api/v1/interested-parties/natures':return {items:natures};
   case '/api/v1/interested-parties':if(options.fail)throw new Error('Offline');return {items:options.empty?[]:[party],total:options.empty?0:1};
   default:throw new Error('Unexpected GET '+u);
  }
 },post:async(u,b)=>{assert.equal(u.pathname,'/api/v1/interested-parties/names');names.push({id:'n2',name:b.name});return names.at(-1);},
 remove:async u=>{assert.equal(u.pathname,'/api/v1/interested-parties/names/n1');names.splice(0,1);return {ok:true};}});
}
test('interested parties render links, communications and hostile names as text',async t=>{
 const ui=await parties(t);
 assert.match(ui.el('partyRows').textContent,/<script>customer<\/script>/);
 assert.equal(ui.el('partyRows').querySelector('script'),null);
 assert.match(ui.el('partyRows').textContent,/Incident \/ Customers/);
 assert.match(ui.el('partyRows').querySelector('a').href,/id=p1.*framework=A/);
 assert.equal(ui.el('partyItemsMeta').textContent,'1 of 1 interested parties');
});
test('interested-party lookup create/delete refreshes the actual list',async t=>{
 const ui=await parties(t);
 ui.el('newPartyName').value='  Suppliers  ';ui.el('addPartyName').click();
 await settle(()=>ui.el('partyNameList').textContent.includes('Suppliers'));
 assert.equal(ui.el('newPartyName').value,'');
 assert.equal(ui.calls.find(c=>c.method==='POST').body.name,'Suppliers');
 ui.document.querySelector('[data-delete-name="n1"]').click();
 await settle(()=>!ui.el('partyNameList').textContent.includes('Customers'));
});
test('interested-party searches and multi-select filters send selected IDs',async t=>{
 const ui=await parties(t);
 ui.el('partyQ').value='  Annual  ';ui.el('partyQ').dispatchEvent(new ui.window.Event('input'));
 ui.document.querySelector('.keen-filter-pill-grid [data-value="n1"]').click();
 await settle(()=>ui.calls.filter(c=>c.url.startsWith('/api/v1/interested-parties?')).length>=3);
 const last=new URL(ui.calls.at(-1).url,'https://keen.example');assert.equal(last.searchParams.get('q'),'Annual');assert.equal(last.searchParams.get('name_id'),'n1');
});
for(const state of ['empty','reader','denied','error'])test('interested parties '+state+' state',async t=>{
 const ui=await parties(t,{empty:state==='empty',fail:state==='error',me:state==='reader'?{can_view_interested_parties:true}:state==='denied'?{}:{is_admin:true}});
 if(state==='empty')assert.match(ui.el('partyRows').textContent,/No interested parties/);
 if(state==='error')assert.match(ui.el('status').textContent,/Offline/);
 if(state==='denied')assert.match(ui.el('partyRows').textContent,/do not have permission/);
 if(state==='reader'){
  assert.equal(ui.el('newInterestedParty').style.display,'none');
  assert.equal(ui.el('partyRows').querySelector('.btn').textContent,'View');
  assert.equal(ui.document.querySelector('[data-delete-name]'),null);
 }
});

const item={id:'p1',type:'Legal',lens:'External',item:'<img src=x> Regulation',overall_relevance:{code:'high',label:'1 High'},rationale:'Annual',business_processes:[],clauses:[]};
async function pestlePage(t,options={}) {
 const processes=[{id:'b1',name:'Governance'}];
 return page(t,'pestle',{...options,get:async u=>{
  if(u.pathname==='/api/v1/pestle/meta')return {types:['Legal','Social'],lenses:['Internal','External'],relevance_levels:[{id:'high',code:'high',label:'1 High'}]};
  if(u.pathname==='/api/v1/pestle/items'){if(options.fail)throw new Error('Unavailable');return {items:options.empty?[]:[item],total:options.empty?0:1,framework:'A'};}
  if(u.pathname==='/api/v1/pestle/business-processes')return {items:processes};
  throw new Error('Unexpected GET '+u);
 },post:async(u,b)=>{assert.equal(u.pathname,'/api/v1/pestle/business-processes');processes.push({id:'b2',name:b.name});return processes.at(-1);}});
}
test('PESTLE list renders safe content and scoped links',async t=>{
 const ui=await pestlePage(t);assert.match(ui.el('pestleRows').textContent,/Regulation/);assert.equal(ui.el('pestleRows').querySelector('img'),null);
 assert.match(ui.el('pestleItemsMeta').textContent,/1 PESTLE/);assert.match(ui.el('newPestleItem').href,/framework=A/);
});
test('PESTLE business process creation validates blank names and refreshes list',async t=>{
 const ui=await pestlePage(t);ui.el('addBusinessProcess').click();
 assert.match(ui.el('status').textContent,/Enter a business process/);assert.equal(ui.calls.filter(c=>c.method==='POST').length,0);
 ui.el('businessProcessName').value='  Operations  ';ui.el('addBusinessProcess').click();
 await settle(()=>ui.el('businessProcessList').textContent.includes('Operations'));
 assert.equal(ui.el('businessProcessName').value,'');assert.equal(ui.el('addBusinessProcess').disabled,false);
});
for(const state of ['empty','reader','denied','error'])test('PESTLE '+state+' state',async t=>{
 const ui=await pestlePage(t,{empty:state==='empty',fail:state==='error',me:state==='reader'?{can_view_pestle:true}:state==='denied'?{}:{is_admin:true}});
 if(state==='empty')assert.match(ui.el('pestleRows').textContent,/No PESTLE/);
 if(state==='error')assert.match(ui.el('status').textContent,/Unavailable/);
 if(state==='denied'){assert.match(ui.el('pestleRows').textContent,/do not have permission/);assert.ok(!ui.calls.some(c=>c.url.startsWith('/api/v1/pestle/items')));}
 if(state==='reader')assert.ok(ui.el('newPestleItem').classList.contains('d-none'));
});
