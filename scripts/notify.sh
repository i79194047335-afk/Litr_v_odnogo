#!/bin/sh
# Delivery channel for agents: sends a message to Telegram.
#
# Usage:
#   notify.sh <sender> <text>
#   echo "text" | notify.sh <sender>
#
# <sender> is prefixed to every message so several projects can share one bot
# and one chat without the reader having to guess who is talking.
#
# Credentials, in order of precedence:
#   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID   env vars (one-off runs, tests)
#   ~/.telegram_key    / ~/.telegram_chat   the normal way
# Paths are overridable via TELEGRAM_KEY_FILE / TELEGRAM_CHAT_FILE so tests
# never touch the real credentials.
#
# Exit codes:
#   0  delivered
#   1  usage error
#   2  not configured (no token or chat id)
#   3  Telegram refused or the network failed
#
# Silence is not success: a failure to deliver must be visible to the caller,
# because the whole point of this script is that an agent stops being mute.

set -eu

API="${TELEGRAM_API:-https://api.telegram.org}"
KEY_FILE="${TELEGRAM_KEY_FILE:-$HOME/.telegram_key}"
CHAT_FILE="${TELEGRAM_CHAT_FILE:-$HOME/.telegram_chat}"

sender="${1:-}"
if [ -z "$sender" ]; then
    echo "usage: notify.sh <sender> [text]   (text may come from stdin)" >&2
    exit 1
fi
shift

if [ $# -gt 0 ]; then
    text="$*"
else
    text="$(cat)"
fi

if [ -z "$text" ]; then
    echo "notify.sh: refusing to send an empty message" >&2
    exit 1
fi

read_secret() {
    # $1 = env value, $2 = file path. Env wins; file is trimmed of whitespace.
    if [ -n "$1" ]; then
        printf '%s' "$1"
    elif [ -r "$2" ]; then
        tr -d ' \t\n\r' < "$2"
    fi
}

token="$(read_secret "${TELEGRAM_BOT_TOKEN:-}" "$KEY_FILE")"
chat="$(read_secret "${TELEGRAM_CHAT_ID:-}" "$CHAT_FILE")"

if [ -z "$token" ] || [ -z "$chat" ]; then
    echo "notify.sh: not configured (need $KEY_FILE and $CHAT_FILE, or the env vars)" >&2
    exit 2
fi

# Compose with the sender prefix, then let jq build the JSON body: the text is
# arbitrary and may contain quotes, newlines and backslashes.
body="$(printf '[%s] %s' "$sender" "$text" \
    | jq -Rs --arg chat "$chat" '{chat_id: $chat, text: ., disable_web_page_preview: true}')"

resp="$(curl -sS --max-time 15 -X POST \
    -H 'Content-Type: application/json' \
    -d "$body" \
    "$API/bot$token/sendMessage" 2>&1)" || {
        echo "notify.sh: request failed: $resp" >&2
        exit 3
    }

if [ "$(printf '%s' "$resp" | jq -r '.ok // false')" != "true" ]; then
    # Never echo the response wholesale — the URL it came from carries the token.
    echo "notify.sh: Telegram refused: $(printf '%s' "$resp" | jq -r '.description // "unparseable reply"')" >&2
    exit 3
fi

exit 0
