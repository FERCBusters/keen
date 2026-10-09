import test from 'node:test';
import assert from 'node:assert/strict';
import {loadUi} from './support/load-ui.mjs';
async function fixture(t,config={}){
 const ui=await loadUi('pages/filter-pill-multiselect.js',{html:'<label for="filter">Source</label><select id="filter"><option value="">All</option><option value="ossec">OSSEC</option><option value="journal">Journal</option></select>'});
 t.after(()=>ui.close());
 const select=ui.document.getElementById('filter');
 const instance=ui.api.enhanceFilterPillMultiSelect(select,config);
 return {...ui,select,instance};
}
test('initial all-values state has no selected filters',async t=>{
 const {document,api,select}=await fixture(t);
 assert.deepEqual(Array.from(api.selectedFilterPillValues(select)),[]);
 assert.match(document.querySelector('.keen-filter-pill-summary').textContent,/All/);
 assert.equal(document.querySelectorAll('.keen-filter-pill-picker').length,1);
 api.enhanceFilterPillMultiSelect(select);
 assert.equal(document.querySelectorAll('.keen-filter-pill-picker').length,1);
});
test('clicking values selects and deselects them and emits one change per edit',async t=>{
 const {document,select,instance}=await fixture(t);let changes=0;select.addEventListener('change',()=>changes++);
 const click=()=>document.querySelector('.keen-filter-pill-grid [data-value="ossec"]').click();
 click();assert.deepEqual(Array.from(instance.getValues()),['ossec']);assert.equal(changes,1);
 click();assert.deepEqual(Array.from(instance.getValues()),[]);assert.equal(changes,2);
});
test('removing a selected chip updates backing filter',async t=>{
 const {document,select,instance}=await fixture(t,{selectedValues:['ossec','journal']});
 document.querySelector('.keen-filter-pill-inline [data-remove-value="ossec"]').click();
 assert.deepEqual(Array.from(instance.getValues()),['journal']);assert.equal(select.dataset.filterPillValue,'journal');
});
test('clear all resets selection and restores all-values summary',async t=>{
 const {document,instance}=await fixture(t,{selectedValues:['ossec','journal']});
 document.querySelector('.keen-filter-pill-modal-clear').click();
 assert.deepEqual(Array.from(instance.getValues()),[]);
 assert.match(document.querySelector('.keen-filter-pill-summary').textContent,/All/);
});
test('search is case insensitive and does not discard hidden selections',async t=>{
 const {document,window,instance}=await fixture(t,{selectedValues:['ossec']});
 const search=document.querySelector('.keen-filter-pill-search');search.value='JOUR';search.dispatchEvent(new window.Event('input'));
 assert.equal(document.querySelectorAll('.keen-filter-pill-grid [data-value]').length,1);
 assert.match(document.querySelector('.keen-filter-pill-grid').textContent,/Journal/);
 assert.deepEqual(Array.from(instance.getValues()),['ossec']);
 search.value='nothing';search.dispatchEvent(new window.Event('input'));
 assert.match(document.querySelector('.keen-filter-pill-grid').textContent,/No values match/);
});
test('refreshing options prunes unavailable choices unless preservation requested',async t=>{
 const {instance}=await fixture(t,{selectedValues:['ossec','journal']});
 instance.setOptions(['journal']);assert.deepEqual(Array.from(instance.getValues()),['journal']);
 instance.setSelected(['old','old',' journal '],{pruneSelected:false});
 assert.deepEqual(Array.from(instance.getValues()),['old','journal']);
 instance.setOptions(['ossec'],{pruneSelected:false});assert.deepEqual(Array.from(instance.getValues()),['old','journal']);
});
test('programmatic updates respect dispatchChange and do not fire on identical values',async t=>{
 const {instance,select}=await fixture(t);let changes=0;select.addEventListener('change',()=>changes++);
 instance.setSelected(['ossec']);assert.equal(changes,0);
 instance.setSelected(['journal'],{dispatchChange:true});assert.equal(changes,1);
 instance.setSelected(['journal'],{dispatchChange:true});assert.equal(changes,1);
});
test('untrusted labels, values and titles remain text',async t=>{
 const attack='<img src=x onerror=alert(1)>';
 const {document,instance}=await fixture(t,{options:[{value:attack,label:attack,title:'" onclick="alert(1)'}]});
 document.querySelector('.keen-filter-pill-grid [data-value]').click();
 assert.deepEqual(Array.from(instance.getValues()),[attack]);
 assert.equal(document.querySelector('img,[onclick],[onerror]'),null);
 assert.match(document.querySelector('.keen-filter-pill-inline').textContent,/<img/);
});
