// The stories that include one record, shown as a card of the record's
// Related items. Story authors can add the record to one of their open
// stories or start a new one from here.
import { featureEnabled } from '/pages/feature-availability.js';
import {
  errorText,
  apiGet,
  apiPost,
  esc,
  fmtTs,
  getCurrentFramework,
  node,
  withFramework,
} from '/app.js';

export const KIND_NAMES = {
  event: 'event',
  risk: 'risk',
  document: 'document',
  asset: 'asset',
  objective: 'objective',
  swot: 'SWOT item',
  person: 'person',
  vendor: 'vendor',
  effectiveness_measure: 'effectiveness measure',
  meeting: 'set of minutes',
  legal_item: 'legal register item',
};

export function canViewStories(me) {
  return featureEnabled(me, 'stories') && Boolean(me?.is_admin || me?.can_view_stories);
}

export function canManageStories(me) {
  return canViewStories(me) && Boolean(me?.is_admin || me?.can_manage_stories);
}

function storyHref(story, framework, tab = '') {
  const query = `id=${encodeURIComponent(story.id)}${tab ? `&tab=${tab}` : ''}`;
  return withFramework(`/story.html?${query}`, framework);
}

export function relatedListHtml(stories, kind, framework) {
  if (!stories.length)
    return `<p class="small-muted mb-0">This ${esc(KIND_NAMES[kind])} is not in a story yet.</p>`;
  const rows = stories
    .map(
      (
        story,
      ) => `<li class="list-group-item d-flex flex-wrap gap-2 justify-content-between align-items-start">
        <div class="text-break">
          <a class="fw-semibold" href="${esc(storyHref(story, framework))}">${esc(story.title)}</a>
          <span class="badge ${story.status === 'closed' ? 'text-bg-secondary' : 'text-bg-success'} ms-1">${esc(story.status)}</span>
          <div class="small-muted">${Number(story.item_count) || 0} item${Number(story.item_count) === 1 ? '' : 's'} · updated ${esc(fmtTs(story.updated_at))}${story.created_by ? ` · by ${esc(story.created_by)}` : ''}</div>
        </div>
        <a class="btn btn-sm btn-outline-secondary" href="${esc(storyHref(story, framework, 'flow'))}">Flow chart</a>
      </li>`,
    )
    .join('');
  return `<ul class="list-group mb-0">${rows}</ul>`;
}

function formHtml(kind) {
  const name = esc(KIND_NAMES[kind]);
  return `<form class="row g-2 align-items-end mt-3" data-story-add>
      <div class="col-12 col-md"><label class="form-label" for="storyRelatedChoice">Add this ${name} to a story</label><select class="form-select" id="storyRelatedChoice" required><option value="">Loading your open stories</option></select></div>
      <div class="col-12 col-md-auto"><button class="btn btn-outline-primary story-related-action" type="submit">Add to story</button></div>
    </form>
    <form class="row g-2 align-items-end mt-1" data-story-start>
      <div class="col-12 col-md"><label class="form-label" for="storyRelatedTitle">Or start a new story with it</label><input class="form-control" id="storyRelatedTitle" maxlength="256" required placeholder="What happened"></div>
      <div class="col-12 col-md-auto"><button class="btn btn-outline-primary story-related-action" type="submit">Start a story</button></div>
    </form>`;
}

/**
 * Show the stories that include a record, in `host`: a card of the record's
 * Related items.
 *
 * The list loads when `trigger`, the tab the card is on, is first shown, or
 * straight away when there is no trigger. `onLoad` receives the stories each
 * time the list loads.
 */
export function mountStoryRelated({
  me,
  kind,
  id,
  host,
  trigger = null,
  framework = getCurrentFramework(),
  onLoad = () => {},
}) {
  if (!id || !KIND_NAMES[kind] || !canViewStories(me)) return null;
  const pane = host;
  const tab = trigger;
  const manage = canManageStories(me);
  const recordUrl = `/api/v1/stories/related/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`;
  pane.innerHTML = `<div data-story-related-list><p class="small-muted mb-0">Loading stories</p></div>
    <div data-story-related-status class="small mt-2" role="status"></div>
    ${manage ? formHtml(kind) : ''}`;
  const list = pane.querySelector('[data-story-related-list]');
  const status = pane.querySelector('[data-story-related-status]');
  let loaded = null;
  let included = new Set();

  function say(message, tone) {
    status.className = `small mt-2 text-${tone}`;
    status.textContent = message;
  }

  async function loadChoices() {
    const select = pane.querySelector('#storyRelatedChoice');
    if (!select) return;
    try {
      const data = await apiGet('/api/v1/stories?status=open&limit=200');
      const options = data.items.filter((s) => s.can_edit && !included.has(s.id));
      select.innerHTML = options.length
        ? `<option value="">Choose one of your open stories</option>${options
            .map(
              (s) =>
                `<option value="${esc(s.id)}" data-version="${esc(s.version)}">${esc(s.title)}</option>`,
            )
            .join('')}`
        : '<option value="">No other open stories you can edit</option>';
    } catch (error) {
      select.innerHTML = '<option value="">Your stories could not be listed</option>';
      say(`Could not list your stories: ${errorText(error)}`, 'danger');
    }
  }

  async function refresh() {
    try {
      const data = await apiGet(recordUrl);
      const stories = data.items;
      included = new Set(stories.map((s) => s.id));
      list.innerHTML = relatedListHtml(stories, kind, framework);
      onLoad(stories);
    } catch {
      list.innerHTML =
        '<p class="small-muted mb-0">Stories are unavailable with your current access.</p>';
      return;
    }
    await loadChoices();
  }

  function load() {
    loaded ||= refresh();
    return loaded;
  }

  pane.querySelector('[data-story-add]')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const option = pane.querySelector('#storyRelatedChoice').selectedOptions[0];
    if (!option?.value) return;
    try {
      await apiPost(
        `/api/v1/stories/${encodeURIComponent(option.value)}/items?framework=${encodeURIComponent(framework)}`,
        { items: [{ kind, entity_id: id }], expected_version: Number(option.dataset.version) },
      );
    } catch (error) {
      say(`Could not add it to the story: ${errorText(error)}`, 'danger');
      await loadChoices();
      return;
    }
    await refresh();
    say(`Added to ${option.textContent}.`, 'success');
  });
  pane.querySelector('[data-story-start]')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const title = pane.querySelector('#storyRelatedTitle');
    if (!title.value.trim()) return;
    let story;
    try {
      story = await apiPost(`/api/v1/stories?framework=${encodeURIComponent(framework)}`, {
        title: title.value.trim(),
        items: [{ kind, entity_id: id }],
      });
    } catch (error) {
      say(`Could not start the story: ${errorText(error)}`, 'danger');
      return;
    }
    title.value = '';
    await refresh();
    say(`Started the story ${story.title}.`, 'success');
  });

  if (tab) tab.addEventListener('shown.bs.tab', load);
  else load();
  return { tab, pane, load, refresh };
}

// The stories assigned to a control or clause, built as elements.
function assignedList(stories, kind, framework) {
  if (!stories.length)
    return node('p', { class: 'small-muted mb-0' }, `No story is assigned to this ${kind} yet.`);
  return node(
    'ul',
    { class: 'list-group mb-0' },
    ...stories.map((story) =>
      node(
        'li',
        {
          class: 'list-group-item d-flex flex-wrap gap-2 justify-content-between align-items-start',
        },
        node(
          'div',
          { class: 'text-break' },
          node('a', { class: 'fw-semibold', href: storyHref(story, framework) }, story.title),
          node(
            'span',
            {
              class: `badge ${story.status === 'closed' ? 'text-bg-secondary' : 'text-bg-success'} ms-1`,
            },
            story.status,
          ),
          node(
            'div',
            { class: 'small-muted' },
            `updated ${fmtTs(story.updated_at)}${story.created_by ? ` · by ${story.created_by}` : ''}`,
          ),
        ),
        node(
          'a',
          {
            class: 'btn btn-sm btn-outline-secondary',
            href: storyHref(story, framework, 'framework'),
          },
          'Controls and clauses',
        ),
      ),
    ),
  );
}

/**
 * Show, on a control's or clause's page, the stories assigned to it, in
 * `host`: a card of the page's Related items.
 *
 * The list loads when `trigger`, the tab the card is on, is shown, or
 * straight away when that tab is already open. Stories are assigned controls
 * and clauses on the story's own page, so the list has none of the forms a
 * record's Stories card has.
 */
export function mountAssignedStories({
  me,
  kind,
  id,
  host,
  trigger,
  framework = getCurrentFramework(),
}) {
  if (!id || !canViewStories(me)) return null;
  const pane = host;
  const tab = trigger;
  const list = node('div', {}, node('p', { class: 'small-muted mb-0' }, 'Loading stories'));
  pane.replaceChildren(list);
  let loaded = null;

  async function refresh() {
    try {
      const data = await apiGet(
        `/api/v1/stories/related/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
      );
      list.replaceChildren(assignedList(data.items, kind, framework));
    } catch {
      // A later opening of the tab tries again.
      loaded = null;
      list.replaceChildren(
        node(
          'p',
          { class: 'small-muted mb-0' },
          'Stories are unavailable with your current access.',
        ),
      );
    }
  }

  function load() {
    loaded ||= refresh();
    return loaded;
  }

  tab.addEventListener('shown.bs.tab', load);
  if (tab.classList.contains('active')) load();
  return { tab, pane, load };
}
