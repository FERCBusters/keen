"""Authoritative stored rich-text sanitisation: preserve formatting, remove executable markup."""

from html.parser import HTMLParser

import pytest
from app.security.rich_text import plain_text_html, sanitize_rich_text_html


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "jav&#x61;script:alert(1)",
        "data:text/html,x",
        "vbscript:x",
        "//outside.test",
        "file:///etc/passwd",
        "https://a.test/" + "x" * 2050,
    ],
)
def test_dangerous_links_have_no_href(url):
    result = sanitize_rich_text_html(f'<a href="{url}" onclick="alert(1)">text</a>')
    assert Tags(result).tags == [("a", {})]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test",
        "http://example.test",
        "mailto:me@example.test",
        "tel:123",
        "/events.html",
        "#section",
    ],
)
def test_safe_links_preserve_destinations(url):
    tags = Tags(sanitize_rich_text_html(f'<a href="{url}">text</a>')).tags
    assert tags[0][1]["href"] == url
    if url.startswith("http"):
        assert tags[0][1]["rel"] == "noopener noreferrer"


@pytest.mark.parametrize(
    "tag",
    [
        "p",
        "h1",
        "h2",
        "strong",
        "em",
        "ul",
        "ol",
        "li",
        "pre",
        "code",
        "blockquote",
        "div",
        "u",
    ],
)
def test_formatting_survives_without_arbitrary_attributes(tag):
    assert (
        sanitize_rich_text_html(f'<{tag} style="bad" id="x" onclick="bad">text</{tag}>')
        == f"<{tag}>text</{tag}>"
    )


@pytest.mark.parametrize(
    "markup",
    [
        "<img src=x onerror=alert(1)>",
        "<svg onload=alert(1)>bad</svg>",
        '<iframe srcdoc="bad">x</iframe>',
        "<script>alert(1)</script>",
    ],
)
def test_active_elements_are_removed(markup):
    assert Tags(sanitize_rich_text_html(markup)).tags == []


def test_entities_strike_void_tags_and_plain_text():
    assert (
        sanitize_rich_text_html("<p>&lt;script&gt; &amp; text</p>")
        == "<p>&lt;script&gt; &amp; text</p>"
    )
    assert sanitize_rich_text_html("<strike>x</strike><br/><hr/>") == "<s>x</s><br><hr>"
    assert sanitize_rich_text_html("plain & text") == "plain & text"
    assert sanitize_rich_text_html(None) == ""


def test_plain_text_becomes_rich_text_the_sanitiser_keeps_unchanged():
    text = (
        "# Access control\r\n"
        "Who may reach what,\n"
        "and why <script>alert(1)</script> & how.\n"
        "\n"
        "- Least privilege\n"
        "* Reviewed quarterly\n"
        "1. Request\n"
        "2) Approve\n"
        "Closing words\r"
        "####### Not a heading\n"
        "\n\n"
        "-not an item"
    )
    html = plain_text_html(text)
    assert html == (
        "<h1>Access control</h1>"
        "<p>Who may reach what,<br>"
        "and why &lt;script&gt;alert(1)&lt;/script&gt; &amp; how.</p>"
        "<ul><li>Least privilege</li><li>Reviewed quarterly</li></ul>"
        "<ol><li>Request</li><li>Approve</li></ol>"
        "<p>Closing words<br>####### Not a heading</p>"
        "<p>-not an item</p>"
    )
    assert sanitize_rich_text_html(html) == html
    assert plain_text_html("") == ""
    assert plain_text_html("###### Six") == "<h6>Six</h6>"
