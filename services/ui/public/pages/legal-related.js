// The "Legal register" tab of a document, asset or risk page: the register
// items related to that record, so a relationship made on an item shows from
// the record's side too. The list loads when the tab is first opened.
import { apiGet, errorText, getCurrentFramework, node, withFramework } from '/app.js';
import { featureEnabled } from '/pages/feature-availability.js';

export function mountLegalRelated({ me, type, id }) {
  if (!id || !me?.can_view_isms || !featureEnabled(me, 'legal_register')) return;
  const framework = getCurrentFramework();
  const pane = node(
    'section',
    { id: 'legalRelatedPane', class: 'tab-pane fade', role: 'tabpanel' },
    node('p', { class: 'small-muted' }, 'Loading related legal register items'),
  );
  const tab = node(
    'button',
    {
      class: 'nav-link',
      type: 'button',
      role: 'tab',
      id: 'legalRelatedTab',
      'data-bs-toggle': 'tab',
      'data-bs-target': '#legalRelatedPane',
    },
    'Legal register',
  );
  document.querySelector('main .nav-tabs').append(node('li', { class: 'nav-item' }, tab));
  document.querySelector('main .tab-content').append(pane);
  const register = withFramework(
    `/legal-register.html?target_type=${type}&target_id=${encodeURIComponent(id)}`,
    framework,
  );
  let loaded = false;
  tab.addEventListener('click', async () => {
    if (loaded) return;
    loaded = true;
    try {
      const data = await apiGet(
        `/api/v1/isms/legal-register?target_type=${type}&target_id=${encodeURIComponent(id)}&limit=100`,
      );
      pane.replaceChildren(
        node('h2', { class: 'h5' }, 'Related legal register items'),
        ...(data.items.length
          ? data.items.map((item) =>
              node(
                'p',
                {},
                node(
                  'a',
                  {
                    href: withFramework(
                      `/legal-register.html?id=${encodeURIComponent(item.id)}&tab=related`,
                      framework,
                    ),
                  },
                  item.title,
                ),
                ' ',
                node('span', { class: 'small-muted' }, item.in_scope ? 'In scope' : 'Out of scope'),
              ),
            )
          : [node('p', { class: 'small-muted' }, 'No legal register items are related.')]),
        node('a', { href: register }, `Open in the legal register (${data.total})`),
      );
    } catch (error) {
      // A later click tries again.
      loaded = false;
      pane.textContent = `Related legal register items could not be loaded: ${errorText(error)}`;
    }
  });
}
