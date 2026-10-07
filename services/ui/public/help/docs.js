'use strict';
const input=document.getElementById('search'),results=document.getElementById('results');
input.addEventListener('input',()=>{
 results.replaceChildren();const query=input.value.trim().toLowerCase();if(query.length<2)return;
 const words=query.split(/\s+/),hits=(window.KEEN_DOC_INDEX||[]).filter(item=>words.every(word=>(item.title+' '+item.text).toLowerCase().includes(word))).slice(0,12);
 const note=document.createElement('p');note.textContent=hits.length?'Matching sections:':'No matching sections. Try fewer words.';results.append(note);
 for(const hit of hits){const a=document.createElement('a');a.href=hit.url;a.textContent=hit.title;results.append(a);}
});

// Retain bookmarks from the earlier single-page handbooks.
(() => {
 const name = location.pathname.split('/').pop() || 'index.html';
 const links = window.KEEN_DOC_LINKS || {};
 function followBookmark() {
  let fragment;
  try { fragment = decodeURIComponent(location.hash); } catch (_) { return; }
  const destination = links[name + fragment] || links[name];
  if (destination && destination !== name + fragment) location.replace(destination);
 }
 followBookmark();
 window.addEventListener('hashchange', followBookmark);
})();

// Keep the handbook reachable without making small screens scroll past its contents.
if (window.matchMedia('(max-width: 850px)').matches) {
 const chapters = document.querySelector('.chapter-menu');
 if (chapters) chapters.open = false;
}
