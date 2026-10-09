import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadUi} from './load-ui.mjs';

export async function settle(check) {
  for(let i=0;i<100;i++) { if(check()) return; await new Promise(r=>setTimeout(r,5)); }
  assert.fail('The page did not reach the expected state');
}

// These adapters represent the external design system only. API calls must be
// explicitly handled by each test; unrecognised calls fail instead of returning {}.
export async function page(t,name,{get,post,patch,remove,me={is_admin:true},query='',globals={},setup,scripts=[],htmlName=name}={}) {
  const calls=[],unexpected=[];
  const route=(method,handler)=>async (url,body)=>{
    calls.push({method,url,body});
    if(!handler) throw new Error(`Unexpected ${method} ${url}`);
    const parsed=new URL(url,'https://keen.example');
    // Shared navbar catalog, independent of the page's API fixture.
    if(method==='GET'&&parsed.pathname==='/api/v1/frameworks')return {items:[{slug:'A',name:'Framework A',control_count:2,has_clauses:true}],default_framework:'A'};
    try {return await handler(parsed,body);}
    catch(error){if(String(error).includes('Unexpected '))unexpected.push(String(error));throw error;}
  };
  let win;
  const html=await readFile(new URL('../../public/'+htmlName+'.html',import.meta.url),'utf8');
  const ui=await loadUi('pages/'+name+'.js',{
    html,scripts,url:'https://keen.example/'+name+'.html?framework=A'+query,
    setup(window){win=window;window.HTMLElement.prototype.scrollIntoView=()=>{};setup?.(window);},globals:{getComputedStyle:el=>win.getComputedStyle(el),requestAnimationFrame:fn=>win.setTimeout(fn,0),cancelAnimationFrame:id=>win.clearTimeout(id),TextEncoder,TextDecoder,performance,queueMicrotask,FormData,Blob,setInterval,clearInterval,localStorage:undefined,...globals},
    vendor:{
      collapseToggleButtonHtml:()=>'<button type="button">Toggle</button>',initNavbar:async()=>me,initCollapsibleFilterSections:()=>{},enableTableSorting:()=>{},
      apiPostForm:route('POST',post),wireIsoDateTextField:()=>{},isoDateToDisplay:s=>s||'',dateFormatPlaceholder:()=> 'yyyy-mm-dd',
      apiGet:route('GET',get),apiPost:route('POST',post),apiPatch:route('PATCH',patch),apiDelete:route('DELETE',remove),
      fmtTsFilename:s=>s,getSourceMeta:async()=>({}),applyTheme:()=>{},THEMES:['purple','dark'],
      getCurrentFramework:()=> 'A',getFrameworkCatalog:async()=>({items:[{slug:'A',name:'Framework A'}]}),
      debounce:fn=>fn,shorten:(s,n)=>String(s).slice(0,n),tsSortKey:s=>s||'',
      toast:(el,message,type)=>{if(el){el.textContent=message;el.dataset.alert=type;el.style.display='';}},
      withFramework:(path,fw)=>{const u=new URL(path,'https://keen.example');u.searchParams.set('framework',fw);return u.pathname+u.search;},
      qs:(key,fallback)=>new URL(win.location.href).searchParams.get(key)??fallback,
      qbool:(key,fallback)=>new URL(win.location.href).searchParams.has(key)?new URL(win.location.href).searchParams.get(key)==='true':fallback,
      qint:(key,fallback)=>Number(new URL(win.location.href).searchParams.get(key)??fallback),
      setQuery:values=>{const u=new URL(win.location.href);for(const [k,v] of Object.entries(values))v===null?u.searchParams.delete(k):u.searchParams.set(k,String(v));win.history.replaceState({},'',u);},
      isoDateToDmy:s=>s?s.split('-').reverse().join('/'):'',
      dmyToIsoDate:s=>/^\d{2}\/\d{2}\/\d{4}$/.test(s)?s.split('/').reverse().join('-'):'',
      wireIsoDmyDateField:()=>{},attachSavedSearchAutocomplete:()=>{},resolveSavedSearchUrl:()=>null,
      showInlineLoading:(el,text)=>{if(el)el.textContent=text;},
      showTableLoading:(el,n,text)=>{if(el)el.textContent=text;},
      sourceLabel:(s,meta)=>meta?.[s]?.label||s,
      sourceBadgeHtml:s=>String(s),upstreamLinkHtml:()=>'',
      safeExternalHref:s=>/^https?:\/\//.test(s)?s:'',
    },
  });
  t.after(()=>{ui.close();assert.deepEqual(unexpected,[],'Every API request must have an explicit fixture');});
  return {...ui,calls,el:id=>ui.document.getElementById(id)};
}
