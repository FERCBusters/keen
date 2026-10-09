import test from 'node:test';
import assert from 'node:assert/strict';
import {loadUi} from './support/load-ui.mjs';

const ui=await loadUi('app.js');
const a=ui.api;
test.after(()=>ui.close());

for(const [label,input,expected] of [
 ['empty',{total:0},[0,0,true,true]],['first',{total:101,limit:50},[1,50,true,false]],
 ['middle',{total:101,limit:50,offset:50},[51,100,false,false]],
 ['last',{total:101,limit:50,offset:100},[101,101,false,true]],
 ['exact',{total:100,limit:50,offset:50},[51,100,false,true]],
 ['negative offset',{total:5,offset:-20},[1,5,true,true]],
])test('pagination '+label,()=>{
 const s=a.offsetPagerState(input);
 assert.deepEqual([s.start,s.end,s.prevDisabled,s.nextDisabled],expected);
 assert.ok(s.prevOffset>=0&&s.nextOffset>s.offset);
});

test('both pagers reflect limits and disabled buttons cannot paginate',()=>{
 const doc=ui.document;doc.body.innerHTML='<button id="p"></button><button id="n"></button><button id="p2"></button><button id="n2"></button>';
 const pager={prev:[doc.getElementById('p'),doc.getElementById('p2')],next:[doc.getElementById('n'),doc.getElementById('n2')]};
 const calls=[];a.wireOffsetPagerButtons(pager,d=>calls.push(d));
 a.setOffsetPagerDisabled(pager,{total:51,limit:50,offset:0});
 pager.prev[0].click();pager.next[1].click();assert.deepEqual(calls,['next']);
 a.setOffsetPagerDisabled(pager,{total:51,limit:50,offset:50});
 pager.next[0].click();pager.prev[1].click();assert.deepEqual(calls,['next','prev']);
});

for(const status of ['open','in_progress','completed','archived','unknown'])test('audit status '+status,()=>{
 assert.equal(a.isAuditActive({status}),['open','in_progress'].includes(status));
 assert.equal(a.isAuditLocked(status),['completed','archived'].includes(status));
});
for(const [user,allowed] of [[null,false],[{},false],[{can_manage_audits:true},true],[{is_admin:true},true],[{is_admin:false},false]])
 test('audit sampling permission '+JSON.stringify(user),()=>assert.equal(a.canSampleIntoAudit(user),allowed));

for(const [operator,value,ok] of [['lt',9,true],['lt',10,false],['lte',10,true],['eq',10,true],['eq',11,false],['gte',10,true],['gte',9,false],['gt',11,true],['gt',10,false]])
 test(`metric ${operator} ${value}`,()=>{
  const result=a.effectivenessMetricThresholdState({threshold_operator:operator,target_value:10,target_unit:'%'},{metric_value:value});
  assert.equal(result.ok,ok);assert.equal(result.state,ok?'ok':'bad');assert.match(result.title,/%/);
 });
for(const value of [null,undefined,'','bad',Infinity])test('invalid metric '+String(value),()=>{
 assert.equal(a.effectivenessMetricThresholdState({threshold_operator:'gte',target_value:10},{metric_value:value}),null);
});
test('zero metric remains a valid value',()=>assert.equal(a.effectivenessMetricThresholdState({threshold_operator:'eq',target_value:0},{metric_value:0}).ok,true));

for(const [input,expected] of [[null,'Fallback'],['  Miguel  ','Miguel'],[{username:'user',name:'Name'},'user'],[{user:{email:'a@example.test'}},'a@example.test'],[{},'Fallback']])
 test('user label '+JSON.stringify(input),()=>assert.equal(a.userDisplayName(input,'Fallback'),expected));
test('user labels and titles cannot introduce markup',()=>{
 const html=a.userPillHtml({username:'<img src=x onerror=alert(1)>',email:'" onmouseover="alert(1)'});
 ui.document.body.innerHTML=html;
 assert.equal(ui.document.querySelector('img'),null);
 assert.equal(ui.document.querySelector('[onmouseover]'),null);
 assert.match(ui.document.body.textContent,/<img/);
});

for(const url of ['/home/user','//outside.test','javascript:alert(1)','data:text/html,x','ftp://host/a','https://','https://a.test/'+ 'x'.repeat(2049)])
 test('upstream rejects '+url.slice(0,40),()=>assert.equal(a.safeAbsoluteHttpHref(url),''));
for(const url of ['https://example.test/a?x=1','http://example.test','HTTPS://example.test/a'])
 test('upstream permits '+url,()=>assert.equal(a.safeAbsoluteHttpHref(url),url));

for(const color of ['#fff','#ffffff'])test('light badge '+color,()=>assert.equal(a.idealTextColor(color),'#111827'));
for(const color of ['#000','#000000'])test('dark badge '+color,()=>assert.equal(a.idealTextColor(color),'#f8fafc'));

test('changelog loader propagates API failure without rendering error markup',async()=>{
 const u=await loadUi('app.js',{vendor:{apiGet:async()=>{throw new Error('<img src=x onerror=alert(1)>');}}});
 try{
  const target=u.document.createElement('div');
  await assert.rejects(u.api.loadEntityChangelog(target,'/api/fail'),/onerror/);
  assert.equal(target.querySelector('img'),null);
  assert.match(target.textContent,/Loading/);
 }finally{u.close();}
});
