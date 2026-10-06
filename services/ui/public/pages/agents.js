import {apiGet,apiPost,initNavbar,esc} from '/app.js';
const root='/api/v1/admin/agents', $=id=>document.getElementById("ka-"+id);
let demo=true, loading=false;
const endpoint=location.origin+'/api/v1/otlp/logs';$('endpoint').textContent=endpoint;
function msg(text,tone='info'){$('status').textContent=text;$('status').className='alert alert-'+tone;$('status').hidden=!text;}
function fail(e){let text=String(e.message||e);try{const d=JSON.parse(text).detail;text=typeof d==='string'?d:'Request failed; check the entered values.';}catch{}msg(text,'danger');}
function date(v){return v?new Date(/Z$|[+-]\d\d:\d\d$/.test(v)?v:v+'Z').toLocaleString():'Never';}
function dismiss(){$('token').value='';$('token').type='password';$('credential').hidden=true;$('reveal').textContent='Show credential';}
function credential(token){dismiss();$('token').value=token;$('credential').hidden=false;$('credential').scrollIntoView({behavior:'smooth',block:'center'});$('copy').focus();}
async function guard(button,fn){button.disabled=true;try{await fn();}catch(e){fail(e);}finally{button.disabled=demo;}}
function button(label,fn){const b=document.createElement('button');b.type='button';b.className='btn btn-outline-primary btn-sm';b.textContent=label;b.disabled=demo;b.onclick=()=>guard(b,fn);return b;}
async function load(){if(loading)return;loading=true;try{
const data=await apiGet(root);demo=data.demo_mode;$('register-button').disabled=demo;
if(demo)msg('You are running in a demo environment. Agent registration and external log ingestion are disabled. Use your own KEEN installation to connect servers.');
$('agents').replaceChildren();
if(!data.agents.length){const p=document.createElement('p');p.textContent='No agents registered yet.';$('agents').append(p);}
for(const a of data.agents){const expired=new Date(a.expires_at+'Z')<new Date(), stale=!a.last_seen||Date.now()-new Date(a.last_seen+'Z')>5*60000;
const h=a.health||{}, status=!a.enabled?'Revoked':expired?'Credential expired':h.delivery_blocked?'Delivery paused':stale?'Awaiting contact':'Connected';
const bad=!a.enabled||expired||h.delivery_blocked;
const card=document.createElement('article');card.className='card';card.innerHTML=`<div class="card-body"><div class="d-flex justify-content-between flex-wrap gap-2"><h2 class="h5">${esc(a.name)}</h2><span class="badge ${bad?'text-bg-danger':stale?'text-bg-secondary':'text-bg-success'}">${esc(status)}</span></div><div class="small-muted text-break">${esc(a.id)}</div><div class="row g-2 my-2"><div class="col-md-6">Last contact: ${esc(date(a.last_seen))}</div><div class="col-md-6">Credential expiry: ${esc(date(a.expires_at))}</div><div class="col-md-6">Queued: ${Number(h.queued||0)} · Queue bytes: ${Number(h.queue_bytes||0)}</div><div class="col-md-6">Dropped records / detected gaps: ${Number(h.rejected||0)}</div></div><p class="small-muted">Health report: ${esc(date(h.health_received_at))} · Agent ${esc(h.version||'version not reported')}</p><div class="agent-sources small mb-3"></div><div class="agent-actions d-flex gap-2 flex-wrap"></div></div>`;
const sources=card.querySelector('.agent-sources');for(const [name,state] of Object.entries(h.sources||{})){const p=document.createElement('p');p.className='mb-1 text-break';p.textContent=name+': '+state;sources.append(p);}
if(a.enabled&&!demo)card.querySelector('.agent-actions').append(button('Rotate credential',async()=>{if(!confirm('Invalidate the current credential and issue a replacement for '+a.name+'?'))return;const r=await apiPost(root+'/'+a.id+'/rotate',{expires_days:Number($('expiry').value)});credential(r.token);await load();}),button('Revoke',async()=>{if(!confirm('Permanently revoke '+a.name+'? Existing evidence remains available.'))return;await apiPost(root+'/'+a.id+'/revoke',{});dismiss();await load();}));
const filtered=document.createElement('p');filtered.className='small-muted';filtered.textContent='Filtered before delivery: '+Object.entries(h.filtered_by_source||{}).map(([source,count])=>source+': '+count).join(' · ');if(Object.keys(h.filtered_by_source||{}).length)card.querySelector('.card-body').append(filtered);
$('agents').append(card);
}}finally{loading=false;}}
$('register').onsubmit=e=>{e.preventDefault();guard($('register-button'),async()=>{const r=await apiPost(root,{name:$('agent-name').value,expires_days:Number($('expiry').value)});$('agent-name').value='';credential(r.token);await load();});};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('token').value);msg('Credential copied. Save it to the protected token file.','success');}catch{msg('Clipboard unavailable. Use Show credential and copy it manually.','warning');}};
$('reveal').onclick=()=>{const show=$('token').type==='password';$('token').type=show?'text':'password';$('reveal').textContent=show?'Hide credential':'Show credential';};
$('dismiss').onclick=dismiss;window.addEventListener('pagehide',dismiss);
document.getElementById('tab-agents')?.addEventListener('hidden.bs.tab', dismiss);
$('refresh').onclick=()=>load().catch(fail);
$('yaml').onclick=()=>{const text=`endpoint: ${JSON.stringify(endpoint)}\ntoken_file: /etc/keen-agent/token\nstate_dir: /var/lib/keen-agent\nqueue_mb: 256\npoll_seconds: 5\nretention_days: 0\ninitial_lookback: 24h\nsources:\n  - name: system\n    kind: journal\n    parser: syslog\n`;
const url=URL.createObjectURL(new Blob([text],{type:'application/yaml'}));const a=document.createElement('a');a.href=url;a.download='keen-agent.yaml';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
if (!document.getElementById("pane-agents")) await initNavbar();
await load().catch(fail);
setInterval(()=>{if(!document.hidden)load().catch(fail);},30000);
