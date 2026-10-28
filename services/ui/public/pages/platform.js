// Device platforms as an agent's own key reports them. Only these fixed values
// are rendered, so no reported text reaches the page.
const platforms = {
  windows: { icon: 'bi-windows', label: 'Windows' },
  darwin: { icon: 'bi-apple', label: 'macOS' },
  linux: { icon: 'bi-tux', label: 'Linux' },
};

export function platformLabel(value) {
  return Object.hasOwn(platforms, value) ? platforms[value].label : '';
}

export function platformIcon(value) {
  if (!Object.hasOwn(platforms, value)) return '';
  const p = platforms[value];
  return `<i class="bi ${p.icon} platform-icon" aria-hidden="true" title="${p.label}"></i><span class="visually-hidden">${p.label}</span>`;
}

// Remembers which collapsible items are open across re-renders of one page.
export function openState() {
  const open = new Set();
  return {
    apply(details, id, fallback) {
      details.open = open.has(id) || (fallback && !open.has('!' + id));
      details.addEventListener('toggle', () => {
        open.delete(id);
        open.delete('!' + id);
        open.add(details.open ? id : '!' + id);
      });
    },
  };
}
