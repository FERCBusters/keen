import test from 'node:test';
import assert from 'node:assert/strict';
import {page,settle} from './support/page-fixture.mjs';
async function fixture(t,options={}){
 const ui=await page(t,'account',{...options,get:async u=>{
  if(u.pathname==='/api/v1/frameworks')return {items:[{slug:'A',name:'Framework A'}],default_framework:'A'};
  if(u.pathname==='/api/v1/sources/meta')return {items:[{source:'keen-agent',label:'Agents',color:'#123456'}]};
  if(u.pathname==='/api/v1/me/saved-searches')return [{id:'s1',name:'<img src=x> Search',url:'/events.html?source=keen-agent'}];
  if(u.pathname.startsWith('/api/v1/me/'))return {items:[]};
  throw new Error('Unexpected GET '+u);
 },patch:async(u,b)=>{assert.equal(u.pathname,'/api/v1/me/preferences');return b;},
 post:async(u,b)=>{assert.equal(u.pathname,'/api/v1/me/password');throw new Error('Incorrect current password');}});
 await settle(()=>ui.el('savedSearchesList').textContent.includes('Search'));
 return ui;
}
test('account saved searches render safely and populate landing-page choices',async t=>{
 const ui=await fixture(t);assert.equal(ui.el('savedSearchesList').querySelector('img'),null);
 assert.match(ui.el('prefLandingPage').textContent,/Search/);
 assert.ok([...ui.el('prefDefaultFramework').options].some(o=>o.value==='A'));
});
test('upstream-auth account hides password and logout controls',async t=>{
 const ui=await fixture(t,{me:{password_change_enabled:false,logout_enabled:false}});
 assert.equal(ui.el('pwCard').style.display,'none');assert.equal(ui.el('logoutBtn').style.display,'none');
});
test('password form rejects missing inputs and displays backend failures',async t=>{
 const ui=await fixture(t);ui.el('pwForm').dispatchEvent(new ui.window.Event('submit',{cancelable:true}));
 assert.match(ui.el('status').textContent,/Both current and new password/);assert.ok(!ui.calls.some(c=>c.method==='POST'));
 ui.el('curPw').value='wrong';ui.el('newPw').value='new-test-only-password';
 ui.el('pwForm').dispatchEvent(new ui.window.Event('submit',{cancelable:true}));
 await settle(()=>ui.el('status').textContent.includes('Incorrect current password'));
 assert.equal(ui.calls.find(c=>c.method==='POST').body.current_password,'wrong');
});
