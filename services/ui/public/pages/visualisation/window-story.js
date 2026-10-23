// A story from a window of the histogram: the events between two instants,
// under the page's filters, with the viewer's annotation as the first note.
import { apiGet, apiPost, fmtTs, toast, withFramework } from '/app.js';
import { featureEnabled } from '/pages/feature-availability.js';

export const WINDOW_STORY_LIMIT = 200;

export function windowStoryTitle(startTs, endTs) {
  return `Events from ${fmtTs(startTs)} to ${fmtTs(endTs)}`;
}

export function createWindowStory({ me, framework, button, panel, statusEl, applyScope }) {
  const enabled = Boolean(
    featureEnabled(me, 'stories') && (me?.is_admin || me?.can_manage_stories),
  );
  const title = panel?.querySelector('[data-window-title]');
  const note = panel?.querySelector('[data-window-note]');
  const label = panel?.querySelector('[data-window]');
  let current = null;
  let autoTitle = '';

  function describe(window) {
    const parts = [`${fmtTs(window.start_ts)} - ${fmtTs(window.end_ts)}`];
    if (Number.isFinite(Number(window.total))) parts.push(`${window.total} events`);
    if (window.source) parts.push(`source ${window.source}`);
    return parts.join(' · ');
  }

  // The window a story would cover: the histogram zoom, or a bar.
  function update(window) {
    current = window && window.start_ts && window.end_ts ? { ...window } : null;
    if (button) button.classList.toggle('d-none', !(enabled && current));
    if (!current || !enabled) {
      if (panel) panel.hidden = true;
      return;
    }
    if (label) label.textContent = describe(current);
    if (title && (!title.value || title.value === autoTitle)) {
      autoTitle = windowStoryTitle(current.start_ts, current.end_ts);
      title.value = autoTitle;
    }
  }

  function open() {
    if (!enabled || !current || !panel) return;
    panel.hidden = false;
    note?.focus();
  }

  async function create() {
    if (!enabled || !current) return;
    const url = new URL('/api/v1/events', 'https://keen.invalid');
    url.searchParams.set('framework', framework);
    url.searchParams.set('start_ts', current.start_ts);
    url.searchParams.set('end_ts', current.end_ts);
    url.searchParams.set('limit', String(WINDOW_STORY_LIMIT));
    applyScope?.(url);
    // A bar names one source; it replaces any source filter from the page.
    if (current.source) url.searchParams.set('source', current.source);
    try {
      const data = await apiGet(url.pathname + url.search);
      const events = (data.items || []).slice(0, WINDOW_STORY_LIMIT);
      const items = events.map((event) => ({ kind: 'event', entity_id: event.id }));
      const annotation = String(note?.value || '').trim();
      if (annotation) {
        items.unshift({
          kind: 'note',
          label: 'Annotation',
          note: annotation,
          occurred_at: current.start_ts,
        });
      }
      if (!items.length) {
        toast(
          statusEl,
          'No events in this window; add an annotation to start a story anyway.',
          'warning',
        );
        return;
      }
      const story = await apiPost(`/api/v1/stories?framework=${encodeURIComponent(framework)}`, {
        title:
          String(title?.value || '').trim() || windowStoryTitle(current.start_ts, current.end_ts),
        items,
      });
      if (Number(data.total || 0) > events.length) {
        toast(
          statusEl,
          `The window holds ${data.total} events; the newest ${events.length} were added to the story.`,
          'warning',
        );
      }
      location.assign(withFramework(`/story.html?id=${encodeURIComponent(story.id)}`, framework));
    } catch (error) {
      toast(statusEl, `Failed to create the story: ${String(error)}`, 'danger');
    }
  }

  button?.addEventListener('click', open);
  panel?.querySelector('[data-window-create]')?.addEventListener('click', create);
  panel?.querySelector('[data-window-cancel]')?.addEventListener('click', () => {
    panel.hidden = true;
  });
  if (button) button.classList.add('d-none');
  return {
    enabled,
    update,
    open,
    create,
    get window() {
      return current;
    },
  };
}
