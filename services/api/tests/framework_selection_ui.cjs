const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('path').resolve(__dirname,'../../ui/public/pages/framework-editor.js'),'utf8');
const fn=source.slice(source.indexOf('function renderOrganisationFrameworks(data)'),source.indexOf("$('save-selection').addEventListener"));
class Element {
  constructor(){this.children=[];this.options=[];this._value='';this.style={setProperty(){}};this.dataset={};this.attributes={}}
  setAttribute(name,value){this.attributes[name]=String(value)}
  replaceChildren(...items){this.children=items;this.options=[];this._value=''}
  append(...items){this.children.push(...items)}
  add(option){this.options.push(option);if(!this._value)this._value=option.value}
  get value(){return this._value} set value(v){this._value=v}
  querySelectorAll(selector){
    const all=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);
    if(selector==='*')return all;
    return all.filter(c=>c.type==='checkbox' && (selector==='input:checked'?c.checked:!c.disabled));
  }
}
const elements=Object.fromEntries(['organisation-frameworks','organisation-default','organisation-note','select-all','deselect-all'].map(k=>[k,new Element()]));
const comparisons=[];
const frameworks=[{slug:"keen"},{slug:"iso_machine"}];
const ctx=vm.createContext({$:id=>elements[id],document:{createElement:()=>new Element()},Option:class{constructor(text,value){this.text=text;this.value=value}},selectionVersion:0,frameworks,comparison:{open:(slug,catalog)=>comparisons.push({slug,catalog})}});
vm.runInContext(fn,ctx);
const data={version:2,default:'keen',items:[{slug:'keen',name:'Keen Assurance Framework',enabled:true,allowed:true},{slug:'iso_machine',name:'ISO 27001:2022',enabled:false,allowed:true},{slug:'restricted',name:'Restricted',enabled:false,allowed:false}]};
ctx.renderOrganisationFrameworks(data);
const compare=elements['organisation-frameworks'].children[0].children[1];
assert.equal(compare.attributes['aria-label'],'Compare Keen Assurance Framework');
assert.equal(compare.dataset.compareFramework,'keen');
compare.onclick();
assert.equal(comparisons[0].slug,'keen');
assert.equal(comparisons[0].catalog,frameworks);
assert.equal(elements['organisation-frameworks'].querySelectorAll('input:checked').length,1);
assert.equal(elements['organisation-default'].options[0].text,'Keen Assurance Framework');
elements['select-all'].onclick();
assert.equal(elements['organisation-frameworks'].querySelectorAll('input:checked').length,2);
assert.equal(elements['organisation-default'].value,'keen');
assert.equal(elements['organisation-default'].options[1].text,'ISO 27001:2022');
elements['deselect-all'].onclick();assert.equal(elements['organisation-default'].options.length,0);assert.equal(elements['organisation-default'].disabled,true);
elements['select-all'].onclick();assert.equal(elements['organisation-default'].disabled,false);
ctx.renderOrganisationFrameworks({...data,version:3,default:'iso_machine',items:data.items.map(x=>({...x,enabled:x.slug==='iso_machine'}))});
elements['select-all'].onclick();assert.equal(elements['organisation-default'].value,'iso_machine');
console.log('Bulk selection, restrictions, labels, empty/default state, and reload handlers passed.');
