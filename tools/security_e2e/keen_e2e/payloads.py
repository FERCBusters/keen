"""Hostile but harmless inputs.

Markup payloads carry a per-run marker attribute. If any of them becomes a real
DOM element, the browser checks find the marker even when the Content Security
Policy stops scripts from running. Script payloads only set a page variable.
"""

from __future__ import annotations

import secrets


class Payloads:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.marker = "data-keen-e2e-" + secrets.token_hex(4)

    def markup(self) -> list[str]:
        m, r = self.marker, self.run_id
        return [
            f'<b {m}="b">{r}-bold</b>',
            f'"><img src=x {m}="img" onerror="window.__keenE2E=1">',
            f'\'><svg {m}="svg" onload="window.__keenE2E=1"></svg>',
            f'</textarea><i {m}="ta">{r}</i>',
            f'</script><script {m}="s">window.__keenE2E=1</script>',
            f'<a {m}="a" href="javascript:window.__keenE2E=1">{r}-link</a>',
            f'<style {m}="style">body{{display:none}}</style>',
            f'<iframe {m}="iframe" srcdoc="<b>x</b>"></iframe>',
            f'<math><mtext><table><mglyph><style><img {m}="mxss" src=x>',
        ]

    def text(self) -> str:
        """One string that mixes the markup payloads with ordinary text."""
        return f"{self.run_id} " + " ".join(self.markup())

    def short(self, limit=200) -> str:
        """A payload that fits short name fields."""
        value = f'{self.run_id}<b {self.marker}="n">n</b>"\'><svg {self.marker}="ns">'
        return value[:limit]

    def links(self) -> list[str]:
        return [
            "javascript:window.__keenE2E=1",
            " JaVaScRiPt:window.__keenE2E=1",
            "data:text/html,<script>window.__keenE2E=1</script>",
            "//evil.example/" + self.run_id,
            "/\\evil.example/" + self.run_id,
            "/\t/evil.example/" + self.run_id,
            "https://user:pass@evil.example/",
        ]

    @staticmethod
    def fuzz_values() -> list[str]:
        return [
            "",
            " ",
            "0",
            "-1",
            "2147483648",
            "99999999999999999999999999",
            "1e309",
            "NaN",
            "true",
            "null",
            "[]",
            "{}",
            "'",
            '"',
            "' OR '1'='1",
            "1;SELECT pg_sleep(5)--",
            "%",
            "%00",
            "\x00",
            "‮",
            "../../../../etc/passwd",
            "..%2f..%2f..%2fetc%2fpasswd",
            "%2e%2e%2f",
            "<script>",
            "{{7*7}}",
            "${7*7}",
            "a" * 5000,
            "😀" * 50,
            "0000-00-00",
            "9999-12-31",
            "0001-01-01",
            "1970-01-01T00:00:00Z",
            "2026-02-30",
            "00000000-0000-0000-0000-000000000000",
            "ffffffff-ffff-ffff-ffff-ffffffffffff",
        ]
