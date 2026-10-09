import test from 'node:test';
import assert from 'node:assert/strict';
import {page,settle} from './support/page-fixture.mjs';
const event={id:'event-1',source:'keen-agent',summary:'<img src=x onerror=evil()>',identifier:'server-a',timestamp:'2026-01-01',artifact_count:2,
  controls:Array.from({length:6},(_,i)=>({id:'c'+i,ref:'A'+i,title:'Control '+i}))};
async function fixture(t,options={}) {
 return page(t,'events',{...options,get:async u=>{
  if(u.pathname==='/api/v1/sources/meta')return {items:[{source:'keen-agent',label:'Agents'}]};
  if(u.pathname==='/api/v1/controls')return {items:[{ref:'A1',title:'Access',type:'control'}]};
  if(u.pathname==='/api/v1/events/facets')return {sources:[{source:'keen-agent',count:101}],controls:[{ref:'A1',count:3,title:'Access'}]};
  if(u.pathname==='/api/v1/events'){
   if(options.fail)throw new Error('Unavailable');
   return {total:options.empty?0:101,items:options.empty?[]:[event]};
  }
  throw new Error('Unexpected GET '+u);
 }});
}
test('events render safe content, bounded control badges and correct paging',async t=>{
 const ui=await fixture(t);
 assert.match(ui.el('rows').textContent,/<img src=x onerror=evil\(\)>/);
 assert.equal(ui.el('rows').querySelector('img'),null);
 assert.match(ui.el('rows').textContent,/\+2/);
 assert.match(ui.el('resultMeta').textContent,/1–50 of 101/);
 assert.equal(ui.el('prev').disabled,true);
 ui.el('next').click();
 await settle(()=>ui.el('resultMeta').textContent==='51–100 of 101');
 assert.equal(new URL(ui.calls.filter(c=>c.url.startsWith('/api/v1/events?')).at(-1).url,'https://keen.example').searchParams.get('offset'),'50');
 assert.equal(ui.el('prev').disabled,false);
});
test('applying filters resets pagination and preserves framework and clause',async t=>{
 const ui=await fixture(t,{query:'&offset=50&clause=4.1'});
 ui.el('q').value='  blocked packets  ';
 ui.el('source').value='keen-agent';
 ui.el('control').value='A1';
 ui.el('filters').dispatchEvent(new ui.window.Event('submit',{cancelable:true}));
 await settle(()=>ui.el('resultMeta').textContent==='1–50 of 101');
 const u=new URL(ui.calls.filter(c=>c.url.startsWith('/api/v1/events?')).at(-1).url,'https://keen.example');
 for(const [key,value] of Object.entries({q:'blocked packets',source:'keen-agent',control:'A1',framework:'A',clause:'4.1',offset:'0'}))assert.equal(u.searchParams.get(key),value);
 assert.match(ui.document.title,/Agents.*Access.*4.1/);
});
test('unmapped view avoids the second expensive facets request',async t=>{
 const ui=await fixture(t,{query:'&unmapped=true'});
 assert.equal(ui.calls.filter(c=>c.url.includes('/events/facets')).length,0);
 assert.equal(ui.el('unmapped').checked,true);
 assert.match(ui.calls.find(c=>c.url.startsWith('/api/v1/events?')).url,/unmapped=true/);
});
test('date edits normalise reversed ranges and remove precise timestamps',async t=>{
 const ui=await fixture(t,{query:'&start_ts=2026-01-01T10:00:00&end_ts=2026-01-01T11:00:00'});
 ui.el('startDateDmy').value='04/02/2026';ui.el('endDateDmy').value='01/02/2026';
 ui.el('filters').dispatchEvent(new ui.window.Event('submit',{cancelable:true}));
 await settle(()=>ui.el('resultMeta').textContent==='1–50 of 101');
 const u=new URL(ui.window.location.href);
 assert.equal(u.searchParams.get('start_date'),'2026-02-01');assert.equal(u.searchParams.get('end_date'),'2026-02-04');
 assert.equal(u.searchParams.has('start_ts'),false);
});
for(const empty of [true,false])test(empty?'empty events show a useful message':'API failure clears stale results and reports the error',async t=>{
 const ui=await fixture(t,{empty,fail:!empty,me:{is_admin:false}});
 assert.match(ui.el('rows').textContent,empty?/No events match/:/Failed to load events/);
 assert.doesNotMatch(ui.el('eventsActions').textContent,/Add Diary/);
 if(!empty)assert.match(ui.el('status').textContent,/Unavailable/);
});
test('saved searches contain the applied filters without the current offset',async t=>{
 const ui=await fixture(t,{query:'&offset=50&source=keen-agent',globals:{prompt:()=> 'Agent events'},post:async(u,body)=>{assert.equal(u.pathname,'/api/v1/me/saved-searches');return body;}});
 ui.el('saveSearch').click();await settle(()=>ui.el('status').textContent==='Saved');
 const payload=ui.calls.find(c=>c.method==='POST').body;
 assert.equal(payload.name,'Agent events');assert.match(payload.url,/source=keen-agent/);assert.doesNotMatch(payload.url,/offset=/);
});
