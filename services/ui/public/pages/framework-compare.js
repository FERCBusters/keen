import { apiGet, esc, errorText } from '/app.js';

const FIELDS = [
  ['title', 'Title'],
  ['description', 'Description'],
  ['parent_ref', 'Parent reference'],
  ['clause_refs', 'Clause links'],
  ['in_scope', 'Scope'],
  ['sort_order', 'Order'],
  ['upstream_url', 'Upstream URL'],
];
const DETAILS = [
  ['name', 'Name'],
  ['version', 'Version'],
  ['description', 'Description'],
  ['upstream_url', 'Upstream URL'],
];
const PAGE_SIZE = 50;
const key = (node) => JSON.stringify([node.kind, node.ref]);

function value(node, field) {
  if (!node) return '';
  if (field === 'clause_refs') return [...new Set(node[field] || [])].sort().join(', ');
  if (field === 'in_scope') return node[field] === false ? 'Out of scope' : 'In scope';
  if (field === 'sort_order') return String(node[field] ?? 0);
  return String(node[field] ?? '').replaceAll('\r\n', '\n');
}

export function compareNodes(before, after) {
  const old = new Map(before.map((node) => [key(node), node]));
  const next = new Map(after.map((node) => [key(node), node]));
  return [...new Set([...old.keys(), ...next.keys()])]
    .map((id) => {
      const left = old.get(id),
        right = next.get(id),
        node = right || left;
      const fields = FIELDS.filter(([field]) => value(left, field) !== value(right, field));
      return {
        left,
        right,
        kind: node.kind,
        ref: node.ref,
        fields,
        status: !left ? 'added' : !right ? 'removed' : fields.length ? 'changed' : 'unchanged',
      };
    })
    .sort(
      (a, b) =>
        String(a.kind).localeCompare(String(b.kind)) ||
        String(a.ref).localeCompare(String(b.ref), undefined, { numeric: true }),
    );
}

// A linear prefix/suffix diff keeps large imported descriptions inexpensive.
// Multiple edits in the middle appear together as one replaced passage.
function textPair(left, right) {
  if (left === right) return [esc(left || '—'), esc(right || '—')];
  const a = left.split(/(\s+)/),
    b = right.split(/(\s+)/);
  let start = 0,
    end = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  while (
    end < a.length - start &&
    end < b.length - start &&
    a[a.length - 1 - end] === b[b.length - 1 - end]
  )
    end++;
  const mark = (tokens, tag, tone) => {
    const middle = tokens.slice(start, end ? -end : undefined).join('');
    return (
      esc(tokens.slice(0, start).join('')) +
      (middle
        ? `<${tag} class="text-bg-${tone}">${esc(middle)}</${tag}>`
        : tokens.join('')
          ? ''
          : '—') +
      esc(end ? tokens.slice(-end).join('') : '')
    );
  };
  return [mark(a, 'del', 'danger'), mark(b, 'ins', 'success')];
}

function fieldsTable(left, right, fields, beforeName, afterName) {
  return `<div class="table-responsive"><table class="table table-sm align-top"><thead><tr><th scope="col">Field</th><th scope="col">Before · ${esc(beforeName)}</th><th scope="col">After · ${esc(afterName)}</th></tr></thead><tbody>${fields
    .map(([field, label]) => {
      const [old, next] = textPair(value(left, field), value(right, field));
      return `<tr><th scope="row">${esc(label)}</th><td class="wrap text-break" style="white-space:pre-wrap;min-width:16rem;width:42%">${old}</td><td class="wrap text-break" style="white-space:pre-wrap;min-width:16rem;width:42%">${next}</td></tr>`;
    })
    .join('')}</tbody></table></div>`;
}

export function frameworkComparison(host) {
  let generation = 0,
    catalog = [],
    before,
    after,
    rows = [],
    offset = 0;
  const find = (selector) => host.querySelector(selector);

  function clearResult() {
    generation++;
    rows = [];
    offset = 0;
    find('[data-results]').replaceChildren();
    find('[data-feedback]').textContent = '';
    find('[data-run]').disabled = false;
  }

  function render() {
    const term = find('[data-search]').value.trim().toLocaleLowerCase();
    const unchanged = find('[data-unchanged]').checked;
    const visible = rows.filter(
      (row) =>
        (unchanged || row.status !== 'unchanged') &&
        [
          row.kind,
          row.ref,
          row.left?.title,
          row.right?.title,
          row.left?.description,
          row.right?.description,
        ].some((v) =>
          String(v || '')
            .toLocaleLowerCase()
            .includes(term),
        ),
    );
    const counts = Object.fromEntries(
      ['added', 'removed', 'changed', 'unchanged'].map((status) => [
        status,
        rows.filter((row) => row.status === status).length,
      ]),
    );
    const metadata = DETAILS.filter(([field]) => value(before, field) !== value(after, field));
    const result = find('[data-results]');
    result.innerHTML = `<p role="status"><strong>+ ${counts.added} added · − ${counts.removed} removed · ~ ${counts.changed} changed · = ${counts.unchanged} unchanged</strong></p>${metadata.length ? `<details class="mb-3"><summary>Framework details · ${metadata.length} changed fields</summary>${fieldsTable(before, after, metadata, before.name, after.name)}</details>` : ''}<div data-diff-rows></div><div class="d-flex align-items-center gap-2 mt-3"><button type="button" class="btn btn-sm btn-outline-secondary" data-prev ${offset === 0 ? 'disabled' : ''}>Previous</button><span>${visible.length ? offset + 1 : 0}–${Math.min(offset + PAGE_SIZE, visible.length)} of ${visible.length} matching entries</span><button type="button" class="btn btn-sm btn-outline-secondary" data-next ${offset + PAGE_SIZE >= visible.length ? 'disabled' : ''}>Next</button></div>`;
    const list = find('[data-diff-rows]');
    if (!visible.length)
      list.textContent =
        rows.some((row) => row.status !== 'unchanged') || term
          ? 'No entries match these filters.'
          : 'No clause or control differences. Enable “Show unchanged” to browse matching entries.';
    for (const row of visible.slice(offset, offset + PAGE_SIZE)) {
      const tone = {
        added: 'success',
        removed: 'danger',
        changed: 'warning',
        unchanged: 'secondary',
      }[row.status];
      const symbol = { added: '+', removed: '−', changed: '~', unchanged: '=' }[row.status];
      const item = document.createElement('article');
      item.className = `card border-${tone} mb-3`;
      item.dataset.diffStatus = row.status;
      const fields =
        row.status === 'changed'
          ? row.fields
          : FIELDS.filter(([field]) => value(row.left, field) || value(row.right, field));
      item.innerHTML = `<div class="card-header"><span class="badge text-bg-${tone} me-2">${symbol} ${row.status}</span><strong>${esc(row.kind)} ${esc(row.ref)}</strong> · ${esc((row.right || row.left).title || '')}</div><div class="card-body">${fieldsTable(row.left, row.right, fields, before.name, after.name)}</div>`;
      list.append(item);
    }
    find('[data-prev]').onclick = () => {
      offset = Math.max(0, offset - PAGE_SIZE);
      render();
    };
    find('[data-next]').onclick = () => {
      offset += PAGE_SIZE;
      render();
    };
  }

  async function run(event) {
    event.preventDefault();
    clearResult();
    before = catalog.find((item) => item.slug === find('[data-before]').value);
    after = catalog.find((item) => item.slug === find('[data-after]').value);
    if (!before || !after || before.slug === after.slug) {
      find('[data-feedback]').textContent = 'Choose two different frameworks.';
      return;
    }
    const request = generation;
    find('[data-run]').disabled = true;
    find('[data-feedback]').textContent = 'Loading framework contents…';
    try {
      const [old, next] = await Promise.all(
        [before, after].map((item) =>
          apiGet(`/api/v1/admin/frameworks/${encodeURIComponent(item.slug)}/nodes`),
        ),
      );
      if (request !== generation) return;
      rows = compareNodes(old.items || [], next.items || []);
      find('[data-feedback]').textContent = '';
      render();
    } catch (error) {
      if (request !== generation) return;
      find('[data-feedback]').textContent =
        `Comparison failed: ${errorText(error)}. Select Compare to retry.`;
    } finally {
      if (request === generation) find('[data-run]').disabled = false;
    }
  }

  return {
    open(first, frameworks) {
      generation++;
      catalog = frameworks;
      rows = [];
      offset = 0;
      host.hidden = false;
      host.innerHTML = `<div class="card mb-3"><div class="card-header d-flex justify-content-between align-items-center"><h3 class="h5 mb-0">Compare frameworks</h3><button type="button" class="btn btn-sm btn-outline-secondary" data-close>Close comparison</button></div><div class="card-body"><p>Choose the earlier framework on the left and the later framework on the right. Entries match by category and exact reference. Renumbered entries appear as removals and additions; no semantic equivalence is inferred.</p><form data-compare-form><div class="row g-2 align-items-end"><div class="col-md-5"><label for="fc-before" class="form-label">Before</label><select id="fc-before" class="form-select" data-before></select></div><div class="col-md-5"><label for="fc-after" class="form-label">After</label><select id="fc-after" class="form-select" data-after></select></div><div class="col-md-2 d-flex gap-2"><button type="submit" class="btn btn-primary" data-run>Compare</button><button type="button" class="btn btn-outline-secondary" data-swap>Swap</button></div></div></form><p class="small-muted mt-2">Compare saved framework text, hierarchy, clause links, scope and ordering. Evidence, mappings and compliance results are outside this comparison. Red − removes, green + adds, amber ~ changes.</p><div class="d-flex flex-wrap gap-3 my-3"><label class="form-check"><input class="form-check-input" type="checkbox" data-unchanged> Show unchanged</label><label>Filter entries <input class="form-control form-control-sm" type="search" data-search placeholder="Reference, title or description"></label></div><p role="status" data-feedback></p><div data-results></div></div></div>`;
      for (const selector of ['[data-before]', '[data-after]']) {
        for (const item of catalog)
          find(selector).add(new Option(`${item.name} (${item.slug})`, item.slug));
        find(selector).addEventListener('change', clearResult);
      }
      find('[data-before]').value = first;
      find('[data-after]').value = catalog.find((item) => item.slug !== first)?.slug || first;
      find('[data-compare-form]').addEventListener('submit', run);
      find('[data-swap]').onclick = () => {
        const old = find('[data-before]').value;
        find('[data-before]').value = find('[data-after]').value;
        find('[data-after]').value = old;
        clearResult();
      };
      for (const selector of ['[data-search]', '[data-unchanged]'])
        find(selector).addEventListener('input', () => {
          offset = 0;
          if (before && after && find('[data-results]').children.length) render();
        });
      find('[data-close]').onclick = () => {
        generation++;
        host.hidden = true;
        host.replaceChildren();
      };
      // Finish positioning before a native select popup can open. Animated
      // scrolling can dismiss the popup as its anchor moves.
      host.scrollIntoView({ block: 'start', behavior: 'instant' });
      find('[data-after]').focus({ preventScroll: true });
    },
  };
}
