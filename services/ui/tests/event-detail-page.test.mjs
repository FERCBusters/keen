import test from 'node:test';
import assert from 'node:assert/strict';
import {page,settle} from './support/page-fixture.mjs';
const event={id:'e1',source:'keen-agent',summary:'<img src=x> alert',timestamp:'2026-01-01',system:'server',actor:'root',action:'ossec.alert',severity:0,
 controls:[],artifacts:[],normalized_payload:{fields:{'ossec.rule.id':'31101'}},raw_pointer:{uri:'s3://redacted'}};
async function fixture(t,options={}){
 const ui=await page(t,'event',{query:'&id=e1',...options,get:async u=>{
  if(u.pathname==='/api/v1/events/e1'){if(options.fail)throw new Error('Unavailable');return {...event,...options.event};}
  if(u.pathname==='/api/v1/events/e1/questions')return {threads:[]};
  if(u.pathname==='/api/v1/audits/by-event/e1')return {items:[]};
  throw new Error('Unexpected GET '+u);
 },post:async(u,b)=>{if(u.pathname.endsWith('/incident'))return {id:'incident1',url:'https://example.org/incident'};if(u.pathname.endsWith('/questions/mark-seen'))return {ok:true};throw new Error('Unexpected POST '+u);}});
 await settle(()=>options.query===''?ui.el('status').textContent.includes('Missing'):options.fail?ui.el('status').textContent.includes('Unavailable'):ui.el('normalized').textContent.includes('ossec.rule.id'));
 return ui;
}
test('event detail renders structured payload and an event-scoped mapping shortcut',async t=>{
 const ui=await fixture(t);assert.equal(ui.el('summary').querySelector('img'),null);assert.match(ui.el('summary').textContent,/<img src=x>/);
 assert.match(ui.el('controls').textContent,/unmapped/);assert.equal(ui.el('btnCreateMapping').hidden,false);
 assert.match(ui.el('btnCreateMapping').href,/framework=A&from_event=e1#evidence-config/);
 assert.match(ui.el('normalized').textContent,/31101/);
});
test('event severity zero remains visible',async t=>{
 const ui=await fixture(t);assert.equal(ui.el('severity').textContent,'0');
});
test('non-administrators do not get a mapping-editor shortcut',async t=>{
 const ui=await fixture(t,{me:{is_admin:false}});assert.equal(ui.el('btnCreateMapping').hidden,true);
});
test('unsafe source URLs are not enabled',async t=>{
 const ui=await fixture(t,{event:{source_url:'javascript:alert(1)'}});assert.equal(ui.el('openSource').style.display,'none');
});
test('mapped controls and downloadable evidence are rendered',async t=>{
 const ui=await fixture(t,{event:{controls:[{id:'c1',ref:'A1',title:'Access control',framework:'A',method:'manual'}],artifacts:[{id:'a1',kind:'raw',content_type:'application/json',size_bytes:12}]}});
 assert.match(ui.el('controls').textContent,/A1/);assert.ok(ui.document.querySelector('a[href="/api/v1/artifacts/a1/download"]'));
});
test('missing event IDs make no event request',async t=>{
 const ui=await fixture(t,{query:''});assert.match(ui.el('status').textContent,/Missing event id/);assert.ok(!ui.calls.some(c=>c.url.startsWith('/api/v1/events/')));
});
test('event fetch failure is visible',async t=>{
 const ui=await fixture(t,{fail:true});assert.match(ui.el('status').textContent,/Unavailable/);
});
