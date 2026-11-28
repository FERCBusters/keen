// The stand-in for the design system's related-item cards keeps the real
// builder's promises, so a page test cannot pass on something the real one
// would refuse: text stays text, markup is a DOM node, bad links link nowhere.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { relatedCardStandIn } from './support/related-cards.mjs';

const HOSTILE = '<img src=x onerror="alert(1)">';

test('the stand-in writes text as text and refuses markup given as a string', () => {
  const { window } = new JSDOM('<main></main>', { url: 'https://keen.example/' });
  const { relatedCard, relatedCards, updateRelatedCard } = relatedCardStandIn(window);
  const card = relatedCard({
    id: 'x" onmouseover="alert(1)',
    title: HOSTILE,
    description: HOSTILE,
    count: HOSTILE,
    action: { label: HOSTILE, href: 'javascript:alert(1)' },
    table: {
      columns: [HOSTILE, { label: 'W', width: '1px;background:url(//evil.example)' }],
      rows: [
        [HOSTILE, { text: HOSTILE, href: 'javascript:alert(1)', detail: HOSTILE }],
        ['x', { text: 'y', href: '//evil.example' }],
        ['x', { text: 'ok', href: '/risk.html?id=1', newTab: true }],
      ],
    },
  });
  assert.equal(card.querySelector('img, script'), null);
  assert.equal(card.id, '');
  assert.equal(card.querySelector('.mosp-related-card-title').textContent, HOSTILE);
  assert.deepEqual(
    [...card.querySelectorAll('a')].map((a) => [a.getAttribute('href'), a.target]),
    [['/risk.html?id=1', '_blank']],
  );
  assert.equal(card.querySelectorAll('span.mosp-related-link').length, 2);
  assert.equal(card.querySelectorAll('th')[1].style.width, '');
  assert.ok(card.classList.contains('mosp-related-card-wide'));

  const refused = /must be a DOM node/;
  assert.throws(() => relatedCard({ body: HOSTILE }), refused);
  assert.throws(() => updateRelatedCard(card, { body: '<b>x</b>' }), refused);
  assert.throws(() => relatedCard({ links: [{ text: 'x', action: '<button>' }] }), refused);
  assert.throws(() => relatedCards(['<section>']), refused);
  assert.throws(() => relatedCards([], '#host'), refused);

  // A list, an empty card and a group, as the pages use them.
  const button = window.document.createElement('button');
  const listed = relatedCard({ links: [{ text: HOSTILE, href: 'data:x', action: button }] });
  assert.equal(listed.querySelector('img'), null);
  assert.equal(listed.querySelector('.mosp-related-list-item').lastChild, button);
  assert.equal(
    relatedCard().querySelector('.mosp-related-empty').textContent,
    'Nothing is linked yet.',
  );
  updateRelatedCard(card, { count: null, table: { columns: ['A'], rows: [] }, empty: 'None.' });
  assert.equal(card.querySelector('.mosp-related-card-count').hidden, true);
  assert.ok(!card.classList.contains('mosp-related-card-wide'));
  const host = window.document.querySelector('main');
  assert.equal(relatedCards([card, listed], host), host);
  assert.deepEqual([...host.children], [card, listed]);
  assert.ok(relatedCards().classList.contains('mosp-related-cards'));
});
