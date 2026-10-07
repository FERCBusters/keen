import {apiGet, apiPost, apiPut} from '/app.js';
const root='/api/v1/admin', byId=id=>document.getElementById('hub-'+id);
let entries=[], mode='source', surface='mapping';
function button(text, action, danger=false){const b=document.createElement('button');b.type='button';b.className='btn btn-sm '+(danger?'btn-outline-danger':'btn-outline-primary');b.textContent=text;b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){showError(e);}finally{b.disabled=false;}};return b;}
function showError(e){const el=byId('status');el.hidden=false;el.className='alert alert-danger';let text=e.message||String(e);try{text=JSON.parse(text).detail||text;}catch{}el.textContent=text;}
function go(source, create=false){showSurface('mapping');location.hash='evidence-config';window.dispatchEvent(new CustomEvent(create?'keen-collection-create':'keen-filter-source',{detail:{source}}));}
async function load(){
 const [definitions, integrations, config]=await Promise.all([apiGet(root+'/evidence-definitions'),apiGet(root+'/integrations'),apiGet(root+'/managed-configurations')]);
 entries=config.items.map(c=>({key:'builtin:'+c.name,name:c.name,source:c.name,type:'Server environment',collectors:definitions.collectors.filter(x=>x.adapter===c.name),rules:definitions.rules.filter(x=>x.when?.source===c.name || (c.name==='webhooks' && x.when?.source?.startsWith('webhook:'))),demo:integrations.demo_mode}));
 for(const c of integrations.connections){const collectors=integrations.collectors.filter(x=>x.connection_id===c.id);const sources=collectors.map(x=>'integration:'+x.id);entries.push({key:'api:'+c.id,name:c.name,source:'API integrations',type:c.base_url,collectors,rules:definitions.rules.filter(x=>sources.includes(x.when?.source)),sources,demo:integrations.demo_mode});}
 render();
}
function render(){
 for(const view of ['source','connection']){const selected=surface===view;byId(view).classList.toggle('active',selected);}
 byId('view-description').textContent=mode==='source'?'Browse sources. Expand a source to manage its collections and definitions.':'All connections are expanded below. Each built-in source currently has one server-environment connection; custom API ingesters can have multiple named connections.';
 const container=byId('list');container.replaceChildren();const query=byId('search').value.toLowerCase();
 const visible=entries.filter(e=>`${e.name} ${e.source} ${e.type}`.toLowerCase().includes(query));
 const groups=new Map();for(const e of visible){const key=mode==='source'?e.source:e.key;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(e);}
 for(const [key,items] of groups){const section=document.createElement(mode==='source'?'details':'section');section.className='card mb-3';if(mode==='source')section.open=items.some(e=>e.collectors.length||e.rules.length);const head=document.createElement(mode==='source'?'summary':'h3');head.className='card-header h6 mb-0';head.textContent=mode==='source'?`${key} · ${items.length} connection${items.length===1?'':'s'}`:(items[0].key.startsWith('builtin:')?`${items[0].source} — Server environment`:items[0].name);section.append(head);const body=document.createElement('div');body.className='card-body';
 for(const item of items){const row=document.createElement('div');row.className='border-bottom pb-3 mb-3';const title=document.createElement('h4');title.className='h6';title.textContent=item.key.startsWith('builtin:')?'Server environment':item.name;const meta=document.createElement('p');meta.className='small text-break';meta.textContent=`${item.type} · ${item.collectors.length} collections · ${item.rules.length} definitions`;if(mode==='source')row.append(title);row.append(meta);
 if(item.key.startsWith('builtin:')) for(const c of item.collectors){
  const line=document.createElement('div');line.className='d-flex flex-wrap align-items-center gap-2 my-2';
  const label=document.createElement('span');label.textContent=c.section+': '+c.key;label.className='text-break';
  line.append(label,button('Edit collection',()=>editCollection(item.source,c)),button('Map events',()=>go(item.source)));row.append(line);
 }
 const actions=document.createElement('div');actions.className='d-flex flex-wrap gap-2';
 if(item.key.startsWith('builtin:')){actions.append(button('View definitions',()=>go(item.source)),button('Add collection',()=>editCollection(item.source)));}
 else{for(const c of item.collectors)actions.append(button('Definitions: '+c.name,()=>go('integration:'+c.id)));}
 const remove=button(item.key.startsWith('builtin:')?'Clear connection':'Delete connection',()=>removeConnection(item),true);remove.disabled=!!item.demo;actions.append(remove);row.append(actions);body.append(row);}
 section.append(body);container.append(section);}
 if(!visible.length){const p=document.createElement('p');p.textContent='No matching sources or connections.';container.append(p);}
}
async function removeConnection(item){
 const url=root+'/evidence-connections/'+encodeURIComponent(item.key);
 const plan=await apiGet(url+'/removal');if(plan.blocked)throw new Error(plan.blocked);
 const dialog=byId('confirm');const detail=byId('impact');detail.replaceChildren();
 const p=document.createElement('p');p.textContent=`${item.name}: remove ${plan.collections} collections, ${plan.definitions.length} definitions and ${plan.automatic_mappings} automatic evidence-to-control links.`;detail.append(p);
 const list=document.createElement('ul');for(const rule of plan.definitions){const li=document.createElement('li');li.textContent=rule.name;list.append(li);}detail.append(list);
 byId('confirm-name').textContent=item.name;byId('typed-name').value='';byId('delete').disabled=true;
 byId('typed-name').oninput=()=>{byId('delete').disabled=byId('typed-name').value!==item.name;};
 byId('delete').onclick=async()=>{byId('delete').disabled=true;try{await apiPost(url+'/remove',{fingerprint:plan.fingerprint});dialog.close();await load();window.dispatchEvent(new Event('keen-connections-changed'));}catch(e){dialog.close();showError(e);}};
 dialog.showModal();
}
byId('source').onclick=()=>{showSurface('source');mode='source';render();};
byId('connection').onclick=()=>{showSurface('connection');mode='connection';render();};
byId('search').oninput=render;byId('refresh').onclick=()=>load().catch(showError);byId('cancel').onclick=()=>byId('confirm').close();
window.addEventListener('keen-connections-changed',()=>load().catch(showError));
// Admin page already enforces authentication; load once and offer explicit refresh.
load().catch(showError);

const layouts={
 redmine:{projects:['project','label']},
 forgejo:{organizations:['org','label'],users:['user','label'],feeds:['url','label']},
 gitea:{organizations:['org','label'],users:['user','label'],feeds:['url','label']},
 gitlab:{groups:['group','label'],users:['user','label']},
 github:{organizations:['org','label'],repos:['owner','repo','label'],feeds:['url','label']},
 loki:{queries:['name','logql']},jenkins:{jobs:['name','label','kind']},rss:{feeds:['name','url','label']},
 cloudwatch_logs:{queries:['name','region','log_group','filter_pattern']},taiga:{projects:['id','label']},
 google_workspace:{streams:['name','application','user_key']},bookstack:{selected_pages:['id','name']}
};
async function editCollection(adapter, existing=null){
 if(!layouts[adapter])throw new Error('Use the evidence editor for this specialised collection type.');
 const config=await apiGet(root+'/managed-configurations/'+encodeURIComponent(adapter));
 const dialog=byId('collection');const type=byId('collection-type');
 type.replaceChildren();for(const key of Object.keys(layouts[adapter]))type.add(new Option(key,key));
 type.value=existing?.section||Object.keys(layouts[adapter])[0];type.disabled=!!existing;
 const paint=()=>{byId('collection-fields').replaceChildren();for(const name of layouts[adapter][type.value]){
  const label=document.createElement('label');label.className='form-label d-block';label.textContent=adapter==='redmine'&&name==='project'?'Project ID / identifier (or * for all accessible projects)':name;
  const input=document.createElement('input');input.className='form-control';input.dataset.field=name;input.value=existing?.entry?.[name]??'';label.append(input);byId('collection-fields').append(label);
 }byId('collection-extra').value=JSON.stringify(existing?.entry||{},null,2);};
 type.onchange=paint;paint();byId('collection-title').textContent=(existing?'Edit ':'Add ')+adapter+' collection';byId('collection-error').textContent='';
 byId('collection-save').onclick=async()=>{const save=byId('collection-save');save.disabled=true;try{
  const entry=JSON.parse(byId('collection-extra').value||'{}');
  if(!entry||Array.isArray(entry)||typeof entry!=='object')throw new Error('Additional settings must be a JSON object.');
  for(const input of byId('collection-fields').querySelectorAll('input')){const key=input.dataset.field;const value=input.value.trim();if(value)entry[key]=key==='id'?Number(value):value;else delete entry[key];}
  const document=structuredClone(config.document);const rows=document[type.value]||[];
  if(existing){const index=rows.findIndex(row=>JSON.stringify(row)===JSON.stringify(existing.entry));if(index<0)throw new Error('Collection changed. Close and reopen the editor.');rows[index]=entry;}else rows.push(entry);
  document[type.value]=rows;await apiPut(root+'/managed-configurations/'+encodeURIComponent(adapter),{version:config.version,document});
  dialog.close();await load();window.dispatchEvent(new Event('keen-connections-changed'));
 }catch(e){byId('collection-error').textContent=e.message||String(e);}finally{save.disabled=false;}};
 dialog.showModal();
}
byId('collection-cancel').onclick=()=>byId('collection').close();

function showSurface(view){
 surface=view;
 const mapping=document.getElementById('evidence-mapping-view'), management=document.getElementById('evidence-management-view');
 mapping.hidden=view!=='mapping';management.hidden=view==='mapping';
 management.setAttribute('aria-labelledby','hub-'+(view==='connection'?'connection':'source'));
 for(const name of ['mapping','source','connection']){const b=byId(name),selected=name===view;b.classList.toggle('active',selected);b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;}
 if(view!=='mapping')byId('panel').open=true;
}
byId('mapping').onclick=()=>showSurface('mapping');
for(const [index,name] of ['mapping','source','connection'].entries())byId(name).addEventListener('keydown',event=>{
 const names=['mapping','source','connection'];let next;
 if(event.key==='ArrowRight')next=(index+1)%3;else if(event.key==='ArrowLeft')next=(index+2)%3;else if(event.key==='Home')next=0;else if(event.key==='End')next=2;else return;
 event.preventDefault();byId(names[next]).click();byId(names[next]).focus();
});
for(const event of ['keen-show-evidence-mapping','keen-filter-source','keen-integration-rule'])window.addEventListener(event,()=>showSurface('mapping'));
