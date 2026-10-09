// Literal field paths are retained beside human-readable names.
const names={
 'service.name':'Service','process.name':'Program','process.pid':'Process ID',
 'host.name':'Host','client.ip':'Client IP','status':'HTTP status','path':'Request path',
 'http.method':'HTTP method','log.file.path':'Log file','package':'Package',
 'package_status':'Package state','version':'Package version','old_version':'Previous package version',
 'new_version':'New package version','job.command':'Scheduled command','user.name':'User name',
 'user.id':'Process user ID','group.id':'Process group ID','_SYSTEMD_UNIT':'Journal service',
 'SYSLOG_IDENTIFIER':'Journal program','PRIORITY':'Journal priority','log.syslog.priority':'Syslog priority',
 'parser':'Parser','http.status_code':'HTTP response status',
 'project.identifier':'Redmine project','issue.status.name':'Issue status',
 'issue.tracker.name':'Issue tracker','issue.priority.name':'Issue priority',
 'issue.assigned_to.name':'Issue assignee','journal.notes':'Journal comment'
};
export function fieldLabel(path){
 const key=path.at(-1);const label=(path.length===1&&key==='source')?'Collection name':names[key]||key.replaceAll('_',' ').replaceAll('.',' ').replace(/\b\w/g,c=>c.toUpperCase());
 const exact=path.map((key,index)=>index===0&&/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)?key:'['+JSON.stringify(key)+']').join('');
 return `${exact} — ${label}`;
}
export function draftFields(payload){
 const f=payload?.fields||{};const result=[];
 const add=(path,value)=>{if(typeof value==='string'&&value!==''&&value.length<=4096)result.push({path,operator:'equals',value});};
 add(['source'],payload?.source);
 // Select meaningful scope; omit PIDs, UIDs, cursors, timestamps, versions and IDs.
 for(const key of ['service.name','process.name','host.name','client.ip','status','http.status_code','http.method','path','log.file.path','parser','package','package_status','job.command','user.name','project.identifier','issue.status.name','issue.tracker.name','issue.priority.name','issue.assigned_to.name']){
  // A session scope identifies a single transient login, not a stable service.
  if(key==='service.name'&&/^session-.*\.scope$/.test(f[key]||''))continue;
  add(['fields',key],f[key]);
 }
 if(!f['service.name']&&!/^session-.*\.scope$/.test(f._SYSTEMD_UNIT||''))add(['fields','_SYSTEMD_UNIT'],f._SYSTEMD_UNIT);
 if(!f['process.name'])add(['fields','SYSLOG_IDENTIFIER'],f.SYSLOG_IDENTIFIER);
 return result.slice(0,20);
}

// Match the backend's literal-key paths and scalar comparison representation.
// Keep dotted attribute names intact; arrays are not addressable conditions.
export function observedPayloadFields(payload){
 const result=[];
 function visit(value,path){
  if(path.length>8||value===null||Array.isArray(value))return;
  if(typeof value==='object'){
   for(const [key,child] of Object.entries(value)){
    if(key.length&&key.length<=256)visit(child,[...path,key]);
   }
  }else if(path.length&&['string','number','boolean'].includes(typeof value)){
   const text=typeof value==='string'?value:JSON.stringify(value);
   if(text.length<=4096)result.push({path,value:text});
  }
 }
 visit(payload,[]);
 return result;
}

export function fieldChoiceGroups(currentFields, otherFields, selectedPath=null){
 const seen=new Set();
 const groups=[];
 function add(label,fields){
  const items=[];
  for(const {path} of fields){
   const key=JSON.stringify(path);
   if(seen.has(key))continue;
   seen.add(key);items.push({path,label:fieldLabel(path)});
  }
  items.sort((a,b)=>a.label.localeCompare(b.label));
  if(items.length)groups.push({label,items});
 }
 add('Fields in this event',currentFields);
 add('Fields seen in other samples',otherFields);
 add('Predefined fields (not observed)',[
  ...['service.name','process.name','_SYSTEMD_UNIT','SYSLOG_IDENTIFIER','client.ip',
   'status','path','log.file.path','http.method','package','package_status','job.command']
   .map(key=>({path:['fields',key]})),{path:['source']}
 ]);
 if(selectedPath)add('Selected field (not observed)',[{path:selectedPath}]);
 return groups;
}
