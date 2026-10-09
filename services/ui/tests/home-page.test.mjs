import test from 'node:test';
import assert from 'node:assert/strict';
import {page,settle} from './support/page-fixture.mjs';
const overview={framework:'A',control_count:2,clause_count:1,events:{mapped:8,unmapped:3},attention:[{href:'/audits.html',label:'Audit due',detail:'Review soon'}],counts:{risks:1}};
async function fixture(t,options={}){
 return page(t,'home',{htmlName:'index',...options,setup(window){window.matchMedia=()=>({matches:true,addEventListener(){},removeEventListener(){}});},get:async u=>{
  if(u.pathname==='/api/v1/frameworks')return {items:[{slug:'A',name:'Framework A',control_count:2,has_clauses:true}]};
  if(u.pathname==='/api/v1/home'){if(options.fail)throw new Error('Offline');return overview;}
  if(u.pathname==='/api/v1/home/controls')return {items:[],sections:[],total:0};
  const routes={'/api/v1/risks':{id:'r1',asset:'Database',threat_summary:'Disclosure',risk_score:20},'/api/v1/events':{id:'e1',summary:'Agent alert',source:'keen-agent'},'/api/v1/audits':{id:'a1',title:'Annual audit',status:'open'},'/api/v1/pestle/items':{id:'p1',item:'Regulation',type:'Legal',lens:'External'},'/api/v1/interested-parties':{id:'i1',name:{name:'Customers'},nature:{name:'External'}},'/api/v1/isms/effectiveness-measures':{id:'m1',summary:'Coverage'},'/api/v1/sources':{source:'keen-agent',label:'Agents',event_count:8},'/api/v1/clauses':{id:'cl1',ref:'4.1',title:'Context'}};
  if(routes[u.pathname])return {items:options.empty?[]:[routes[u.pathname]],total:options.empty?0:1};
  throw new Error('Unexpected GET '+u);
 }});
}
test('home overview renders evidence counts and attention links',async t=>{
 const ui=await fixture(t);assert.match(ui.el('homeKpis').textContent,/8/);assert.match(ui.el('homeAttention').textContent,/3 events awaiting mapping/);
 assert.match(ui.el('homeAttention').textContent,/Audit due/);assert.ok(ui.document.querySelector('[data-kind="risks"]'));
});
for(const [kind,text] of [['risks','Database'],['evidence','Agent alert'],['audits','Annual audit'],['pestle','Regulation'],['interested_parties','Customers'],['effectiveness_measures','Coverage'],['sources','Agents'],['clauses','Context']])test('home explorer opens '+kind+' collection',async t=>{
 const ui=await fixture(t);ui.document.querySelector(`[data-kind="${kind}"] .js-entry-zoom`).click();
 await settle(()=>ui.el('explorerCanvas').textContent.includes(text));
 assert.ok(ui.el('explorerCanvas').querySelector('a'));
});
test('home limits explorer entry points to user permissions',async t=>{
 const ui=await fixture(t,{me:{can_view_events:true}});assert.equal(ui.document.querySelector('[data-kind="risks"]'),null);
 assert.ok(ui.document.querySelector('[data-kind="evidence"]'));
});
