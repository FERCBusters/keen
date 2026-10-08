const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('path').resolve(__dirname,'../../ui/public/pages/evidence-config.js'),'utf8');
const fn=source.slice(source.indexOf('async function loadRuleRevisions()'),source.indexOf('async function loadSamples()'));
const el={'rule-history':{},'rule-restore':{},'rule-revision':{options:[],replaceChildren(...xs){this.options=xs},add(x){this.options.push(x)}}};
const context=vm.createContext({$:s=>el[s],Option:class{constructor(text,value){this.text=text;this.value=value}},selectedRule:null,apiRoot:'/v1/admin',requests:[],waiting:[]});
vm.runInContext('const apiGet=url=>{requests.push(url);return new Promise(resolve=>waiting.push(resolve))};'+fn,context);
(async()=>{
 await context.loadRuleRevisions();assert.equal(el['rule-history'].hidden,true);
 context.selectedRule='first';const old=context.loadRuleRevisions();
 context.selectedRule='second';const current=context.loadRuleRevisions();
 context.waiting[1]({items:[{revision:1,version:97,at:'now'}]});await current;
 context.waiting[0]({items:[{revision:51,version:51,at:'old'}]});await old;
 assert.equal(el['rule-revision'].options[1].text,'#1 · now');assert.equal(el['rule-revision'].options[1].value,'97');
 assert.equal(context.requests[1],'/v1/admin/mapping-rules/second/revisions');
 context.selectedRule=null;await context.loadRuleRevisions();assert.equal(el['rule-restore'].disabled,true);
 console.log('UI logic passed: new rule hidden, scoped requests, per-rule numbering, stale response ignored.');
})().catch(e=>{console.error(e);process.exit(1)});
