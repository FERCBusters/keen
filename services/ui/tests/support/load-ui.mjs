// Load complete first-party ES modules into an isolated DOM. Only the external
// design-system/network boundary is substituted; KEEN functions run unchanged.
import {JSDOM,VirtualConsole} from 'jsdom';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const publicRoot=new URL('../../public/',import.meta.url);
export async function loadUi(path,{html='',vendor={},url='https://keen.example/events.html?framework=F1',globals={},setup,scripts=[]}={}){
 const runtimeErrors=[];
 const virtualConsole=new VirtualConsole();
 virtualConsole.on('jsdomError',error=>{
  if(error.type==='unhandled exception')runtimeErrors.push(error.detail||error);
 });
 const dom=new JSDOM(html,{url,virtualConsole});
 const {window}=dom;
 setup?.(window);
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const unsupported=()=>{throw new Error('Unexpected design-system/network call');};
 const external={esc,getCurrentFramework:()=> 'F1',getUiConfig:()=>({}),
  apiGet:unsupported,apiPost:unsupported,apiPut:unsupported,apiDelete:unsupported,fmtTs:value=>String(value),safeExternalHref:unsupported,
  toast:unsupported,sourceBadgeHtml:unsupported,initNavbar:unsupported,initCollapsibleFilterSections:unsupported,...vendor};
 const context=vm.createContext({window,document:window.document,Node:window.Node,Event:window.Event,
  CustomEvent:window.CustomEvent,HTMLElement:window.HTMLElement,HTMLSelectElement:window.HTMLSelectElement,
  Option:window.Option,location:window.location,history:window.history,navigator:window.navigator,
  confirm:()=>true,CSS:{escape:value=>String(value).replace(/[^a-zA-Z0-9_-]/g,c=>'\\'+c)},
  URL,URLSearchParams,console,setTimeout,clearTimeout,...globals});
 for(const source of scripts) vm.runInContext(source,context);
 const cache=new Map();
 const vendorModule=new vm.SyntheticModule(Object.keys(external),function(){for(const [k,v] of Object.entries(external))this.setExport(k,v);},{context});
 async function moduleFor(url){
  if(cache.has(url.href))return cache.get(url.href);
  const module=new vm.SourceTextModule(await readFile(url,'utf8'),{context,identifier:url.href});
  cache.set(url.href,module);
  await module.link(async(specifier,parent)=>{
   if(specifier==='/vendor/mosp-design-system/app.js')return vendorModule;
   const next=specifier.startsWith('/')?new URL(specifier.slice(1),publicRoot):new URL(specifier,parent.identifier);
   if(!next.href.startsWith(publicRoot.href))throw new Error('Unexpected module '+specifier);
   return moduleFor(next);
  });
  return module;
 }
 const module=await moduleFor(new URL(path,publicRoot));
 await module.evaluate();
 return {api:module.namespace,window,document:window.document,close:()=>{window.close();if(runtimeErrors.length)throw new AggregateError(runtimeErrors,'Unhandled page errors');}};
}
