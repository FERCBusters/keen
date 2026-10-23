// The control page's Requirements tab: what a control expects and what it has.
// Policies and processes are the ISMS documents linked to the control. Evidence
// is the events mapped to it, counted by source, the Diary included. Measures
// and tests are the linked effectiveness measures with their latest results.
// Each part shows for people who can read it, and each count links to the
// records behind it.
import { featureEnabled } from '/pages/feature-availability.js';
import {
  apiGet,
  esc,
  fmtTs,
  withFramework,
  effectivenessMetricThresholdBadgeHtml,
  effectivenessMetricThresholdState,
  effectivenessMetricValueHtml,
} from '/app.js';

const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
const capitalise = (text) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

const TILE_STATES = {
  ok: ['bi-check-circle-fill text-success', 'In place'],
  attention: ['bi-exclamation-circle-fill text-warning', 'Needs attention'],
  none: ['bi-dash-circle text-secondary', 'Not available'],
};

// A summary tile that jumps to its section.
function tile(id, label, value, detail, state) {
  const [icon, meaning] = TILE_STATES[state];
  return `<a class="kpi d-block h-100 link-body-emphasis text-decoration-none" href="#${id}">
      <div class="label"><i class="bi ${icon} me-1" aria-hidden="true"></i>${esc(label)}<span class="visually-hidden">: ${esc(meaning)}</span></div>
      <div class="value">${esc(value)}</div>
      <div class="small-muted">${esc(detail)}</div>
    </a>`;
}

function section(id, title, subtitle, body, actions = '') {
  return `<section class="card mb-3" id="${id}">
      <div class="card-header d-flex flex-wrap gap-2 align-items-center justify-content-between">
        <div><h2 class="h6 fw-bold mb-0">${esc(title)}</h2><div class="small-muted">${esc(subtitle)}</div></div>
        <div class="d-flex flex-wrap gap-2 no-print">${actions}</div>
      </div>
      <div class="card-body">${body}</div>
    </section>`;
}

function table(headings, rows) {
  return `<div class="table-responsive"><table class="table table-sm align-middle mb-0"><thead><tr>${headings
    .map((h) => `<th>${esc(h)}</th>`)
    .join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

const muted = (text) => `<div class="small-muted">${esc(text)}</div>`;
const failed = (text) => `<div class="text-danger small">${esc(text)}</div>`;
const button = (href, label) =>
  `<a class="btn btn-sm btn-outline-primary" href="${esc(href)}">${esc(label)}</a>`;

export function createControlRequirements({ root, me, isms, sourceName, onOpenEvidence }) {
  const canEvidence = !!(me?.is_admin || me?.can_view_events);
  const canIsms = !!(me?.is_admin || me?.can_view_isms);
  const canManageIsms = !!(me?.is_admin || me?.can_manage_isms);
  const parts = {
    policies: featureEnabled(me, 'isms') && featureEnabled(me, 'documents'),
    evidence: featureEnabled(me, 'evidence'),
    measures: featureEnabled(me, 'isms') && featureEnabled(me, 'effectiveness'),
  };
  let control = null;
  let loaded = null;

  const link = (path) => withFramework(path, control.framework);
  const evidenceHref = (source) =>
    link(
      `/control.html?id=${encodeURIComponent(control.id)}&tab=evidence${
        source ? `&source=${encodeURIComponent(source)}` : ''
      }`,
    );

  function policies(result) {
    if (!canIsms)
      return { body: muted('You do not have permission to view policies and processes.') };
    if (result.status === 'rejected')
      return { body: failed('Failed to load linked policies and processes.') };
    const documents = Array.isArray(result.value?.documents) ? result.value.documents : [];
    const rows = documents.map((d) => {
      const kept = d.content_html
        ? 'Written in Keen'
        : d.has_file
          ? d.filename || 'File'
          : d.external_url
            ? 'Link'
            : '-';
      const version = Number(d.content_version) > 0 ? `v${d.content_version}` : '-';
      return `<tr>
          <td class="wrap"><a class="fw-semibold" href="${esc(link(`/isms-document.html?id=${encodeURIComponent(d.id || '')}`))}">${esc(d.title || 'Document')}</a></td>
          <td>${esc(capitalise(String(d.document_type || 'document')))}</td>
          <td class="small-muted">${esc(kept)}</td>
          <td class="small-muted">${esc(version)}</td>
          <td class="small-muted">${esc(d.updated_at ? fmtTs(d.updated_at) : '-')}</td>
        </tr>`;
    });
    return {
      count: documents.length,
      body: rows.length
        ? table(['Policy or process', 'Type', 'Kept as', 'Version', 'Updated'], rows)
        : muted(
            'No policies or processes are linked yet. Link one from its page under ISMS, Policies and processes.',
          ),
    };
  }

  function sourceRow(row) {
    const links = Number(row.link_entries) || 0;
    const note = links
      ? `<div class="small-muted">${esc(`${plural(links, 'entry', 'entries')} recorded when a record was linked`)}</div>`
      : '';
    return `<tr>
        <td class="wrap"><div class="fw-semibold">${esc(row.source ? sourceName(row.source) : 'Unknown source')}</div>${note}</td>
        <td><a href="${esc(evidenceHref(row.source))}" data-open-evidence="${esc(row.source)}">${esc(plural(row.count, 'event'))}</a></td>
        <td class="small-muted">${esc(row.latest ? fmtTs(row.latest) : '-')}</td>
      </tr>`;
  }

  function evidence(result) {
    if (!canEvidence) return { body: muted('You do not have permission to view evidence.') };
    if (result.status === 'rejected')
      return { body: failed('Failed to load the evidence summary.') };
    const data = result.value || {};
    const sources = Array.isArray(data.sources) ? data.sources : [];
    return {
      total: Number(data.total) || 0,
      sources: sources.length,
      latest: data.latest,
      body: sources.length
        ? table(['Source', 'Evidence', 'Latest'], sources.map(sourceRow))
        : muted('No evidence is mapped to this control yet.'),
    };
  }

  function measureRow(m) {
    const entry = m.latest_entry || null;
    const href = link(`/isms-effectiveness-measure.html?id=${encodeURIComponent(m.id || '')}`);
    const value = entry
      ? `${effectivenessMetricValueHtml(entry, m)}${effectivenessMetricThresholdBadgeHtml(m, entry)}`
      : '<span class="badge text-bg-warning">No result yet</span>';
    const record = canManageIsms
      ? `<a class="small" href="${esc(link(`/isms.html?tab=effectiveness&record=${encodeURIComponent(m.id || '')}`))}">Record a result</a>`
      : '';
    return `<tr>
        <td class="wrap"><a class="fw-semibold" href="${esc(href)}">${esc(m.summary || 'Effectiveness measure')}</a>${m.frequency ? `<div class="small-muted">${esc(capitalise(String(m.frequency)))}</div>` : ''}</td>
        <td>${value}</td>
        <td class="small-muted">${esc(m.target_display || '-')}</td>
        <td class="small-muted">${esc(entry?.recorded_at ? fmtTs(entry.recorded_at) : '-')}</td>
        <td class="text-end text-nowrap">${record}</td>
      </tr>`;
  }

  function measures(result) {
    if (!canIsms)
      return { body: muted('You do not have permission to view effectiveness measures.') };
    if (result.status === 'rejected')
      return { body: failed('Failed to load linked effectiveness measures.') };
    const items = Array.isArray(result.value?.items) ? result.value.items : [];
    const judged = items.filter((m) => effectivenessMetricThresholdState(m, m.latest_entry));
    return {
      count: items.length,
      judged: judged.length,
      met: judged.filter((m) => effectivenessMetricThresholdState(m, m.latest_entry).ok).length,
      unrecorded: items.filter((m) => !m.latest_entry).length,
      body: items.length
        ? table(
            ['Measure or test', 'Latest result', 'Target', 'Recorded', ''],
            items.map(measureRow),
          )
        : muted(
            'No effectiveness measures are linked yet. Link a measure to this control from its page under ISMS, Effectiveness.',
          ),
    };
  }

  function tiles(found) {
    const out = [];
    if (found.policies) {
      const p = found.policies;
      out.push(
        p.count === undefined
          ? tile('requirementsPolicies', 'Policies and processes', '-', 'Not available', 'none')
          : tile(
              'requirementsPolicies',
              'Policies and processes',
              String(p.count),
              p.count ? 'linked to this control' : 'None linked yet',
              p.count ? 'ok' : 'attention',
            ),
      );
    }
    if (found.evidence) {
      const e = found.evidence;
      const detail = e.total
        ? `${plural(e.sources, 'source')}${e.latest ? `, latest ${fmtTs(e.latest)}` : ''}`
        : 'None mapped yet';
      out.push(
        e.total === undefined
          ? tile('requirementsEvidence', 'Evidence', '-', 'Not available', 'none')
          : tile(
              'requirementsEvidence',
              'Evidence',
              plural(e.total, 'event'),
              detail,
              e.total ? 'ok' : 'attention',
            ),
      );
    }
    if (found.measures) {
      const m = found.measures;
      let detail = 'None linked yet';
      if (m.count)
        detail = m.judged
          ? `${m.met} of ${m.judged} meeting target`
          : 'No results compared with a target';
      if (m.count && m.unrecorded) detail += `; ${m.unrecorded} without a result`;
      out.push(
        m.count === undefined
          ? tile('requirementsMeasures', 'Measures and tests', '-', 'Not available', 'none')
          : tile(
              'requirementsMeasures',
              'Measures and tests',
              String(m.count),
              detail,
              m.count && m.met === m.judged && !m.unrecorded ? 'ok' : 'attention',
            ),
      );
    }
    return out.length
      ? `<div class="row g-3 mb-3">${out.map((html) => `<div class="col-12 col-md-4">${html}</div>`).join('')}</div>`
      : '';
  }

  function render(results) {
    const found = {};
    const html = [];
    if (parts.policies) {
      found.policies = policies(results.policies);
      const actions = canIsms
        ? button(link('/isms.html?tab=documents'), 'Open policies and processes')
        : '';
      html.push(
        section(
          'requirementsPolicies',
          'Policies and processes',
          'The documents this control expects, linked from ISMS.',
          found.policies.body,
          actions,
        ),
      );
    }
    if (parts.evidence) {
      found.evidence = evidence(results.evidence);
      const actions = [
        me?.is_admin
          ? button(
              `${link(`/admin.html?control=${encodeURIComponent(control.id)}`)}#diary`,
              'Add diary evidence',
            )
          : '',
        canEvidence
          ? `<a class="btn btn-sm btn-primary" href="${esc(evidenceHref(''))}" data-open-evidence="">Open evidence</a>`
          : '',
      ].join('');
      html.push(
        section(
          'requirementsEvidence',
          'Evidence',
          'Events mapped to this control by source, including its Diary entries.',
          found.evidence.body,
          actions,
        ),
      );
    }
    if (parts.measures) {
      found.measures = measures(results.measures);
      const actions = canIsms
        ? button(link('/isms.html?tab=effectiveness'), 'Open effectiveness measures')
        : '';
      html.push(
        section(
          'requirementsMeasures',
          'Measures and tests',
          'Effectiveness measures linked to this control, with their latest results.',
          found.measures.body,
          actions,
        ),
      );
    }
    root.innerHTML =
      tiles(found) +
      (html.join('') ||
        muted('Policies, evidence and measures are switched off for this installation.'));
  }

  async function fetchAll() {
    const skip = Promise.resolve(null);
    const [policyResult, evidenceResult, measureResult] = await Promise.allSettled([
      parts.policies && canIsms ? isms() : skip,
      parts.evidence && canEvidence
        ? apiGet(`/api/v1/controls/${encodeURIComponent(control.id)}/evidence/summary`)
        : skip,
      parts.measures && canIsms
        ? apiGet(
            `/api/v1/controls/${encodeURIComponent(control.id)}/effectiveness-measures?framework=${encodeURIComponent(control.framework)}`,
          )
        : skip,
    ]);
    render({ policies: policyResult, evidence: evidenceResult, measures: measureResult });
  }

  root.addEventListener('click', (ev) => {
    const target = ev.target.closest?.('[data-open-evidence]');
    // Plain clicks stay on the page; other clicks open the link as usual.
    if (!target || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    onOpenEvidence(target.dataset.openEvidence || '');
  });

  return {
    // Loads once per control; later calls reuse the first load.
    load(nextControl) {
      if (!loaded || control?.id !== nextControl?.id) {
        control = nextControl;
        root.innerHTML = muted('Loading requirements');
        loaded = fetchAll();
      }
      return loaded;
    },
    // The next load reads everything again, after a change elsewhere on the page.
    reset() {
      loaded = null;
    },
  };
}
