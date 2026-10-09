import test from 'node:test';
import assert from 'node:assert/strict';
import {loadUi} from './support/load-ui.mjs';

test('enrollment profile creation displays a one-time key and clears it on navigation', async t=>{
  let saved;
  const p={id:'profile',name:'Web fleet',state:'active',token_days:30,enrollment_count:0,max_enrollments:5,allowed_cidrs:[],expires_at:null};
  const ui=await loadUi('pages/agent-enrollment.js',{html:'<section id="host"></section>',vendor:{
    apiGet:async()=>({profiles:[p]}),apiPost:async(url,body)=>{saved={url,body};return {profile:p,bootstrap_key:'ke_test-secret'};}}});
  t.after(ui.close);
  const host=ui.document.getElementById('host'),controller=ui.api.enrollmentProfiles(host,async()=>{});
  await controller.refresh(false);
  host.querySelector('#ep-name').value='Web fleet';host.querySelector('#ep-days').value='30';host.querySelector('#ep-limit').value='5';
  host.querySelector('#ep-labels').value='environment=production';
  await host.querySelector('form').onsubmit({preventDefault(){}});
  assert.equal(saved.body.name,'Web fleet');assert.equal(saved.body.labels.environment,'production');
  assert.equal(host.querySelector('#ep-secret').value,'ke_test-secret');
  assert.equal(host.querySelector('#ep-secret').type,'password');
  ui.window.dispatchEvent(new ui.window.Event('pagehide'));
  assert.equal(host.querySelector('#ep-secret').value,'');assert.equal(host.querySelector('[data-secret-box]').hidden,true);
});

test('profile revocation is explicit and revoked profiles have no reactivation controls', async t=>{
  const calls=[];let p={id:'profile',name:'Fleet <x>',state:'active',token_days:90,enrollment_count:3,allowed_cidrs:[]};
  const ui=await loadUi('pages/agent-enrollment.js',{html:'<section id="host"></section>',vendor:{
    apiGet:async()=>({profiles:[p]}),apiPost:async url=>{calls.push(url);p={...p,state:'revoked'};return{profile:p};}}});
  t.after(ui.close);
  const host=ui.document.getElementById('host'),controller=ui.api.enrollmentProfiles(host,async()=>{});
  await controller.refresh(false);
  assert.equal(host.querySelector('x'),null);
  await [...host.querySelectorAll('[data-actions] button')].find(b=>b.textContent==='Revoke profile and agents').onclick();
  assert.equal(calls[0],'/api/v1/admin/agent-enrollment-profiles/profile/revoke');
  assert.equal(host.querySelectorAll('[data-actions] button').length,0);
  await controller.refresh(true);assert.equal(host.querySelector('[data-create]').disabled,true);
});
