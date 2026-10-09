import {apiGet, apiPost, esc} from '/app.js';

export function enrollmentProfiles(host, onChanged) {
  const root = '/api/v1/admin/agent-enrollment-profiles';
  host.innerHTML = `<div class="card mb-3"><div class="card-body"><h2 class="h5">Enrollment profiles</h2>
  <p>Give each autoscaling fleet its own bootstrap key. Each enrolled machine receives a separate agent credential. Revoking a profile revokes every agent enrolled through it.</p>
  <form class="row g-3" data-form>
  <div class="col-md-6"><label class="form-label" for="ep-name">Profile name</label><input id="ep-name" name="name" class="form-control" maxlength="128" required></div>
  <div class="col-md-3"><label class="form-label" for="ep-days">Agent credential lifetime (days)</label><input id="ep-days" name="days" type="number" min="1" max="365" value="90" class="form-control" required></div>
  <div class="col-md-3"><label class="form-label" for="ep-limit">Maximum enrollments (optional)</label><input id="ep-limit" name="limit" type="number" min="1" max="1000000" class="form-control"></div>
  <div class="col-md-6"><label class="form-label" for="ep-expires">Enrollment expiry (local time, optional)</label><input id="ep-expires" name="expires" type="datetime-local" class="form-control"></div>
  <div class="col-md-6"><label class="form-label" for="ep-cidrs">Allowed CIDRs (optional, comma-separated)</label><input id="ep-cidrs" name="cidrs" class="form-control" placeholder="192.0.2.0/24, 2001:db8::/32"></div>
  <div class="col-12"><label class="form-label" for="ep-labels">Fleet labels (optional, one key=value per line)</label><textarea id="ep-labels" name="labels" class="form-control" rows="2" placeholder="environment=production"></textarea></div>
  <div class="col-12"><button class="btn btn-primary" data-create>Create enrollment profile</button></div></form>
  <p class="small-muted mt-3">Store bootstrap keys in your cluster’s secret manager. Limit access to the enrollment endpoint at your reverse proxy. <a href="/help/administrator-agents.html#agent-enrollment-profiles">Enrollment help</a></p>
  <div data-status role="status" aria-live="polite"></div>
  <section data-secret-box hidden class="alert alert-warning"><h3 class="h6">Save this bootstrap key now</h3><p>KEEN displays this key once. It permits enrollment into this profile. Store it in the fleet’s secret manager.</p>
  <label for="ep-secret" class="form-label">Bootstrap key</label><input id="ep-secret" type="password" readonly autocomplete="off" class="form-control font-monospace">
  <div class="d-flex gap-2 mt-2"><button type="button" data-reveal class="btn btn-outline-primary">Show key</button><button type="button" data-copy class="btn btn-primary">Copy key</button><button type="button" data-dismiss class="btn btn-outline-secondary">Saved — dismiss</button></div></section>
  <div data-profiles class="d-grid gap-3 mt-3"></div></div></div>`;
  const q = s => host.querySelector(s), form = q('[data-form]');
  let demo = true;
  const status = text => {q('[data-status]').textContent = text;};
  const clear = () => {q('#ep-secret').value=''; q('#ep-secret').type='password'; q('[data-secret-box]').hidden=true; q('[data-reveal]').textContent='Show key';};
  const reveal = key => {clear(); q('#ep-secret').value=key; q('[data-secret-box]').hidden=false;};
  const error = e => {let text=String(e.message||e);try{const v=JSON.parse(text).detail;text=typeof v==='string'?v:'Check the profile settings.';}catch{}status(text);};
  async function run(button, fn) {button.disabled=true;try{await fn();}catch(e){error(e);}finally{button.disabled=demo;}}
  function action(p,label,verb,confirmation) {
    const b=document.createElement('button');b.type='button';b.className='btn btn-outline-primary btn-sm';b.textContent=label;b.disabled=demo;
    b.onclick=()=>run(b,async()=>{if(confirmation&&!confirm(confirmation))return;clear();const r=await apiPost(`${root}/${p.id}/${verb}`,{});if(r.bootstrap_key)reveal(r.bootstrap_key);await refresh(demo);await onChanged();});return b;
  }
  async function refresh(isDemo) {
    demo=isDemo; q('[data-create]').disabled=demo;
    const data=await apiGet(root);const list=q('[data-profiles]');list.replaceChildren();
    for(const p of data.profiles){
      const card=document.createElement('article');card.className='border rounded p-3';
      card.innerHTML=`<h3 class="h6">${esc(p.name)} <span class="badge text-bg-secondary">${esc(p.state)}</span></h3><p class="small-muted text-break">Profile: ${esc(p.id)}</p><p>${Number(p.enrollment_count)} enrolled${p.max_enrollments?' / '+Number(p.max_enrollments)+' maximum':''} · Credentials last ${Number(p.token_days)} days</p><p class="small-muted">Enrollment expires: ${esc(p.expires_at?new Date(p.expires_at+'Z').toLocaleString():'No expiry')} · Networks: ${esc(p.allowed_cidrs.join(', ')||'Any address with a valid key')}</p><div class="d-flex flex-wrap gap-2" data-actions></div>`;
      if(p.state!=='revoked')card.querySelector('[data-actions]').append(
        action(p,'Rotate bootstrap key','rotate','Replace this profile’s bootstrap key? Existing agents keep working.'),
        action(p,p.state==='paused'?'Resume enrollment':'Pause enrollment',p.state==='paused'?'resume':'pause'),
        action(p,'Revoke profile and agents','revoke',`Permanently revoke ${p.name} and ALL its enrolled agents? Existing evidence remains available.`));
      list.append(card);
    }
    if(!data.profiles.length)list.textContent='No enrollment profiles. Individual agents can still be registered separately.';
    return new Map(data.profiles.map(p=>[p.id,p.name]));
  }
  form.onsubmit=e=>{e.preventDefault();return run(q('[data-create]'),async()=>{
    clear();const f=new window.FormData(form),labels={};
    for(const line of String(f.get('labels')).split('\n').filter(x=>x.trim())){const at=line.indexOf('=');if(at<1)throw Error('Use key=value for each label.');labels[line.slice(0,at).trim()]=line.slice(at+1).trim();}
    const r=await apiPost(root,{name:String(f.get('name')),token_days:Number(f.get('days')),max_enrollments:f.get('limit')?Number(f.get('limit')):null,expires_at:f.get('expires')?new Date(f.get('expires')).toISOString():null,allowed_cidrs:String(f.get('cidrs')).split(',').map(x=>x.trim()).filter(Boolean),labels});
    reveal(r.bootstrap_key);status('Enrollment profile created. Save its bootstrap key.');await refresh(demo);
  });};
  q('[data-dismiss]').onclick=clear;
  q('[data-reveal]').onclick=()=>{const show=q('#ep-secret').type==='password';q('#ep-secret').type=show?'text':'password';q('[data-reveal]').textContent=show?'Hide key':'Show key';};
  q('[data-copy]').onclick=async()=>{try{await navigator.clipboard.writeText(q('#ep-secret').value);status('Bootstrap key copied.');}catch{status('Use Show key to copy it manually.');}};
  window.addEventListener('pagehide',clear);document.getElementById('tab-agents')?.addEventListener('hidden.bs.tab',clear);
  return {refresh,clear};
}
