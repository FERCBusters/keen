import {apiGet, apiPost, apiPut, apiDelete} from '/app.js';

const $ = id => document.getElementById(`fe-${id}`);
let frameworks = [];
let nodes = [];
let selected = null;
const message = value => { $('message').textContent = value; };
const key = node => `${node.kind}\0${node.ref}`;
const url = (kind, ref) => `/api/v1/admin/frameworks/${encodeURIComponent($('frameworks').value)}/nodes/${encodeURIComponent(kind)}/${encodeURIComponent(ref)}`;

async function refreshFrameworks(preferred) {
  const data = await apiGet('/api/v1/frameworks');
  frameworks = data.items || [];
  $('frameworks').replaceChildren();
  for (const fw of frameworks) $('frameworks').add(new Option(`${fw.name} (${fw.slug})`, fw.slug));
  $('frameworks').value = preferred || $('frameworks').value || data.default;
  await selectFramework();
}

async function selectFramework() {
  const fw = frameworks.find(item => item.slug === $('frameworks').value);
  if (!fw) return;
  $('slug').dataset.original = fw.slug;
  for (const [field, value] of Object.entries({slug: fw.slug, name: fw.name, version: fw.version || '', description: fw.description || '', url: fw.upstream_url || ''})) $(field).value = value;
  const data = await apiGet(`/api/v1/admin/frameworks/${encodeURIComponent(fw.slug)}/nodes`);
  nodes = data.items || [];
  selected = null;
  newNode();
  renderTree();
}

function newNode() {
  selected = null;
  for (const id of ['ref', 'title', 'node-description', 'node-url']) $(id).value = '';
  $('sort').value = '0';
  $('scope').checked = true;
  $('clauses').replaceChildren();
  updateChoices();
  message('');
}

function updateChoices() {
  const kind = $('kind').value;
  $('parent').replaceChildren(new Option('No parent', ''));
  for (const item of nodes.filter(item => item.kind === kind && (!selected || key(item) !== key(selected))))
    $('parent').add(new Option(`${item.ref} — ${item.title}`, item.ref));
  $('clauses').replaceChildren();
  $('clauses').disabled = kind === 'clause';
  for (const item of nodes.filter(item => item.kind === 'clause'))
    $('clauses').add(new Option(`${item.ref} — ${item.title}`, item.ref));
}

function editNode(item) {
  selected = item;
  $('kind').value = item.kind;
  if ($('kind').value !== item.kind) $('kind').add(new Option(item.kind, item.kind));
  $('ref').value = item.ref;
  $('title').value = item.title;
  $('node-description').value = item.description || '';
  $('node-url').value = item.upstream_url || '';
  $('sort').value = item.sort_order || 0;
  $('scope').checked = item.in_scope;
  updateChoices();
  $('parent').value = item.parent_ref || '';
  for (const option of $('clauses').options) option.selected = (item.clause_refs || []).includes(option.value);
  message('');
}

function renderTree() {
  const query = $('search').value.toLowerCase();
  const container = $('tree');
  container.replaceChildren();
  const ordered = [];
  const visited = new Set();
  function visit(item, depth) {
    if (visited.has(key(item))) return;
    visited.add(key(item));
    ordered.push({item, depth});
    for (const child of nodes.filter(n => n.kind === item.kind && n.parent_ref === item.ref).sort(compare)) visit(child, depth + 1);
  }
  for (const item of nodes.filter(n => !n.parent_ref || !nodes.some(p => p.kind === n.kind && p.ref === n.parent_ref)).sort(compare)) visit(item, 0);
  for (const item of nodes) visit(item, 0);
  for (const {item, depth} of ordered) {
    if (query && !`${item.ref} ${item.title} ${item.kind}`.toLowerCase().includes(query)) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'list-group-item list-group-item-action text-start';
    button.style.paddingLeft = `${12 + Math.min(depth, 8) * 18}px`;
    button.textContent = `${item.ref}  ${item.title} · ${item.kind}`;
    button.addEventListener('click', () => editNode(item));
    container.append(button);
  }
}
function compare(a, b) { return (a.sort_order || 0) - (b.sort_order || 0) || a.ref.localeCompare(b.ref, undefined, {numeric:true}); }

$('frameworks').addEventListener('change', () => selectFramework().catch(error => message(error.message)));
$('search').addEventListener('input', renderTree);
$('new').addEventListener('click', newNode);
$('kind').addEventListener('change', updateChoices);
$('new-framework').addEventListener('click', () => {
  for (const id of ['slug', 'name', 'version', 'description', 'url']) $(id).value = '';
  $('slug').dataset.original = '';
  message('Enter a new slug and save the framework before adding nodes');
});
$('save-framework').addEventListener('click', async () => {
  try {
    const slug = $('slug').value.trim();
    if (!slug) throw new Error('A framework slug is required');
    if (frameworks.some(f => f.slug === $('frameworks').value) && $('slug').dataset.original === $('frameworks').value && slug !== $('frameworks').value) throw new Error('Framework slugs cannot be renamed; use New framework');
    await apiPost('/api/v1/frameworks', {slug, name: $('name').value, version: $('version').value,
      description: $('description').value, upstream_url: $('url').value});
    await refreshFrameworks(slug);
    message('Framework saved');
  } catch (error) { message(error.message); }
});
$('save-node').addEventListener('click', async () => {
  try {
    const kind = $('kind').value, ref = $('ref').value.trim();
    if (!ref || !$('title').value.trim()) throw new Error('Reference and title are required');
    if (selected && (selected.ref !== ref || selected.kind !== kind)) throw new Error('Create a new node to change its reference or category');
    await apiPut(url(kind, ref), {kind, ref, title: $('title').value, description: $('node-description').value,
      upstream_url: $('node-url').value, parent_ref: $('parent').value || null,
      clause_refs: [...$('clauses').selectedOptions].map(option => option.value),
      in_scope: $('scope').checked, sort_order: Number($('sort').value || 0)});
    await selectFramework();
    const item = nodes.find(n => n.kind === kind && n.ref === ref);
    if (item) editNode(item);
    message('Node saved');
  } catch (error) { message(error.message); }
});
$('delete-node').addEventListener('click', async () => {
  if (!selected || !confirm(`Delete ${selected.kind} ${selected.ref}?`)) return;
  try {
    await apiDelete(url(selected.kind, selected.ref));
    await selectFramework();
    message('Node deleted');
  } catch (error) { message(error.message); }
});
refreshFrameworks().catch(error => message(error.message));
