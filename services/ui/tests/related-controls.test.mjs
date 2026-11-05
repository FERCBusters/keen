import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';

async function fixture(t, isAdmin = false) {
  const html = await readFile(new URL('../public/control.html', import.meta.url), 'utf8');
  const ui = await loadUi('pages/related-controls.js', {
    html,
    vendor: {
      withFramework: (path, framework) => `${path}&framework=${encodeURIComponent(framework)}`,
    },
  });
  t.after(ui.close);
  return {
    ...ui,
    browser: ui.api.createRelatedControlsBrowser(ui.document, { isAdmin }),
    el: (id) => ui.document.getElementById(id),
    choose(value) {
      const select = ui.document.getElementById('crossFrameworkView');
      select.value = value;
      select.dispatchEvent(new ui.window.Event('change'));
    },
  };
}
const link = (i, framework = 'A', derived = false) => ({
  id: `link-${i}`,
  source_control_id: `control-${i}`,
  source_framework: framework,
  source_ref: `${framework}${i}`,
  source_title: `Control ${i}`,
  rationale: 'Evidence overlap',
  derived,
});

test('initial view stays compact and selects only frameworks with links', async (t) => {
  const ui = await fixture(t);
  ui.browser.update(
    [link(1), link(2), link(3, 'B')],
    [
      { slug: 'A', name: 'Alpha' },
      { slug: 'B', name: 'Beta' },
      { slug: 'C', name: 'Unrelated' },
    ],
  );
  assert.equal(ui.el('crossFrameworkLinks').querySelectorAll('a').length, 0);
  assert.deepEqual(
    [...ui.el('crossFrameworkView').options].map((o) => o.textContent),
    ['Choose framework', 'Alpha (2)', 'Beta (1)'],
  );
  ui.choose('B');
  assert.equal(ui.el('crossFrameworkLinks').querySelectorAll('a').length, 1);
  assert.match(ui.el('crossFrameworkLinks').textContent, /B3/);
  assert.equal(ui.el('crossFrameworkLinks').querySelector('details').open, false);
  assert.equal(ui.el('crossFrameworkLinks').querySelector('button'), null);
});

test('large frameworks paginate and preserve selection after refresh/removal', async (t) => {
  const ui = await fixture(t, true);
  const links = Array.from({ length: 12 }, (_, i) => link(i));
  ui.browser.update(links);
  ui.choose('A');
  assert.equal(ui.el('crossFrameworkLinks').querySelectorAll('a').length, 10);
  ui.el('crossFrameworkNext').click();
  assert.equal(ui.el('crossFrameworkLinks').querySelectorAll('a').length, 2);
  assert.equal(ui.el('crossFrameworkNext').disabled, true);
  ui.browser.update(links.slice(0, 10));
  assert.equal(ui.el('crossFrameworkView').value, 'A');
  assert.equal(ui.el('crossFrameworkPager').hidden, true);
  assert.match(ui.el('crossFrameworkSummary').textContent, /1-10 of 10/);
  ui.browser.update([link(1, 'B')]);
  assert.equal(ui.el('crossFrameworkView').value, '');
  assert.equal(ui.el('crossFrameworkLinks').querySelectorAll('a').length, 0);
});

test('only direct admin links can be removed and untrusted text is escaped', async (t) => {
  const ui = await fixture(t, true);
  ui.browser.update([
    {
      ...link(1),
      source_title: '<img src=x onerror=alert(1)>',
      rationale: '<script>bad()</script>',
    },
    link(2, 'A', true),
  ]);
  ui.choose('A');
  assert.equal(ui.el('crossFrameworkLinks').querySelectorAll('[data-remove-cross-link]').length, 1);
  assert.equal(ui.el('crossFrameworkLinks').querySelector('img,script'), null);
  assert.match(ui.el('crossFrameworkLinks').textContent, /<script>/);
  ui.browser.showError('Unavailable');
  assert.equal(ui.el('crossFrameworkBrowser').hidden, true);
  assert.equal(ui.el('crossFrameworkLinks').textContent, 'Unavailable');
  ui.browser.update([]);
  assert.match(ui.el('crossFrameworkLinks').textContent, /No related controls/);
});

test('frameworks missing from the catalogue are named and ordered by slug', async (t) => {
  const ui = await fixture(t);
  ui.browser.update(
    [link(1, 'ZETA'), link(2, 'BETA'), link(3, 'A')],
    [{ slug: 'A', name: 'Delta' }],
  );
  assert.deepEqual(
    [...ui.el('crossFrameworkView').options].map((o) => o.textContent),
    ['Choose framework', 'BETA (1)', 'Delta (1)', 'ZETA (1)'],
  );
});

test('Previous returns to the earlier page of related controls', async (t) => {
  const ui = await fixture(t);
  ui.browser.update(Array.from({ length: 12 }, (_, i) => link(i)));
  ui.choose('A');
  ui.el('crossFrameworkNext').click();
  assert.equal(ui.el('crossFrameworkPrevious').disabled, false);
  assert.equal(ui.el('crossFrameworkPage').textContent, 'Page 2 of 2');
  ui.el('crossFrameworkPrevious').click();
  assert.equal(ui.el('crossFrameworkLinks').querySelectorAll('a').length, 10);
  assert.equal(ui.el('crossFrameworkPage').textContent, 'Page 1 of 2');
  assert.equal(ui.el('crossFrameworkPrevious').disabled, true);
  assert.match(ui.el('crossFrameworkSummary').textContent, /1-10 of 12/);
});
