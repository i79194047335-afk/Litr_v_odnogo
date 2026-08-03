#!/bin/sh
# Tests for notify.sh. No network, no real credentials: a local HTTP stub
# plays Telegram, and the key/chat files are pointed at a temp dir.
#
# Failure modes are the point here. notify.sh exists so an agent stops being
# mute; a delivery script that reports success while dropping messages is
# worse than no script at all.
#
#   ./scripts/test_notify.sh

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
NOTIFY="$HERE/notify.sh"
TMP="$(mktemp -d)"
PORT=${PORT:-18099}
PASS=0
FAIL=0

cleanup() {
    [ -n "${STUB_PID:-}" ] && kill "$STUB_PID" 2>/dev/null || true
    rm -rf "$TMP"
}
trap cleanup EXIT INT TERM

ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; }

check_eq() {
    # check_eq <label> <expected> <actual>
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$2', got '$3')"; fi
}

# --- the stub -------------------------------------------------------------
# Writes each request body to $TMP/last_body and replies with whatever
# $TMP/reply says. Keeps the test honest about what was actually sent.
cat > "$TMP/stub.py" <<'PY'
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TMP = sys.argv[1]
PORT = int(sys.argv[2])

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(n)
        with open(os.path.join(TMP, 'last_body'), 'wb') as f:
            f.write(body)
        with open(os.path.join(TMP, 'last_path'), 'w') as f:
            f.write(self.path)
        mode = 'ok'
        p = os.path.join(TMP, 'reply')
        if os.path.exists(p):
            mode = open(p).read().strip()
        if mode == 'ok':
            code, payload = 200, {"ok": True, "result": {"message_id": 1}}
        elif mode == 'refuse':
            code, payload = 400, {"ok": False, "description": "chat not found"}
        else:
            code, payload = 200, {"ok": False}
        out = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass

HTTPServer(('127.0.0.1', PORT), H).serve_forever()
PY

python3 "$TMP/stub.py" "$TMP" "$PORT" &
STUB_PID=$!

# Wait for the stub to accept connections rather than sleeping a fixed amount.
i=0
while [ $i -lt 50 ]; do
    if curl -s -o /dev/null --max-time 1 -X POST "http://127.0.0.1:$PORT/ping"; then break; fi
    i=$((i + 1))
    sleep 0.1
done
[ $i -lt 50 ] || { echo "stub did not come up on port $PORT"; exit 1; }

printf 'test-token' > "$TMP/key"
printf '12345'      > "$TMP/chat"

export TELEGRAM_API="http://127.0.0.1:$PORT"
export TELEGRAM_KEY_FILE="$TMP/key"
export TELEGRAM_CHAT_FILE="$TMP/chat"
# Make sure ambient credentials can never leak into a test run.
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID

echo "notify.sh"

# 1. happy path
printf 'ok' > "$TMP/reply"
rc=0; "$NOTIFY" archivarius "hello" >/dev/null 2>&1 || rc=$?
check_eq "delivers and exits 0" 0 "$rc"

# 2. the sender prefix is actually in the payload
got="$(jq -r '.text' < "$TMP/last_body")"
check_eq "prefixes the sender" "[archivarius] hello" "$got"

# 3. chat id from the file reaches the API
check_eq "sends the configured chat id" "12345" "$(jq -r '.chat_id' < "$TMP/last_body")"

# 4. token goes into the URL path
check_eq "uses the token in the path" "/bottest-token/sendMessage" "$(cat "$TMP/last_path")"

# 5. stdin is accepted
rc=0; printf 'from stdin' | "$NOTIFY" collector >/dev/null 2>&1 || rc=$?
check_eq "reads text from stdin" "[collector] from stdin" "$(jq -r '.text' < "$TMP/last_body")"

# 6. text that would break naive JSON quoting
tricky='he said "hi"
line2 \ backslash'
"$NOTIFY" arch "$tricky" >/dev/null 2>&1 || true
check_eq "survives quotes, newlines and backslashes" "[arch] $tricky" "$(jq -r '.text' < "$TMP/last_body")"

# 7. Telegram refuses -> exit 3, not silent success
printf 'refuse' > "$TMP/reply"
rc=0; "$NOTIFY" arch "will be refused" >/dev/null 2>&1 || rc=$?
check_eq "reports refusal with exit 3" 3 "$rc"

# 8. ok:false with HTTP 200 is still a failure
printf 'notok' > "$TMP/reply"
rc=0; "$NOTIFY" arch "ok false" >/dev/null 2>&1 || rc=$?
check_eq "treats ok:false as failure" 3 "$rc"

# 9. the error message must not leak the token
printf 'refuse' > "$TMP/reply"
err="$("$NOTIFY" arch "leak check" 2>&1 >/dev/null || true)"
if printf '%s' "$err" | grep -q 'test-token'; then
    bad "error output leaks the token"
else
    ok "error output does not leak the token"
fi

# 10. unreachable endpoint -> exit 3
printf 'ok' > "$TMP/reply"
rc=0; TELEGRAM_API="http://127.0.0.1:1" "$NOTIFY" arch "nowhere" >/dev/null 2>&1 || rc=$?
check_eq "reports network failure with exit 3" 3 "$rc"

# 11. missing credentials -> exit 2, distinct from a delivery failure
rc=0; TELEGRAM_KEY_FILE="$TMP/nope" "$NOTIFY" arch "no key" >/dev/null 2>&1 || rc=$?
check_eq "unconfigured exits 2, not 3" 2 "$rc"

# 12. no sender -> usage error
rc=0; "$NOTIFY" >/dev/null 2>&1 || rc=$?
check_eq "missing sender exits 1" 1 "$rc"

# 13. empty text is refused rather than sent as an empty message
rc=0; printf '' | "$NOTIFY" arch >/dev/null 2>&1 || rc=$?
check_eq "empty text exits 1" 1 "$rc"

# 14. env vars win over the files
printf 'ok' > "$TMP/reply"
TELEGRAM_BOT_TOKEN=envtoken TELEGRAM_CHAT_ID=999 "$NOTIFY" arch "via env" >/dev/null 2>&1 || true
check_eq "env overrides the key file" "/botenvtoken/sendMessage" "$(cat "$TMP/last_path")"
check_eq "env overrides the chat file" "999" "$(jq -r '.chat_id' < "$TMP/last_body")"

echo
echo "passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
