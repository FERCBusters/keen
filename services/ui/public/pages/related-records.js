// The Related items tab of a record that keeps its own relationships (a legal
// register item, a SWOT item): one card for each kind of record it relates
// to, and one for the stories that include it. Every card is the design
// system's related-item card, so the tab looks and reads the same for every
// kind of record.
import { node, relatedCard, relatedCards, updateRelatedCard } from '/app.js';
import { mountStoryRelated } from '/pages/story-related.js';

/**
 * Draw a record's relationships in `host`.
 *
 * - types: the kinds of record, in the order shown.
 * - label(type): the kind's name.
 * - links: the relationships, each { target_type, target_id, title }.
 * - href(link): where a related record opens; empty for nowhere.
 * - manage: whether each has an Unlink button. The button names its
 *   relationship in `data-unlink-type` and `data-unlink-id`, for the page.
 * - newTab: whether a related record opens in a new tab, as from a draft.
 * - stories: the card from storiesCard, shown after the others.
 */
export function renderRelatedRecords(
  host,
  { types, label, links, href, manage, newTab = false, stories = null },
) {
  const cards = types.map((type) => {
    const related = links.filter((link) => link.target_type === type);
    return relatedCard({
      title: label(type),
      count: related.length,
      links: related.map((link) => ({
        text: link.title,
        href: href(link),
        newTab,
        ...(manage
          ? {
              action: node(
                'button',
                {
                  type: 'button',
                  class: 'btn btn-sm btn-outline-danger flex-shrink-0',
                  'data-unlink-type': type,
                  'data-unlink-id': link.target_id,
                  'aria-label': `Remove ${link.title}`,
                },
                'Unlink',
              ),
            }
          : {}),
      })),
      empty: 'No linked records.',
    });
  });
  relatedCards(stories ? [...cards, stories] : cards, host);
}

/**
 * The card listing the stories that include a record, or null for a reader
 * who cannot see stories. The list loads when `trigger`, the tab the card is
 * on, is first shown.
 */
export function storiesCard({ me, kind, id, framework, trigger, noun }) {
  const list = node('div', { id: 'relatedStories' });
  const card = relatedCard({
    id: 'relatedStoriesCard',
    title: 'Stories',
    description: `Stories that include this ${noun}, with the events and records around it.`,
    body: list,
    wide: true,
  });
  const mounted = mountStoryRelated({
    me,
    kind,
    id,
    framework,
    host: list,
    trigger,
    onLoad: (stories) => updateRelatedCard(card, { count: stories.length }),
  });
  return mounted ? card : null;
}
