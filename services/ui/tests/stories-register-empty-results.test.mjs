// The story register when no story matches.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

test('a register with no matching stories says so and offers no paging', async (t) => {
  const searches = [];
  const ui = await page(t, 'stories', {
    me: { can_view_stories: true },
    query: '&q=%3Cb%3Eflood%3C%2Fb%3E&status=closed',
    get: async (url) => {
      searches.push(Object.fromEntries(url.searchParams));
      return { items: [], total: 0, limit: 50, offset: 0 };
    },
  });
  await settle(() => ui.el('meta').textContent !== 'Loading');
  assert.equal(ui.el('rows').textContent.trim(), 'No stories match the current filters.');
  assert.equal(ui.el('rows').querySelector('[data-story-id]'), null);
  assert.equal(ui.el('q').value, '<b>flood</b>');
  assert.deepEqual(searches, [{ q: '<b>flood</b>', status: 'closed', limit: '50', offset: '0' }]);
  assert.equal(ui.el('prev').disabled, true);
  assert.equal(ui.el('next').disabled, true);
});
