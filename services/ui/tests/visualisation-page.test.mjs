import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {page,settle} from './support/page-fixture.mjs';
const d3=await readFile(new URL('./node_modules/d3/dist/d3.min.js',import.meta.url),'utf8');
const dataset={nodes:[{id:'s1',type:'source',label:'Agent',source:'keen-agent',color:'#123456',mapped_events:5},{id:'c1',type:'control',label:'A1',control_id:'c1',title:'Access',mapped_events:5}],links:[{source:'s1',target:'c1',value:5}]};
async function fixture(t,mode,options={}){
 const ui=await page(t,'visualisation',{htmlName:'events',scripts:[d3],query:'&mode='+mode,...options,
  setup(window){window.document.getElementById('embeddedVisualisationsPane').classList.add('active');window.document.querySelector('[data-keen-visualisation-mount]').dataset.vizModes='all';},
  get:async u=>{
   if(options.fail)throw new Error('Offline');
   if(u.pathname.startsWith('/api/v1/graph/'))return options.empty?{nodes:[],links:[]}:dataset;
   if(u.pathname==='/api/v1/stats/controls')return {items:[{id:'c1',ref:'A1',mapped_events:5},{id:'c2',ref:'A2',mapped_events:0}]};
   throw new Error('Unexpected GET '+u);
  },patch:async(u,b)=>{assert.equal(u.pathname,'/api/v1/me/preferences');return b;}});
 await settle(()=>/Loaded\.|Failed to load/.test(ui.el('status').textContent));
 return ui;
}
for(const mode of ['graph','heatmap','sunburst_source','sunburst_control','graph_clause_events','heatmap_clause_events','graph_clause_control'])test(mode+' renders real SVG from relationship data',async t=>{
 const ui=await fixture(t,mode);
 assert.equal(ui.el('status').textContent,'Loaded.');
 assert.ok(ui.el('viz').querySelector('path,rect,circle'));
 assert.ok(ui.el('viz').textContent.includes('A1')||ui.el('viz').textContent.includes('Agent'));
 assert.equal(ui.el('vizSelect').value,mode);
 assert.ok(ui.calls.filter(c=>c.url.startsWith('/api/v1/graph/')).every(c=>c.url.includes('framework=A')));
});
test('visualisation fetch failure exposes an error rather than stale relationships',async t=>{
 const ui=await fixture(t,'graph',{fail:true});assert.match(ui.el('status').textContent,/Offline/);
});
test('visualisation graph threshold hides weak links without re-fetching',async t=>{
 const ui=await fixture(t,'graph');const before=ui.calls.length;
 ui.el('graphMinEdge').value='6';ui.el('graphApply').click();
 assert.equal(ui.el('viz').querySelectorAll('.links path').length,0);assert.equal(ui.calls.length,before);
});
test('switching graph to heatmap reuses the existing dataset',async t=>{
 const ui=await fixture(t,'graph');const before=ui.calls.length;
 ui.el('vizSelect').value='heatmap';ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
 await settle(()=>ui.el('viz').querySelector('rect'));
 assert.equal(ui.calls.length,before);
});
