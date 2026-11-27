// The Related items tab of a record that other records point at (a document,
// an asset, a risk, an objective, a person, a vendor, a set of minutes, an
// effectiveness measure): one card for each kind of record related to it,
// drawn with the design system's related-item cards, as on the pages of the
// records that keep their own relationships (a SWOT item, a legal register
// item, a control).
import {
  apiGet,
  errorText,
  getCurrentFramework,
  node,
  relatedCard,
  relatedCards,
  updateRelatedCard,
  withFramework,
} from '/app.js';
import { featureEnabled } from '/pages/feature-availability.js';
import { storiesCard } from '/pages/related-records.js';
import { KIND_NAMES } from '/pages/story-related.js';

// The kinds of record a SWOT item and a legal register item can relate to.
const SWOT_TARGETS = new Set(['risk', 'document', 'asset', 'objective', 'person', 'vendor']);
const LEGAL_TARGETS = new Set(['risk', 'document', 'asset']);

// Earlier addresses named a tab for each kind; they open Related items now.
const TAB_NAMES = new Set(['related', 'stories', 'swot', 'legal']);

const loading = (what) => node('p', { class: 'small-muted mb-0' }, `Loading ${what}`);

/**
 * A card whose list is fetched when the tab is first shown.
 *
 * `read()` answers the card's count and its `links` or `table`. A failed read leaves its reason in the
 * card and is tried again the next time the tab is shown.
 */
export function fetchedCard({ id, title, description, action, what, empty, read }) {
  const card = relatedCard({ id, title, description, action, body: loading(what) });
  let loaded = null;
  async function refresh() {
    try {
      updateRelatedCard(card, { ...(await read()), empty });
    } catch (error) {
      loaded = null;
      updateRelatedCard(card, {
        body: node(
          'p',
          { class: 'small-muted mb-0' },
          `${title} could not be loaded: ${errorText(error)}`,
        ),
      });
    }
  }
  return {
    card,
    load() {
      loaded ||= refresh();
      return loaded;
    },
  };
}

function swotCard({ kind, id, framework }) {
  const query = `target_type=${encodeURIComponent(kind)}&target_id=${encodeURIComponent(id)}`;
  return fetchedCard({
    id: 'relatedSwotCard',
    title: 'SWOT analysis',
    description: `SWOT items related to this ${KIND_NAMES[kind]}.`,
    action: {
      label: 'Open in SWOT analysis',
      href: withFramework(`/swot.html?${query}`, framework),
    },
    what: 'related SWOT items',
    empty: 'No SWOT items are related.',
    read: async () => {
      const data = await apiGet(`/api/v1/isms/swot?${query}&limit=100`);
      return {
        count: data.total,
        links: data.items.map((row) => ({
          text: row.title,
          href: withFramework(`/swot.html?id=${encodeURIComponent(row.id)}&tab=related`, framework),
          detail: `${row.kind} · ${row.significance}/5`,
        })),
      };
    },
  });
}

function legalCard({ kind, id, framework }) {
  const query = `target_type=${encodeURIComponent(kind)}&target_id=${encodeURIComponent(id)}`;
  return fetchedCard({
    id: 'relatedLegalCard',
    title: 'Legal register',
    description: `Legal register items related to this ${KIND_NAMES[kind]}.`,
    action: {
      label: 'Open in the legal register',
      href: withFramework(`/legal-register.html?${query}`, framework),
    },
    what: 'related legal register items',
    empty: 'No legal register items are related.',
    read: async () => {
      const data = await apiGet(`/api/v1/isms/legal-register?${query}&limit=100`);
      return {
        count: data.total,
        links: data.items.map((item) => ({
          text: item.title,
          href: withFramework(
            `/legal-register.html?id=${encodeURIComponent(item.id)}&tab=related`,
            framework,
          ),
          detail: item.in_scope ? 'In scope' : 'Out of scope',
        })),
      };
    },
  });
}

// The tab joins the page's tabs ahead of the Changelog, which stays last.
function insertTab() {
  const tabs = document.querySelector('main .nav-tabs');
  const content = document.querySelector('main .tab-content');
  if (!tabs || !content) return null;
  const tab = node(
    'button',
    {
      class: 'nav-link',
      id: 'relatedItemsTab',
      type: 'button',
      role: 'tab',
      'data-bs-toggle': 'tab',
      'data-bs-target': '#relatedItemsPane',
      'aria-controls': 'relatedItemsPane',
      'aria-selected': 'false',
    },
    'Related items',
  );
  const item = node('li', { class: 'nav-item', role: 'presentation' }, tab);
  const changelog = tabs.querySelector('button[id$="ChangelogTab"]')?.closest('li');
  if (changelog) changelog.before(item);
  else tabs.append(item);
  const pane = node('section', {
    class: 'tab-pane fade',
    id: 'relatedItemsPane',
    role: 'tabpanel',
    'aria-labelledby': 'relatedItemsTab',
    tabindex: '0',
  });
  content.append(pane);
  return { tab, pane };
}

/**
 * Show the records related to one record, a card for each kind.
 *
 * - kind, id: the record. The kind decides which cards there are: SWOT
 *   analysis and Legal register for the kinds those can relate to, and
 *   Stories for every kind, each only for a reader who can see that area.
 * - first: cards of the page's own, as `{ card, load }`, shown before those.
 * - host: where the cards go. Without one, a Related items tab joins the
 *   page's tabs and the lists load when it is first shown; with one, they
 *   load straight away, for a page that draws its own tabs.
 *
 * Answers `{ tab, pane, load }`, or null when the reader has no card to see.
 */
export function mountRelatedItems({
  me,
  kind,
  id,
  framework = getCurrentFramework(),
  first = [],
  host = null,
}) {
  if (!id) return null;
  const record = { kind, id, framework };
  const isms = Boolean(me?.is_admin || me?.can_view_isms);
  const fetched = [
    ...first,
    ...(SWOT_TARGETS.has(kind) && isms && featureEnabled(me, 'swot') ? [swotCard(record)] : []),
    ...(LEGAL_TARGETS.has(kind) && isms && featureEnabled(me, 'legal_register')
      ? [legalCard(record)]
      : []),
  ];
  // Stories load themselves: straight away in a host, else with the tab,
  // which has to exist before the card is made.
  const tabbed = host ? null : insertTab();
  if (!host && !tabbed) return null;
  const stories = storiesCard({
    me,
    kind,
    id,
    framework,
    trigger: tabbed?.tab || null,
    noun: KIND_NAMES[kind],
  });
  const cards = fetched.map((entry) => entry.card);
  if (stories) cards.push(stories);
  if (!cards.length) {
    tabbed?.tab.closest('li').remove();
    tabbed?.pane.remove();
    return null;
  }
  const pane = host || tabbed.pane;
  relatedCards(cards, pane);
  const load = () => Promise.all(fetched.map((entry) => entry.load()));
  if (!tabbed) {
    load();
    return { tab: null, pane, load };
  }
  const { tab } = tabbed;
  tab.addEventListener('shown.bs.tab', load);
  if (TAB_NAMES.has(new URLSearchParams(location.search).get('tab'))) {
    window.bootstrap?.Tab?.getOrCreateInstance(tab).show();
    load();
  }
  return { tab, pane, load };
}
