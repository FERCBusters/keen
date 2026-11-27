// The badges in an "Affects" column: a long name is cut short so one vendor
// or asset does not widen a whole register, and stays readable on hover.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const LONG = 'Akamai Cloud (Linode) account · Miguel Example (A85608A1-20FE-11EA)';

async function badges(t, items) {
  const ui = await loadUi('pages/affected-common.js', {
    html: '<main></main>',
    url: 'https://keen.example/risks.html',
    vendor: { withFramework: (path, framework) => `${path}&framework=${framework}` },
  });
  t.after(ui.close);
  return {
    ui,
    made: ui.api.affectedBadges(items, 'A'),
    html: ui.api.affectedBadgesHtml(items, 'A'),
  };
}

test('a long name is cut to 32 characters, shown in full on hover, and still links', async (t) => {
  const { ui, made } = await badges(t, [
    { kind: 'asset', id: 'a1', label: LONG, detail: '', href: '/isms-asset.html?id=a1' },
    {
      kind: 'vendor',
      id: 'v1',
      label: LONG,
      detail: 'Hosting <b>supplier</b>',
      href: '/vendor.html?id=v1',
    },
  ]);
  assert.equal(ui.api.AFFECTED_LABEL_LIMIT, 32);
  const [asset, vendor] = made;
  const shown = asset.lastChild.textContent;
  assert.equal(shown, 'Akamai Cloud (Linode) account ·…');
  assert.equal(shown.length, 32);
  assert.equal(asset.title, LONG);
  assert.equal(asset.tagName, 'A');
  assert.equal(asset.getAttribute('href'), '/isms-asset.html?id=a1&framework=A');
  // The kind is still named for assistive technology, before the short name.
  assert.equal(asset.textContent, `Asset: ${shown}`);
  // With a detail, the hover gives the full name and then the detail, as text.
  assert.equal(vendor.title, `${LONG} · Hosting <b>supplier</b>`);
  assert.equal(vendor.querySelector('b'), null);
});

test('a name of 32 characters or fewer is shown whole, with only its detail on hover', async (t) => {
  const exact = 'x'.repeat(32);
  const { made, html } = await badges(t, [
    { kind: 'person', id: 'p1', label: exact, detail: 'DPO', href: '/person.html?id=p1' },
    { kind: 'person', id: 'p2', label: 'Dana', detail: '', href: '' },
    { kind: 'vendor', id: 'v2', label: `${'y'.repeat(30)}  zz`, detail: '' },
    { kind: 'asset', id: 'a2', label: null, detail: '' },
  ]);
  assert.equal(made[0].lastChild.textContent, exact);
  assert.equal(made[0].title, 'DPO');
  // No link and nothing to add: a plain badge without a hover text.
  assert.equal(made[1].tagName, 'SPAN');
  assert.equal(made[1].hasAttribute('title'), false);
  assert.equal(made[1].lastChild.textContent, 'Dana');
  // A cut that would end on a space ends on the letter before it.
  assert.equal(made[2].lastChild.textContent, `${'y'.repeat(30)}…`);
  assert.equal(made[3].lastChild.textContent, '');
  assert.match(html, /^<a class="badge badge-soft me-1 text-decoration-none"/);
});

test('nothing affected shows a dash', async (t) => {
  const { made } = await badges(t, null);
  assert.equal(made.length, 1);
  assert.equal(made[0].textContent, '-');
});
