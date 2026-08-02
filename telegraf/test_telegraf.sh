#!/usr/bin/env bash
#
# Тесты Телеграфа. Сети и ключа не требуют: канал и API модели отдают локальные
# заглушки, состояние живёт во временном каталоге.
#
# Проверяются режимы отказа, а не happy path. Агент дописывает в постоянную базу
# знаний, и тихий баг здесь не падает, а портит запись — как это уже случилось
# у Архивариуса (10 негодных ответов из 103, три по 18 КБ повтора).
#
#   ./telegraf/test_telegraf.sh

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$HERE/telegraf.sh"
WORK="$(mktemp -d)"
CH_PORT=8741
API_PORT=8742

passed=0
failed=0
ok()   { passed=$(( passed + 1 )); printf '  \033[32mOK\033[0m   %s\n' "$1"; }
fail() { failed=$(( failed + 1 )); printf '  \033[31mFAIL\033[0m %s\n' "$1"
         [[ -n "${2:-}" ]] && printf '       %s\n' "$2"; }

cleanup() {
    [[ -n "${CH_PID:-}"  ]] && kill "$CH_PID"  2>/dev/null
    [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

# --- заглушка канала ------------------------------------------------------
# Отдаёт содержимое $WORK/page.html по любому пути. Так тест управляет тем,
# что «опубликовано в канале», не трогая сеть.
SRV_ROOT="$WORK/www"; mkdir -p "$SRV_ROOT"
cat > "$WORK/chan.py" <<PY
import os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
ROOT = "$SRV_ROOT"
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = os.path.join(ROOT, "page.html")
        if not os.path.exists(p):
            self.send_error(404); return
        body = open(p, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", $CH_PORT), H).serve_forever()
PY
python3 "$WORK/chan.py" >/dev/null 2>&1 &
CH_PID=$!

# --- заглушка модели ------------------------------------------------------
# Отдаёт то, что лежит в $WORK/reply — так тест задаёт поведение модели,
# включая вырождение и прозу вместо JSON.
cat > "$WORK/api.py" <<PY
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
WORK = "$WORK"
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        mode = "ok"
        p = os.path.join(WORK, "reply")
        if os.path.exists(p):
            mode = open(p).read().strip()
        if mode == "breaking":
            content = json.dumps({"summary": "Поле remaining_usage удалено",
                                  "kind": "api", "breaking": True,
                                  "highlights": ["remaining_usage removed"]})
        elif mode == "quiet":
            content = json.dumps({"summary": "Выпущен SDK v1.0.7",
                                  "kind": "release", "breaking": False,
                                  "highlights": ["v1.0.7"]})
        elif mode == "prose":
            content = "Модель решила ответить прозой без всякого JSON."
        elif mode == "degenerate":
            content = "спам " * 300
        elif mode == "empty":
            content = ""
        else:
            content = json.dumps({"summary": "Обычное изменение", "kind": "other",
                                  "breaking": False, "highlights": []})
        out = json.dumps({"choices": [{"message": {"content": content},
                                       "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", $API_PORT), H).serve_forever()
PY
python3 "$WORK/api.py" >/dev/null 2>&1 &
API_PID=$!

for _ in $(seq 40); do
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$CH_PORT/" && break
    sleep 0.1
done

# --- вспомогательное ------------------------------------------------------

# Собирает страницу канала из пар «номер:текст».
make_page() {
    { printf '<html><body>\n'
      for spec in "$@"; do
          local id="${spec%%:*}" txt="${spec#*:}"
          printf '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
          printf '<div class="tgme_widget_message" data-post="testchan/%s">' "$id"
          printf '<div class="tgme_widget_message_text js-message_text" dir="auto">%s</div>' "$txt"
          printf '<time datetime="2026-08-03T10:00:00+00:00"></time>'
          printf '</div></div>\n'
      done
      printf '</body></html>\n'
    } > "$SRV_ROOT/page.html"
}

setup_box() {  # $1 = имя песочницы, $2 = код возврата канала доставки
    local box="$WORK/$1"
    rm -rf "$box"; mkdir -p "$box"
    cp "$AGENT" "$box/telegraf.sh"
    cp "$HERE/parse_posts.py" "$box/parse_posts.py"
    chmod +x "$box/telegraf.sh"
    printf 'sk-stub\n' > "$box/.key"
    { printf '#!/bin/sh\n'
      printf 'printf "%%s" "$1" > "%s/notified.sender"\n' "$box"
      printf 'cat > "%s/notified.body"\n' "$box"
      printf 'exit %s\n' "${2:-0}"
    } > "$box/notify.sh"
    chmod +x "$box/notify.sh"
    printf '%s' "$box"
}

run_box() {
    local box="$1"
    ( cd "$box" && env -u DEEPSEEK_API_KEY \
        DEEPSEEK_KEY_FILE="$box/.key" \
        TELEGRAF_CHANNEL="testchan" \
        TELEGRAF_BASE_URL="http://127.0.0.1:$CH_PORT" \
        TELEGRAF_API_URL="http://127.0.0.1:$API_PORT/" \
        NOTIFY_CMD="$box/notify.sh" \
        timeout 60 ./telegraf.sh 2>&1 )
}

# Слепок настоящего состояния агента ДО тестов — проверяется последним тестом.
STATE_BEFORE="$( { md5sum "$HERE/knowledge/"* 2>/dev/null
                   md5sum "$HERE/state/"*     2>/dev/null; } | sort )"

echo "=== Телеграф ==="

# 1. Первый прогон только ставит отметку: разбирать историю канала незачем,
#    она уже случилась, а токены стоят денег.
printf 'ok' > "$WORK/reply"
make_page "10:первый" "11:второй" "12:третий"
box=$(setup_box first_run)
out=$(run_box "$box"); rc=$?
if [[ "$rc" -eq 0 ]] && grep -q "модель не вызывалась" <<<"$out" \
   && [[ "$(cat "$box/state/last_post_id")" == "12" ]] \
   && [[ ! -f "$box/knowledge/events.jsonl" ]]; then
    ok "первый прогон: отметка поставлена, модель не вызывалась"
else
    fail "первый прогон" "rc=$rc, отметка=$(cat "$box/state/last_post_id" 2>/dev/null)"
fi

# 2. Ничего нового — модель не трогаем.
out=$(run_box "$box"); rc=$?
if [[ "$rc" -eq 0 ]] && grep -q "новых постов нет" <<<"$out"; then
    ok "без новых постов: модель не вызывается"
else
    fail "без новых постов" "rc=$rc"
fi

# 3. Появился новый пост — он и только он идёт в модель и в журнал.
make_page "10:первый" "11:второй" "12:третий" "13:новый пост про SDK"
out=$(run_box "$box"); rc=$?
n=$(wc -l < "$box/knowledge/events.jsonl" 2>/dev/null || echo 0)
if [[ "$rc" -eq 0 && "$n" -eq 1 ]] \
   && [[ "$(jq -r '.post_id' "$box/knowledge/events.jsonl")" == "13" ]] \
   && [[ "$(cat "$box/state/last_post_id")" == "13" ]]; then
    ok "новый пост: разобран один, отметка сдвинута"
else
    fail "новый пост" "rc=$rc, событий=$n, отметка=$(cat "$box/state/last_post_id" 2>/dev/null)"
fi

# 4. Старые посты не перечитываются: отметка — это и есть всё состояние.
before=$(wc -l < "$box/knowledge/events.jsonl")
out=$(run_box "$box")
after=$(wc -l < "$box/knowledge/events.jsonl")
if [[ "$before" -eq "$after" ]]; then
    ok "повторный прогон: старые посты не перечитываются"
else
    fail "повторный прогон" "событий было $before, стало $after"
fi

# 5. Доказательство рядом с выводом: без исходного текста пересказ нечем
#    перепроверить, а пост в канале могут отредактировать или удалить.
ev=$(jq -r 'select(.post_id==13) | .evidence' "$box/knowledge/events.jsonl")
if [[ "$ev" == "новый пост про SDK" ]]; then
    ok "событие несёт исходный текст поста"
else
    fail "evidence" "получено: '$ev'"
fi

# 6. Проза вместо JSON: текст не теряем, но помечаем kind='?'.
printf 'prose' > "$WORK/reply"
make_page "10:a" "11:b" "12:c" "13:d" "14:пост с прозаичным ответом"
out=$(run_box "$box")
last=$(tail -1 "$box/knowledge/events.jsonl")
if [[ "$(jq -r '.kind' <<<"$last")" == "?" ]] \
   && grep -q "прозой" <<<"$(jq -r '.summary' <<<"$last")"; then
    ok "проза вместо JSON: текст сохранён, kind='?'"
else
    fail "проза вместо JSON" "$(jq -c '{kind,summary}' <<<"$last")"
fi

# 7. Вырождение в повтор — мусор в базу не пускаем, отметка не двигается.
printf 'degenerate' > "$WORK/reply"
mark_before=$(cat "$box/state/last_post_id")
ev_before=$(wc -l < "$box/knowledge/events.jsonl")
make_page "10:a" "11:b" "12:c" "13:d" "14:e" "15:пост, на котором модель сорвётся"
out=$(run_box "$box")
mark_after=$(cat "$box/state/last_post_id")
ev_after=$(wc -l < "$box/knowledge/events.jsonl")
if grep -q "выродился в повтор" <<<"$out" \
   && [[ "$ev_before" -eq "$ev_after" && "$mark_before" == "$mark_after" ]]; then
    ok "вырождение: отброшено, в базу не попало, отметка на месте"
else
    fail "вырождение" "событий $ev_before->$ev_after, отметка $mark_before->$mark_after"
fi

# 8. Пустой ответ модели — тот же отказ: пост будет повторён.
printf 'empty' > "$WORK/reply"
ev_before=$(wc -l < "$box/knowledge/events.jsonl")
out=$(run_box "$box")
ev_after=$(wc -l < "$box/knowledge/events.jsonl")
if grep -q "модель не ответила" <<<"$out" && [[ "$ev_before" -eq "$ev_after" ]]; then
    ok "пустой ответ: в базу не пишем, пост повторится"
else
    fail "пустой ответ" "событий $ev_before->$ev_after"
fi

# 9. После сбоя пост разбирается на следующем прогоне — не потерян навсегда.
printf 'quiet' > "$WORK/reply"
out=$(run_box "$box")
if [[ "$(cat "$box/state/last_post_id")" == "15" ]] \
   && [[ "$(tail -1 "$box/knowledge/events.jsonl" | jq -r '.post_id')" == "15" ]]; then
    ok "после сбоя: пост подхвачен следующим прогоном"
else
    fail "восстановление после сбоя" "отметка=$(cat "$box/state/last_post_id")"
fi

# 10. Два выхода обязаны сходиться по счёту (проза против JSONL).
posts_in_md=$(grep -c '^### \[Пост' "$box/knowledge/updates.md" 2>/dev/null || echo 0)
events=$(wc -l < "$box/knowledge/events.jsonl")
if [[ "$posts_in_md" -eq "$events" ]]; then
    ok "проза и JSONL сходятся по счёту ($events)"
else
    fail "расхождение выходов" "в md $posts_in_md, в jsonl $events"
fi

# 11. breaking: true — канал доставки обязан быть вызван.
printf 'breaking' > "$WORK/reply"
box2=$(setup_box notify_ok 0)
make_page "20:старый"
run_box "$box2" >/dev/null
make_page "20:старый" "21:удалено поле remaining_usage"
out=$(run_box "$box2")
if [[ -f "$box2/notified.body" ]] \
   && grep -q "BREAKING" "$box2/notified.body" \
   && [[ "$(cat "$box2/notified.sender")" == "telegraf" ]]; then
    ok "breaking: уведомление отправлено от имени telegraf"
else
    fail "breaking: уведомление" "файл: $([[ -f "$box2/notified.body" ]] && echo есть || echo нет)"
fi

# 12. Не-breaking молчит: алерт на каждый релиз SDK обесценит алерты.
printf 'quiet' > "$WORK/reply"
box3=$(setup_box notify_quiet 0)
make_page "30:старый"
run_box "$box3" >/dev/null
make_page "30:старый" "31:вышел SDK v1.0.7"
run_box "$box3" >/dev/null
if [[ ! -f "$box3/notified.body" ]]; then
    ok "не-breaking: канал доставки не дёргается"
else
    fail "не-breaking" "уведомление ушло на обычный релиз"
fi

# 13. Канал недоступен — состояние не трогаем.
printf 'quiet' > "$WORK/reply"
box4=$(setup_box chan_down)
make_page "40:пост"
run_box "$box4" >/dev/null
mark_before=$(cat "$box4/state/last_post_id")
rm -f "$SRV_ROOT/page.html"
out=$(run_box "$box4"); rc=$?
mark_after=$(cat "$box4/state/last_post_id")
if [[ "$rc" -ne 0 && "$mark_before" == "$mark_after" ]]; then
    ok "канал недоступен: прогон прерван, отметка цела"
else
    fail "канал недоступен" "rc=$rc, отметка $mark_before->$mark_after"
fi

# 14. Страница есть, но постов в ней нет — это смена формата или поломка
#     парсера, а не «канал молчит». Разница обязана быть видна в логе.
printf '<html><body>совсем другая разметка</body></html>' > "$SRV_ROOT/page.html"
mark_before=$(cat "$box4/state/last_post_id")
out=$(run_box "$box4"); rc=$?
mark_after=$(cat "$box4/state/last_post_id")
if [[ "$rc" -ne 0 ]] && grep -q "смена формата\|не дал постов" <<<"$out" \
   && [[ "$mark_before" == "$mark_after" ]]; then
    ok "нулевой разбор: отличён от тишины, состояние не тронуто"
else
    fail "нулевой разбор" "rc=$rc, отметка $mark_before->$mark_after"
fi

# 15. Слишком много новых постов разом — предохранитель: либо канал взорвался,
#     либо потерялась отметка, и то и другое стоит увидеть до траты токенов.
box5=$(setup_box flood)
make_page "1:a"
run_box "$box5" >/dev/null
specs=(); for i in $(seq 2 40); do specs+=("$i:пост $i"); done
make_page "1:a" "${specs[@]}"
out=$(run_box "$box5"); rc=$?
if [[ "$rc" -ne 0 ]] && grep -q "при пороге" <<<"$out" \
   && [[ ! -f "$box5/knowledge/events.jsonl" ]]; then
    ok "поток постов: предохранитель сработал, токены не потрачены"
else
    fail "предохранитель" "rc=$rc"
fi

# 16. Пост без текста (фото) не должен застревать: отметка идёт дальше.
printf 'quiet' > "$WORK/reply"
box6=$(setup_box textless)
make_page "50:обычный"
run_box "$box6" >/dev/null
{ printf '<html><body>\n'
  printf '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
  printf '<div class="tgme_widget_message" data-post="testchan/50">'
  printf '<div class="tgme_widget_message_text js-message_text">обычный</div>'
  printf '<time datetime="2026-08-03T10:00:00+00:00"></time></div></div>\n'
  printf '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
  printf '<div class="tgme_widget_message" data-post="testchan/51">'
  printf '<div class="tgme_widget_message_photo"></div>'
  printf '<time datetime="2026-08-03T11:00:00+00:00"></time></div></div>\n'
  printf '</body></html>\n'
} > "$SRV_ROOT/page.html"
out=$(run_box "$box6")
if [[ "$(cat "$box6/state/last_post_id")" == "51" ]] \
   && grep -q "новых постов нет" <<<"$out"; then
    ok "пост без текста: отметка проходит мимо, не застревая"
else
    fail "пост без текста" "отметка=$(cat "$box6/state/last_post_id")"
fi

# 17. Тесты не пишут в настоящую базу знаний — правило, которое уже стоило
#     Архивариусу двух выдуманных записей в постоянном журнале.
#
#     Сравниваем состояние ДО и ПОСЛЕ, а не «чистоту» рабочего дерева: файлы
#     могут быть законно грязными от живого прогона или ещё не закоммичены,
#     и это не повод объявлять тесты виновными.
fingerprint() {
    { md5sum "$HERE/knowledge/"* 2>/dev/null
      md5sum "$HERE/state/"*     2>/dev/null; } | sort
}
if [[ "$STATE_BEFORE" == "$(fingerprint)" ]]; then
    ok "прогон тестов не тронул knowledge/ и state/ агента"
else
    fail "тесты изменили настоящее состояние агента" \
         "$(diff <(printf '%s\n' "$STATE_BEFORE") <(fingerprint) | head -5)"
fi

printf '\n%s\n' "----------------------------------------"
printf 'пройдено: %d, провалено: %d\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
