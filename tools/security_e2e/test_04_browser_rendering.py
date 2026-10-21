"""Stored hostile text rendered in a real browser through the supplied UI and nginx."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

PROBE = """
() => {
  const marker = window.__keenE2EMarker;
  const injected = Array.from(document.querySelectorAll('[' + marker + ']')).map(e => e.tagName);
  const links = Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href'));
  const resolved = Array.from(document.querySelectorAll('a[href]')).map(a => a.href);
  return {
    rendered: (document.body ? document.body.innerText : '').includes(window.__keenE2ERunId),
    injected,
    executed: window.__keenE2E === 1,
    links,
    resolved,
    violations: window.__keenE2EViolations || [],
  };
}
"""

INIT = """
window.__keenE2EViolations = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__keenE2EViolations.push(e.violatedDirective + ' ' + (e.blockedURI || ''));
});
"""


# Overview pages that do not necessarily list the seeded records.
OVERVIEW_PAGES = {"isms", "soa"}


def _inspect(page, target, url, payloads):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    page.add_init_script(
        INIT
        + f"window.__keenE2EMarker = {payloads.marker!r};"
        + f"window.__keenE2ERunId = {payloads.run_id!r};"
    )
    page.goto(target.origin + url, wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(750)
    result = page.evaluate(PROBE)
    result["dialogs"] = dialogs
    return result


def test_probe_detects_injected_markup(page_as, payloads):
    """The detector itself works: raw payload markup inserted into a page is found."""
    page = page_as()
    page.add_init_script(
        f"window.__keenE2EMarker = {payloads.marker!r}; window.__keenE2ERunId = {payloads.run_id!r};"
    )
    page.goto("about:blank")
    page.set_content("<body>" + payloads.text() + "</body>")
    page.evaluate(
        f"() => {{ window.__keenE2EMarker = {payloads.marker!r}; window.__keenE2ERunId = {payloads.run_id!r}; }}"
    )
    result = page.evaluate(PROBE)
    assert result["rendered"] and len(set(result["injected"])) >= 5, result["injected"]


def test_seeded_pages_render_hostile_text_inertly(
    page_as, admin, seed, payloads, target
):
    findings = []
    host = urlsplit(target.origin).hostname
    for label, url in seed.pages:
        page = page_as(admin)
        result = _inspect(page, target, url, payloads)
        problems = {}
        if not result["rendered"] and label not in OVERVIEW_PAGES:
            problems["seeded_text_not_shown"] = True
        if result["injected"]:
            problems["html_injection"] = sorted(set(result["injected"]))
        if result["executed"] or result["dialogs"]:
            problems["script_execution"] = True
        bad_links = [
            href
            for href, resolved in zip(result["links"], result["resolved"])
            if href.strip().lower().startswith(("javascript:", "data:", "vbscript:"))
            or (
                payloads.run_id in href
                and urlsplit(resolved).hostname not in (host, "example.org")
            )
        ]
        if bad_links:
            problems["unsafe_links"] = bad_links[:5]
        script_violations = [
            v for v in result["violations"] if v.startswith("script-src")
        ]
        if script_violations:
            # Blocked by CSP, but the markup still reached the DOM unescaped.
            problems["csp_blocked_injection"] = script_violations[:5]
        if problems:
            findings.append((label, url, problems))
    assert not findings, findings


@pytest.mark.parametrize("role", ["risk_reader", "isms_reader", "audit_reader"])
def test_hostile_text_is_inert_for_read_only_roles(
    page_as, accounts, seed, payloads, target, role
):
    session = accounts(role)
    allowed = {
        "risk_reader": (
            "risk",
            "risks",
            "cia",
            "library",
            "pestle",
            "party",
            "objective",
        ),
        "isms_reader": ("objective", "document", "meeting", "isms"),
        "audit_reader": ("audit", "audits"),
    }[role]
    findings = []
    for label, url in seed.pages:
        if label not in allowed:
            continue
        result = _inspect(page_as(session), target, url, payloads)
        if result["injected"] or result["executed"] or result["dialogs"]:
            findings.append((label, url))
    assert not findings, findings


def test_pages_load_without_script_errors_when_signed_in(page_as, admin, target):
    """A page that throws may leave security-relevant UI half initialised."""
    errors = []
    for url in (
        "/",
        "/events.html",
        "/risks.html",
        "/isms.html",
        "/audits.html",
        "/account.html",
    ):
        page = page_as(admin)
        page.goto(target.origin + url, wait_until="networkidle", timeout=45000)
        if page.keen_errors:
            errors.append((url, page.keen_errors[:2]))
    assert not errors, errors


def test_diary_links_are_rejected_or_rendered_inertly(
    page_as, admin, seed, payloads, target
):
    """Each hostile link is either refused by the API or neutralised by the event page."""
    event = seed.ids.get("event")
    if not event:
        pytest.skip(
            "Needs KEEN_E2E_ALLOW_EVIDENCE_WRITES=1 for a synthetic diary event"
        )
    safe = {"url": "https://example.org/" + payloads.run_id, "text": payloads.run_id}
    findings = []
    try:
        for link in payloads.links():
            response = admin.patch(
                f"/api/v1/admin/diary/{event}",
                json={"links": [safe, {"url": link, "text": "x"}]},
            )
            if response.status_code >= 500:
                findings.append((link, response.status_code))
                continue
            if response.status_code != 200:
                continue
            result = _inspect(
                page_as(admin), target, f"/event.html?id={event}", payloads
            )
            unsafe = [
                href
                for href in result["links"]
                if href.strip()
                .lower()
                .startswith(("javascript:", "data:", "vbscript:"))
                or href.startswith(("//", "/\\"))
            ]
            if unsafe or result["executed"] or result["dialogs"]:
                findings.append((link, unsafe[:3]))
    finally:
        admin.patch(f"/api/v1/admin/diary/{event}", json={"links": [safe]})
    assert not findings, findings
