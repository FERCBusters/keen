// Literal field paths are retained beside human-readable names.
const names={
 'service.name':'Service','process.name':'Program','process.pid':'Process ID',
 'host.name':'Host','client.ip':'Client IP','status':'HTTP status','path':'Request path',
 'http.method':'HTTP method','log.file.path':'Log file','package':'Package',
 'package_status':'Package state','version':'Package version','old_version':'Previous package version',
 'new_version':'New package version','job.command':'Scheduled command','user.name':'User name',
 'user.id':'Process user ID','group.id':'Process group ID','_SYSTEMD_UNIT':'Journal service',
 'SYSLOG_IDENTIFIER':'Journal program','PRIORITY':'Journal priority','log.syslog.priority':'Syslog priority',
 'parser':'Parser','http.status_code':'HTTP response status'
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
 for(const key of ['service.name','process.name','host.name','client.ip','status','http.status_code','http.method','path','log.file.path','parser','package','package_status','job.command','user.name']){
  // A session scope identifies a single transient login, not a stable service.
  if(key==='service.name'&&/^session-.*\.scope$/.test(f[key]||''))continue;
  add(['fields',key],f[key]);
 }
 if(!f['service.name']&&!/^session-.*\.scope$/.test(f._SYSTEMD_UNIT||''))add(['fields','_SYSTEMD_UNIT'],f._SYSTEMD_UNIT);
 if(!f['process.name'])add(['fields','SYSLOG_IDENTIFIER'],f.SYSLOG_IDENTIFIER);
 return result.slice(0,20);
}
