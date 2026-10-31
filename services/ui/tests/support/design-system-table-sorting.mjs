// Click-to-sort table headers as the pinned design system implements them
// (FERCBusters/mosp-design-system 58c7da37, src/js/120-table-sorting.js).
// Every header without data-nosort becomes a sort button: a click or
// Enter/Space anywhere in it is cancelled and the body rows are reordered by
// that header's position, so each row needs one cell per header. Other UI tests
// stub enableTableSorting, so only tests using this double notice a header
// whose own controls stop working or a row whose cells sit under the wrong
// header. Visible day-first dates are left out: these pages sort by data-sort
// keys.
function sortValue(cell) {
  if (!cell) return { t: 'str', v: '~~~~' };
  const raw = cell.getAttribute('data-sort');
  const s = String(raw !== null && raw !== '' ? raw : cell.textContent || '').trim();
  if (!s) return { t: 'str', v: '' };
  if (/^-?\d+(?:\.\d+)?$/.test(s.replace(/,/g, ''))) return { t: 'num', v: parseFloat(s) };
  const d = Date.parse(s);
  if (!Number.isNaN(d) && /\d/.test(s)) return { t: 'date', v: d };
  return { t: 'str', v: s.toLowerCase() };
}

function sortTableByColumn(table, colIndex) {
  const ths = [...table.querySelectorAll('thead th')];
  const th = ths[colIndex];
  const dir = th.getAttribute('data-sort-dir') === 'asc' ? 'desc' : 'asc';
  for (const h of ths) {
    h.removeAttribute('data-sort-dir');
    h.classList.remove('sort-asc', 'sort-desc');
  }
  th.setAttribute('data-sort-dir', dir);
  th.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
  const tbody = table.querySelector('tbody');
  const rows = [...tbody.querySelectorAll('tr')];
  if (rows.length <= 1) return;
  const order = { num: 0, date: 1, str: 2 };
  const decorated = rows.map((tr, i) => ({ tr, i, key: sortValue(tr.children[colIndex]) }));
  decorated.sort((a, b) => {
    if (a.key.t !== b.key.t) return order[a.key.t] - order[b.key.t];
    const cmp = a.key.v < b.key.v ? -1 : a.key.v > b.key.v ? 1 : a.i - b.i;
    return dir === 'asc' ? cmp : -cmp;
  });
  for (const { tr } of decorated) tbody.appendChild(tr);
}

export function designSystemTableSorting(getWindow) {
  return {
    enableTableSorting(root = getWindow().document) {
      for (const table of root.querySelectorAll('table[data-sortable], table.sortable')) {
        if (!table.querySelector('thead') || !table.querySelector('tbody')) continue;
        [...table.querySelectorAll('thead th')].forEach((th, idx) => {
          if (th.hasAttribute('data-nosort')) return;
          th.classList.add('sortable');
          th.tabIndex = 0;
          th.setAttribute('role', 'button');
          const handler = (ev) => {
            ev.preventDefault();
            sortTableByColumn(table, idx);
          };
          th.addEventListener('click', handler);
          th.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') handler(ev);
          });
        });
      }
    },
  };
}
