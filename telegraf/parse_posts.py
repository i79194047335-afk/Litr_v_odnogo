#!/usr/bin/env python3
"""Turn a t.me/s/<channel> page into one JSON object per post, on stdout.

Deterministic, no network, no model: reads HTML on stdin, writes JSONL.
Split out of the agent because the parser is the part that can lie quietly —
it is the only piece here with a real chance of being subtly wrong, and a
subtly wrong parser turns into "Lighter announced X" when Lighter did not.

    curl -s https://t.me/s/lighter_api_updates | parse_posts.py

Each line: {"id": 154, "post": "chan/154", "ts": "...", "url": "...", "text": "..."}
Posts come out in ascending id order.

Exit codes:
    0  parsed at least one post
    1  bad usage
    3  the page held no posts — the format changed, or the channel is empty

Exit 3 matters: silently printing nothing would make a broken parser look
exactly like a quiet day on the channel.
"""

import html
import json
import re
import sys

# One wrapper div per post. Splitting on the wrapper (rather than scanning for
# data-post) keeps each post's markup separate, so a reply quote cannot be
# attributed to the post that follows it.
WRAPPER = re.compile(r'<div class="tgme_widget_message_wrap[^"]*"')

POST_ID = re.compile(r'data-post="([^"]+)"')

# js-message_text is the post's own text. A reply carries a second block with
# js-message_reply_text holding the *quoted* text of the post being answered;
# treating that as new content invents announcements that were never made.
# Measured 2026-08-03: 20 posts on the first page carried 22 text divs, the two
# extras being reply quotes on posts 138 and 141.
TEXT_BLOCK = re.compile(
    r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>',
    re.S,
)

TIME_TAG = re.compile(r'<time[^>]+datetime="([^"]+)"')

BR = re.compile(r'<br\s*/?>', re.I)
BLOCK_END = re.compile(r'</(?:p|div|li)>', re.I)
TAG = re.compile(r'<[^>]+>')


def strip_markup(fragment: str) -> str:
    """HTML fragment -> plain text, keeping the line structure.

    <br/> carries the line breaks in Telegram posts; dropping it glues the last
    word of one bullet onto the first of the next and the model reads nonsense.
    """
    text = BR.sub('\n', fragment)
    text = BLOCK_END.sub('\n', text)
    text = TAG.sub('', text)
    text = html.unescape(text)
    # Trim trailing spaces per line, collapse runs of blank lines.
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse(page: str):
    """Yield post dicts in ascending id order."""
    out = []
    for block in WRAPPER.split(page)[1:]:
        m = POST_ID.search(block)
        if not m:
            continue
        post = m.group(1)
        try:
            num = int(post.rsplit('/', 1)[1])
        except (IndexError, ValueError):
            continue

        body = TEXT_BLOCK.search(block)
        text = strip_markup(body.group(1)) if body else ''

        ts = TIME_TAG.search(block)
        out.append({
            'id': num,
            'post': post,
            'ts': ts.group(1) if ts else '',
            'url': f'https://t.me/{post}',
            'text': text,
        })

    # Ascending, and de-duplicated: a page fetched mid-update can repeat a post.
    seen = set()
    uniq = []
    for p in sorted(out, key=lambda p: p['id']):
        if p['id'] in seen:
            continue
        seen.add(p['id'])
        uniq.append(p)
    return uniq


def main() -> int:
    if len(sys.argv) > 1:
        print(__doc__, file=sys.stderr)
        return 1

    posts = parse(sys.stdin.read())
    if not posts:
        print('parse_posts: no posts found — page format changed or channel empty',
              file=sys.stderr)
        return 3

    for p in posts:
        print(json.dumps(p, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
