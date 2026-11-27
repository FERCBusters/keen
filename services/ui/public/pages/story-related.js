// The stories that include one record, shown on the record's own page. Story
// authors can add the record to one of their open stories or start a new one
// from here. The list loads when its tab is first shown.
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
      <div class="col-12 col-md-8"><label class="form-label" for="storyRelatedChoice">Add this ${name} to a story</label><select class="form-select" id="storyRelatedChoice" required><option value="">Loading your open stories</option></select></div>
      <div class="col-12 col-md-4"><button class="btn btn-outline-primary w-100" type="submit">Add to story</button></div>
    </form>
    <form class="row g-2 align-items-end mt-1" data-story-start>
      <div class="col-12 col-md-8"><label class="form-label" for="storyRelatedTitle">Or start a new story with it</label><input class="form-control" id="storyRelatedTitle" maxlength="256" required placeholder="What happened"></div>
      <div class="col-12 col-md-4"><button class="btn btn-outline-primary w-100" type="submit">Start a story</button></div>
    </form>`;
}

function insertTab(label) {
  const tabs = document.querySelector('main .nav-tabs');
  const content = document.querySelector('main .tab-content');
  if (!tabs || !content) return null;
  const item = document.createElement('li');
  item.className = 'nav-item';
  item.setAttribute('role', 'presentation');
  item.innerHTML = `<button class="nav-link" id="storiesRelatedTab" type="button" role="tab" data-bs-toggle="tab" data-bs-target="#storiesRelatedPane" aria-controls="storiesRelatedPane" aria-selected="false">${esc(label)}</button>`;
  tabs.append(item);
  const pane = document.createElement('section');
  pane.className = 'tab-pane fade';
  pane.id = 'storiesRelatedPane';
  pane.setAttribute('role', 'tabpanel');
  pane.setAttribute('aria-labelledby', 'storiesRelatedTab');
  pane.tabIndex = 0;
  content.append(pane);
  return { tab: item.firstElementChild, pane };
}

/**
 * Show the stories that include a record.
 *
 * Without a host, a "Stories" tab joins the page's tabs and loads when first
 * shown. With a host, the list renders there, loading when `trigger` (a tab
 * button, which keeps its name) is first shown or straight away when there is
 * no trigger. A page with a Related items tab hosts the list in a card there.
 * `onLoad` receives the stories each time the list loads.
 */
export function mountStoryRelated({
  me,
  kind,
  id,
  host = null,
  trigger = null,
  framework = getCurrentFramework(),
  onLoad = () => {},
}) {
  if (!id || !KIND_NAMES[kind] || !canViewStories(me)) return null;
  let pane = host;
  let tab = trigger;
  if (!pane) {
    const inserted = insertTab('Stories');
    if (!inserted) return null;
    ({ pane, tab } = inserted);
  }
  const manage = canManageStories(me);
  const recordUrl = `/api/v1/stories/related/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`;
  // A host frames the list itself; a tab of its own carries a heading.
  const heading = host
    ? ''
    : `<h2 class="h5">Stories</h2>
    <p class="small-muted">Stories that include this ${esc(KIND_NAMES[kind])}, with the events and records around it.</p>`;
  pane.innerHTML = `${heading}
    <div data-story-related-list><p class="small-muted mb-0">Loading stories</p></div>
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
      // A tab of its own counts its stories; a host's tab keeps its name.
      if (!host) tab.textContent = stories.length ? `Stories (${stories.length})` : 'Stories';
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

  if (!tab) load();
  else {
    tab.addEventListener('shown.bs.tab', load);
    if (!host && new URLSearchParams(location.search).get('tab') === 'stories') {
      if (window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(tab).show();
      load();
    }
  }
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
 * Show, on a control's or clause's page, the stories assigned to it.
 *
 * Without a host, a "Stories" tab joins the page's tabs and loads when first
 * shown. With a host, the list renders there and loads when `trigger` (a tab
 * button) is shown, or straight away when that tab is already open. Stories
 * are assigned controls and clauses on the story's own page, so the list has
 * none of the forms a record's Stories tab has.
 */
export function mountAssignedStories({
  me,
  kind,
  id,
  host = null,
  trigger = null,
  framework = getCurrentFramework(),
}) {
  if (!id || !canViewStories(me)) return null;
  let pane = host;
  let tab = trigger;
  if (!pane) {
    const inserted = insertTab('Stories');
    if (!inserted) return null;
    ({ pane, tab } = inserted);
  }
  const list = node('div', {}, node('p', { class: 'small-muted mb-0' }, 'Loading stories'));
  // A host frames the list itself; a tab of its own carries a heading.
  pane.replaceChildren(
    ...(host
      ? []
      : [
          node('h2', { class: 'h5' }, 'Stories'),
          node(
            'p',
            { class: 'small-muted' },
            `Stories whose authors assigned them to this ${kind}, on the story's Controls and clauses tab.`,
          ),
        ]),
    list,
  );
  let loaded = null;

  async function refresh() {
    try {
      const data = await apiGet(
        `/api/v1/stories/related/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
      );
      list.replaceChildren(assignedList(data.items, kind, framework));
      // A tab of its own counts its stories; a host's tab keeps its name.
      if (!host) tab.textContent = data.items.length ? `Stories (${data.items.length})` : 'Stories';
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
