'use strict';
const input=document.getElementById('search'),results=document.getElementById('results');
input.addEventListener('input',()=>{
 results.replaceChildren();const query=input.value.trim().toLowerCase();if(query.length<2)return;
 const words=query.split(/\s+/),hits=(window.KEEN_DOC_INDEX||[]).filter(item=>words.every(word=>(item.title+' '+item.text).toLowerCase().includes(word))).slice(0,12);
 const note=document.createElement('p');note.textContent=hits.length?'Matching sections:':'No matching sections. Try fewer words.';results.append(note);
 for(const hit of hits){const a=document.createElement('a');a.href=hit.url;a.textContent=hit.title;results.append(a);}
});
