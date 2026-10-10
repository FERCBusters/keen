import {editSourceConnection} from './source-connection-editor.js';
import {confirmEvidenceRemoval} from './evidence-removal.js';
import {apiGet, apiPut, apiPost, apiDelete} from '/app.js';
const root='/api/v1/admin', byId=id=>document.getElementById('hub-'+id);
let connectionTypes=[];
let entries=[], surface='source';
function button(text, action, danger=false){const b=document.createElement('button');b.type='button';b.className='btn btn-sm '+(danger?'btn-outline-danger':'btn-outline-primary');b.textContent=text;b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){showError(e);}finally{b.disabled=false;}};return b;}
function showError(e){const el=byId('status');el.hidden=false;el.className='alert alert-danger';let text=e.message||String(e);try{text=JSON.parse(text).detail||text;}catch{}el.textContent=text;}
function go(source, create=false, connection=null, input=null){showSurface('mapping');location.hash='evidence-config';window.dispatchEvent(new CustomEvent(create?'keen-collection-create':'keen-filter-source',{detail:{source,connection_id:connection?.id || (source.startsWith('integration:') ? null : 'env:'+source),input}}));}
async function load(){
 const [definitions, integrations, config, named]=await Promise.all([apiGet(root+'/evidence-definitions'),apiGet(root+'/integrations'),apiGet(root+'/managed-configurations'),apiGet(root+'/source-connections')]);
 connectionTypes=named.types;
 entries=config.items.map(c=>({key:'builtin:'+c.name,name:c.name,source:c.name,type:'Environment default',collectors:definitions.collectors.filter(x=>x.adapter===c.name&&!x.connection_id),rules:definitions.rules.filter(x=>x.when?.source===c.name || (c.name==='webhooks' && x.when?.source?.startsWith('webhook:'))),demo:integrations.demo_mode}));
 for(const c of named.connections)entries.push({key:'named:'+c.id,name:c.name,source:c.source,type:(c.enabled?'Enabled':'Paused')+' · '+(c.configuration.base_url||'Account connection'),collectors:definitions.collectors.filter(x=>x.connection_id===c.id),rules:definitions.rules.filter(x=>x.when?.connection_id===c.id),connection:c,demo:integrations.demo_mode});
 for(const c of integrations.connections){const collectors=integrations.collectors.filter(x=>x.connection_id===c.id);const sources=collectors.map(x=>'integration:'+x.id);entries.push({key:'api:'+c.id,name:c.name,source:'API integrations',type:c.base_url,collectors,rules:definitions.rules.filter(x=>sources.includes(x.when?.source)),sources,demo:integrations.demo_mode});}
 entries=entries.filter(e=>e.key.startsWith('api:') || (connectionTypes.some(t=>t.source===e.source&&t.enabled) && (!e.key.startsWith('builtin:') || e.collectors.length)));
 render();
}
function render(){
 byId('view-description').textContent='1. Define a source → 2. Add inputs → 3. Add mapping rules';
 const container=byId('list');container.replaceChildren();
 const query=byId('search').value.toLowerCase();
 const visible=entries.filter(e=>`${e.name} ${e.source}`.toLowerCase().includes(query));
 for(const item of visible){
  const section=document.createElement('section');section.className='card mb-3';
  const heading=document.createElement('h3');heading.className='card-header h6';
  heading.textContent=item.key.startsWith('builtin:')?item.source+' · Environment default':item.name;
  const body=document.createElement('div');body.className='card-body';
  const meta=document.createElement('p');meta.className='small text-break';
  meta.textContent=`${item.source} · ${item.type} · ${item.collectors.length} inputs`;
  body.append(meta);
  const editable=item.key.startsWith('builtin:')||item.connection?.managed_by==='database';
  const push=item.source==='webhooks';
  if(push&&item.connection){const url=document.createElement('p');url.className='text-break';url.textContent=location.origin+'/api/v1/webhooks/connections/'+item.connection.id+'/'+(item.connection.configuration.provider||'provider')+'/EVENT_TYPE';body.append(url);}
  if(!item.collectors.length&&!push){const hint=document.createElement('p');hint.textContent='Add an input to choose what this source collects. Then add a mapping rule to link its evidence to controls.';body.append(hint);}
  for(const input of item.collectors){
   const line=document.createElement('div');line.className='d-flex flex-wrap align-items-center gap-2 my-2';
   const label=document.createElement('span');label.className='text-break';label.textContent=input.name||`${input.section}: ${input.key}`;line.append(label);
   if(editable&&!push)line.append(button('Edit input',()=>editCollection(item.source,input,item.connection)));
   line.append(button('Add mapping rule',()=>go(item.sources?'integration:'+input.id:item.source,true,item.connection,input)));
   if(item.connection?.managed_by==='database'&&!push)line.append(button('Remove input',()=>removeInput(item.connection,input),true));
   body.append(line);
  }
  const actions=document.createElement('div');actions.className='d-flex flex-wrap gap-2 mt-3';
  if(item.connection?.managed_by==='database')actions.append(button('Edit source',()=>editConnection(item.connection)));
  if(editable&&!push)actions.append(button('Add input',()=>editCollection(item.source,null,item.connection)));
  if(item.collectors.length||push){
   if(!item.sources)actions.append(button('Add mapping rule',()=>go(item.source,true,item.connection)));
   actions.append(button('View mapping rules',()=>go(item.source)));
   if(item.connection&&!push)actions.append(button('Collect now',async()=>{await apiPost(root+'/source-connections/'+item.connection.id+'/run',{});byId('status').hidden=false;byId('status').className='alert alert-success';byId('status').textContent='Collection queued.';}));
  }
  const remove=button(item.key.startsWith('builtin:')?'Clear inputs':'Delete source',()=>removeConnection(item),true);
  remove.disabled=!!item.demo||!!(item.connection&&item.connection.managed_by!=='database');actions.append(remove);
  body.append(actions);section.append(heading,body);container.append(section);
 }
 if(!visible.length){const p=document.createElement('p');p.textContent=query?'No matching sources.':'No sources defined. Choose Define a source to enter its address and credentials.';container.append(p);}
}
async function removeConnection(item){
 if(item.connection){if(confirm('Delete '+item.name+'? Collected evidence and its connection identity will be retained.')){await apiDelete(root+'/source-connections/'+item.connection.id+'?version='+item.connection.version);await load();}return;}
 await confirmEvidenceRemoval(item, {onRemoved: load, onError: showError});
}
byId('start-setup').onclick=()=>editConnection().catch(showError);
byId('source').onclick=()=>{showSurface('source');render();};
byId('search').oninput=render;byId('refresh').onclick=()=>load().catch(showError);byId('cancel').onclick=()=>byId('confirm').close();
window.addEventListener('keen-connections-changed',()=>load().catch(showError));
// Admin page already enforces authentication; load once and offer explicit refresh.
load().catch(showError);

const layouts={
 riskledger:{organizations:['org','label']},
 redmine:{projects:['project','label']},
 forgejo:{organizations:['org','label'],users:['user','label'],feeds:['url','label']},
 gitea:{organizations:['org','label'],users:['user','label'],feeds:['url','label']},
 gitlab:{groups:['group','label'],users:['user','label']},
 github:{organizations:['org','label'],repos:['owner','repo','label'],feeds:['url','label']},
 loki:{queries:['name','logql']},jenkins:{jobs:['name','label','kind']},rss:{feeds:['name','url','label']},
 cloudwatch_logs:{queries:['name','region','log_group','filter_pattern']},taiga:{projects:['id','label']},
 google_workspace:{streams:['name','application','user_key']},bookstack:{selected_pages:['id','name']}
};
async function editCollection(adapter, existing=null, connection=null){
 if(!layouts[adapter])throw new Error('Use the evidence editor for this specialised collection type.');
 const config=connection?{document:connection.inputs,version:connection.version}:await apiGet(root+'/managed-configurations/'+encodeURIComponent(adapter));
 const dialog=byId('collection');const type=byId('collection-type');
 type.replaceChildren();for(const key of Object.keys(layouts[adapter]))type.add(new Option(({users:'User',organizations:'Organisation',feeds:'Feed'})[key]||key,key));
 type.value=existing?.section||Object.keys(layouts[adapter])[0];type.disabled=!!existing;
 const paint=()=>{byId('collection-fields').replaceChildren();for(const name of layouts[adapter][type.value]){
  const label=document.createElement('label');label.className='form-label d-block';label.textContent=adapter==='riskledger'&&name==='org'?'Organisation UUID (or * for the API key’s organisation)':adapter==='redmine'&&name==='project'?'Project ID / identifier (or * for all accessible projects)':name;
  const input=document.createElement('input');input.className='form-control';input.dataset.field=name;input.value=existing?.entry?.[name]??'';label.append(input);byId('collection-fields').append(label);
 }byId('collection-extra').value=JSON.stringify(existing?.entry||{},null,2);};
 type.onchange=paint;paint();byId('collection-title').textContent=(existing?'Edit ':'Add ')+adapter+' input · '+(connection?.name||'Environment default');byId('collection-error').textContent='';
 byId('collection-save').onclick=async()=>{const save=byId('collection-save');save.disabled=true;try{
  const entry=JSON.parse(byId('collection-extra').value||'{}');
  if(!entry||Array.isArray(entry)||typeof entry!=='object')throw new Error('Additional settings must be a JSON object.');
  for(const input of byId('collection-fields').querySelectorAll('input')){const key=input.dataset.field;const value=input.value.trim();if(value)entry[key]=key==='id'?Number(value):value;else delete entry[key];}
  const document=structuredClone(config.document);const rows=document[type.value]||[];
  if(existing){const index=rows.findIndex(row=>JSON.stringify(row)===JSON.stringify(existing.entry));if(index<0)throw new Error('Collection changed. Close and reopen the editor.');rows[index]=entry;}else rows.push(entry);
  document[type.value]=rows;if(connection)await apiPut(root+'/source-connections/'+connection.id,connectionPayload(connection,{inputs:document}));else await apiPut(root+'/managed-configurations/'+encodeURIComponent(adapter),{version:config.version,document});
  dialog.close();await load();window.dispatchEvent(new Event('keen-connections-changed'));
 }catch(e){byId('collection-error').textContent=e.message||String(e);}finally{save.disabled=false;}};
 dialog.showModal();
}
byId('collection-cancel').onclick=()=>byId('collection').close();

function showSurface(view){
 surface=view;
 const mapping=document.getElementById('evidence-mapping-view'), management=document.getElementById('evidence-management-view');
 mapping.hidden=view!=='mapping';management.hidden=view==='mapping';
 management.setAttribute('aria-labelledby','hub-source');
 for(const name of ['source','mapping']){const b=byId(name),selected=name===view;b.classList.toggle('active',selected);b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;}
 if(view!=='mapping')byId('panel').open=true;
}
byId('mapping').onclick=()=>showSurface('mapping');
for(const [index,name] of ['source','mapping'].entries())byId(name).addEventListener('keydown',event=>{
 const names=['source','mapping'];let next;
 if(event.key==='ArrowRight')next=(index+1)%2;else if(event.key==='ArrowLeft')next=(index+1)%2;else if(event.key==='Home')next=0;else if(event.key==='End')next=1;else return;
 event.preventDefault();byId(names[next]).click();byId(names[next]).focus();
});
for(const event of ['keen-show-evidence-mapping','keen-filter-source','keen-integration-rule'])window.addEventListener(event,()=>showSurface('mapping'));

window.addEventListener('keen-manage-source', event => {
  byId('search').value = event.detail?.source || '';
  showSurface('source');
  render();
  for (const section of byId('list').querySelectorAll('details')) section.open = true;
  byId('search').focus();
});


function connectionPayload(c,changes={}){return {source:c.source,name:c.name,enabled:c.enabled,configuration:c.configuration,inputs:c.inputs,version:c.version,...changes};}
async function editConnection(connection=null){
 editSourceConnection({types:connectionTypes,connection,onSaved:async result=>{
  try {await load();window.dispatchEvent(new Event('keen-connections-changed'));showSurface('source');render();
   if(!connection&&result.source!=='webhooks')await editCollection(result.source,null,result);
  }catch(error){showError(error);}
 }});
}

async function removeInput(connection,input){
 if(!confirm('Remove this input? Existing evidence and mapping rules are retained.'))return;
 const inputs=structuredClone(connection.inputs);
 inputs[input.section]=(inputs[input.section]||[]).filter(row=>JSON.stringify(row)!==JSON.stringify(input.entry));
 await apiPut(root+'/source-connections/'+connection.id,connectionPayload(connection,{inputs}));
 await load();window.dispatchEvent(new Event('keen-connections-changed'));
}

showSurface('source');
