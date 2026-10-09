import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {observedPayloadFields,fieldLabel} from '../public/pages/evidence-fields.js';

test('draft payload exposes OSSEC literal keys and other scalar fields',()=>{
 const fields=observedPayloadFields({fields:{'ossec.rule.id':'31101','ossec.rule.level':'5'},severity:3,flag:false,nested:{value:'yes'},nil:null,items:['no']});
 assert.deepEqual(fields,[
  {path:['fields','ossec.rule.id'],value:'31101'},
  {path:['fields','ossec.rule.level'],value:'5'},
  {path:['severity'],value:'3'}, {path:['flag'],value:'false'},
  {path:['nested','value'],value:'yes'}]);
});

test('all usable agent fields remain available beyond the sample discovery limit',()=>{
 const fields=Object.fromEntries(Array.from({length:128},(_,i)=>['key'+i,String(i)]));
 assert.equal(observedPayloadFields({fields}).length,128);
 assert.deepEqual(observedPayloadFields({tooLong:'x'.repeat(4097),array:[],nil:null}),[]);
});

test('loading samples refreshes existing choices and retains draft-only fields and selections',()=>{
 const source=readFileSync(new URL('../public/pages/evidence-config.js',import.meta.url),'utf8');
 const selected='["fields","parser"]';
 const select={options:[{value:selected}],value:selected,add(option){this.options.push(option);}};
 const row={fieldInputs:{path:select,op:{value:'equals'},value:{value:'ossec'}}};
 const elements={'field-paths':{replaceChildren(){},append(){}},'field-rows':{children:[row]}};
 const context=vm.createContext({draftEventFields:observedPayloadFields({fields:{'ossec.rule.id':'31101'}}),samples:[],fieldLabel,
  $:id=>elements[id],Option:function(label,value){this.label=label;this.value=value;}});
 vm.runInContext(source.slice(source.indexOf('function availableEventFields()'),source.indexOf('function addFieldCondition(')),context);
 vm.runInContext('refreshFieldChoices()',context);
 context.samples=[{fields:[{path:['fields','service.name'],value:'sshd'}]}];
 vm.runInContext('refreshFieldChoices()',context);
 assert.equal(select.value,selected);
 assert.equal(row.fieldInputs.value.value,'ossec');
 assert.equal(row.fieldInputs.op.value,'equals');
 assert.equal(select.options.filter(o=>o.value==='["fields","ossec.rule.id"]').length,1);
 assert.ok(select.options.some(o=>o.value==='["fields","service.name"]'));
});
