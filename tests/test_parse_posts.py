"""Tests for telegraf/parse_posts.py.

The HTML here is written by hand from the rules the parser is supposed to
follow, not copied out of its output. Where a case came from a real page, the
comment says what was measured and when.

The parser is the one piece of the Telegram agent that can be wrong without
anything looking wrong: it turns markup into sentences a model then summarises
as fact. A reply quote read as new content becomes "Lighter announced X" when
Lighter announced nothing.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "telegraf" / "parse_posts.py"


def run(page: str):
    """Feed HTML to the tool, return (exit code, list of parsed posts)."""
    proc = subprocess.run(
        [sys.executable, str(TOOL)],
        input=page, capture_output=True, text=True,
    )
    posts = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode, posts


def wrap(post_id: int, body: str, ts: str = "2026-08-03T10:00:00+00:00",
         channel: str = "chan") -> str:
    """Minimal markup with the same shape as a real t.me/s page."""
    return (
        f'<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message" data-post="{channel}/{post_id}">'
        f'{body}'
        f'<div class="tgme_widget_message_footer">'
        f'<time datetime="{ts}"></time></div>'
        f'</div></div>'
    )


def text_div(inner: str) -> str:
    return f'<div class="tgme_widget_message_text js-message_text" dir="auto">{inner}</div>'


def reply_div(inner: str) -> str:
    """The quoted text of the post being replied to — not new content."""
    return f'<div class="tgme_widget_message_text js-message_reply_text" dir="auto">{inner}</div>'


def test_extracts_one_post():
    rc, posts = run(wrap(10, text_div("hello")))
    assert rc == 0
    assert len(posts) == 1
    assert posts[0]["id"] == 10
    assert posts[0]["text"] == "hello"
    assert posts[0]["post"] == "chan/10"
    assert posts[0]["url"] == "https://t.me/chan/10"
    assert posts[0]["ts"] == "2026-08-03T10:00:00+00:00"


def test_reply_quote_is_not_treated_as_content():
    """Measured 2026-08-03: posts 138 and 141 carried a reply quote each, so the
    first page held 22 text divs for 20 posts. Counting the quote as content
    would attribute someone else's words to a new announcement."""
    page = wrap(11, reply_div("ORIGINAL QUOTED TEXT") + text_div("my actual reply"))
    rc, posts = run(page)
    assert rc == 0
    assert len(posts) == 1
    assert posts[0]["text"] == "my actual reply"
    assert "ORIGINAL" not in posts[0]["text"]


def test_br_becomes_newline():
    """Telegram carries line breaks as <br/>. Dropping them glues the end of one
    bullet to the start of the next."""
    rc, posts = run(wrap(12, text_div("line one<br/>line two<br>line three")))
    assert rc == 0
    assert posts[0]["text"] == "line one\nline two\nline three"


def test_inline_tags_are_stripped_without_joining_words():
    rc, posts = run(wrap(13, text_div("from <b>reduce both</b> to <code>cancel maker</code>")))
    assert posts[0]["text"] == "from reduce both to cancel maker"


def test_html_entities_are_decoded():
    rc, posts = run(wrap(14, text_div("We&#39;re changing &amp; testing &lt;x&gt;")))
    assert posts[0]["text"] == "We're changing & testing <x>"


def test_posts_come_out_in_ascending_id_order():
    page = wrap(30, text_div("c")) + wrap(10, text_div("a")) + wrap(20, text_div("b"))
    rc, posts = run(page)
    assert [p["id"] for p in posts] == [10, 20, 30]


def test_duplicate_post_is_emitted_once():
    """A page fetched while the channel updates can repeat a post."""
    page = wrap(10, text_div("first")) + wrap(10, text_div("first"))
    rc, posts = run(page)
    assert len(posts) == 1


def test_post_without_text_yields_empty_string_not_a_skip():
    """A photo-only post still exists; it must keep its id so the watermark
    advances past it instead of re-reading it forever."""
    rc, posts = run(wrap(15, '<div class="tgme_widget_message_photo"></div>'))
    assert rc == 0
    assert len(posts) == 1
    assert posts[0]["id"] == 15
    assert posts[0]["text"] == ""


def test_missing_timestamp_is_empty_not_fatal():
    page = (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        '<div class="tgme_widget_message" data-post="chan/16">'
        + text_div("no time here") +
        '</div></div>'
    )
    rc, posts = run(page)
    assert rc == 0
    assert posts[0]["ts"] == ""


def test_empty_page_exits_3():
    """Printing nothing on exit 0 would make a broken parser look like a quiet
    day. The caller must be able to tell the difference."""
    rc, posts = run("<html><body>no posts here</body></html>")
    assert rc == 3
    assert posts == []


def test_format_change_exits_3_rather_than_inventing_posts():
    """If Telegram renames the wrapper class, the parser must fail loudly."""
    page = ('<div class="tgme_NEW_wrapper" data-post="chan/17">'
            + text_div("text") + '</div>')
    rc, posts = run(page)
    assert rc == 3


def test_two_posts_are_not_merged():
    """Splitting on the wrapper keeps posts apart; a greedy match would swallow
    the second post's markup into the first."""
    page = wrap(20, text_div("first post")) + wrap(21, text_div("second post"))
    rc, posts = run(page)
    assert len(posts) == 2
    assert posts[0]["text"] == "first post"
    assert posts[1]["text"] == "second post"


def test_non_numeric_post_id_is_skipped():
    page = ('<div class="tgme_widget_message_wrap js-widget_message_wrap">'
            '<div class="tgme_widget_message" data-post="chan/abc">'
            + text_div("junk") + '</div></div>'
            + wrap(22, text_div("good")))
    rc, posts = run(page)
    assert [p["id"] for p in posts] == [22]


def test_blank_line_runs_are_collapsed():
    rc, posts = run(wrap(23, text_div("a<br/><br/><br/><br/>b")))
    assert posts[0]["text"] == "a\n\nb"


def test_real_page_shape(tmp_path):
    """Against a saved fragment shaped like the live page: two posts, one of
    which is a reply. Guards the count invariant that first exposed the
    reply-quote problem — text divs outnumber posts."""
    page = (
        wrap(100, text_div("plain announcement")) +
        wrap(101, reply_div("quoted") + text_div("reply body")) +
        wrap(102, text_div("another<br/>multi line"))
    )
    # 3 posts, but 4 text divs: the reply carries a quote of the original.
    assert page.count("tgme_widget_message_text") == 4
    assert page.count("js-message_text") == 3
    rc, posts = run(page)
    assert rc == 0
    assert len(posts) == 3
    assert posts[1]["text"] == "reply body"
