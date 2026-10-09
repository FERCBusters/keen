import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {observedPayloadFields,fieldLabel,fieldChoiceGroups} from '../public/pages/evidence-fields.js';

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
 const select={children:[],value:selected,replaceChildren(...items){this.children=items;},append(item){this.children.push(item);},get options(){return this.children.flatMap(item=>item.children||[item]);}};
 const row={fieldInputs:{path:select,op:{value:'equals'},value:{value:'ossec'}}};
 const elements={'sample':{value:''},'field-paths':{replaceChildren(){},append(){}},'field-rows':{children:[row]}};
 const context=vm.createContext({draftEventFields:observedPayloadFields({fields:{'ossec.rule.id':'31101'}}),samples:[],fieldLabel,fieldChoiceGroups,document:{createElement(){return {children:[],append(item){this.children.push(item);}};}},
  $:id=>elements[id],Option:function(label,value){this.label=label;this.value=value;}});
 vm.runInContext(source.slice(source.indexOf('function currentEventFields()'),source.indexOf('function addFieldCondition(')),context);
 vm.runInContext('refreshFieldChoices()',context);
 context.samples=[{fields:[{path:['fields','service.name'],value:'sshd'}]}];
 vm.runInContext('refreshFieldChoices()',context);
 assert.equal(select.children[1].label,'Fields in this event');
 elements.sample.value='0';
 vm.runInContext('refreshFieldChoices()',context);
 assert.equal(select.children[1].children[0].value,'["fields","service.name"]');
 assert.equal(select.children[2].label,'Fields seen in other samples');
 assert.equal(select.value,selected);
 assert.equal(row.fieldInputs.value.value,'ossec');
 assert.equal(row.fieldInputs.op.value,'equals');
 assert.equal(select.options.filter(o=>o.value==='["fields","ossec.rule.id"]').length,1);
 assert.ok(select.options.some(o=>o.value==='["fields","service.name"]'));
});


test('groups distinguish observed, predefined and saved fields without duplicates',()=>{
 const current=[{path:['fields','ossec.rule.id']}];
 const groups=fieldChoiceGroups(current,[...current,{path:['fields','process.name']}],['legacy']);
 assert.deepEqual(groups.map(g=>g.label),['Fields in this event','Fields seen in other samples','Predefined fields (not observed)','Selected field (not observed)']);
 const keys=groups.flatMap(g=>g.items.map(i=>JSON.stringify(i.path)));
 assert.equal(keys.length,new Set(keys).size);
 assert.equal(groups[0].items[0].label,'fields["ossec.rule.id"] — Ossec Rule Id');
 assert.ok(!fieldChoiceGroups([],[]).some(g=>g.label==='Fields in this event'));
});
