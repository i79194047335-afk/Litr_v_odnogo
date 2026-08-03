#!/usr/bin/env bash
#
# Тесты Архивариуса — на режимы отказа, не на happy path.
#
# Агент дописывает в постоянную базу знаний, и тихий баг здесь не падает,
# а портит запись. Каждый тест ниже соответствует поломке, которая уже
# случалась или которую состояние на диске переживёт молча.
#
# Сеть не нужна: индекс и страницы подаются с локального файлового сервера,
# ответы модели — заглушками. Запуск: ./test_archivarius.sh

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$HERE/archivarius.sh"

passed=0; failed=0
WORK="$(mktemp -d)"
STUB_PIDS=""
cleanup() { rm -rf "$WORK"; [ -n "$STUB_PIDS" ] && kill $STUB_PIDS 2>/dev/null; return 0; }
trap cleanup EXIT INT TERM

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; passed=$(( passed + 1 )); }
fail() { printf '  \033[31mFAIL\033[0m %s\n     %s\n' "$1" "${2:-}"; failed=$(( failed + 1 )); }

# --- вспомогательное ----------------------------------------------------

# Логика берётся ИЗ агента, а не переписывается здесь. Копия в тесте
# бессмысленна: сломай агента — тест останется зелёным. Проверено намеренной
# поломкой, копия её не поймала.

# Строка вида:  parsed=$(printf '%s' "$raw" | <конвейер> \
JSON_PIPE=$(grep -F 'parsed=$(printf' "$AGENT" | head -1 \
            | sed -e 's/.*"\$raw" | //' -e 's/[[:space:]]*\\$//')
[[ -z "$JSON_PIPE" ]] && { echo "не удалось извлечь разбор JSON из агента"; exit 1; }

extract_json_parse() {
    printf '%s' "$1" | eval "$JSON_PIPE" | jq -c '{summary, kind, breaking, highlights}' 2>/dev/null
}

# Порог деградации — тоже из агента, чтобы правка порога не разошлась с тестом.
DEGEN_MIN_WORDS=$(grep -o '"\${top_share:-0}" -gt [0-9]*' "$AGENT" | grep -o '[0-9]*$')
DEGEN_PCT=$(grep -o 'dominant \* 100 / top_share )) -ge [0-9]*' "$AGENT" | grep -o '[0-9]*$')
: "${DEGEN_MIN_WORDS:=40}" "${DEGEN_PCT:=50}"

is_degenerate() {
    local summary="$1" total dominant
    total=$(printf '%s' "$summary" | tr -s '[:space:]' '\n' | grep -c .)
    [[ "$total" -le "$DEGEN_MIN_WORDS" ]] && return 1
    dominant=$(printf '%s' "$summary" | tr -s '[:space:]' '\n' | sort | uniq -c | sort -rn | head -1 | awk '{print $1}')
    [[ $(( dominant * 100 / total )) -ge "$DEGEN_PCT" ]]
}

echo "=== Разбор ответа модели ==="

# 31 июля модель вернула валидный JSON за префиксом " в JSON." — снятие
# ```-обёртки его не поймало, и содержательный ответ ушёл в мусор.
out=$(extract_json_parse ' в JSON.{"summary":"т","kind":"docs","breaking":false,"highlights":[]}')
[[ "$(printf '%s' "$out" | jq -r .kind 2>/dev/null)" == "docs" ]] \
    && ok "JSON за мусорным префиксом разбирается" \
    || fail "JSON за мусорным префиксом" "получено: $out"

out=$(extract_json_parse '```json
{"summary":"т","kind":"api","breaking":true,"highlights":[]}
```')
[[ "$(printf '%s' "$out" | jq -r .breaking 2>/dev/null)" == "true" ]] \
    && ok "JSON в markdown-обёртке разбирается" \
    || fail "JSON в markdown-обёртке" "получено: $out"

out=$(extract_json_parse 'Просто проза, никакого JSON здесь нет')
[[ -z "$out" ]] \
    && ok "проза не выдаётся за JSON" \
    || fail "проза принята за JSON" "получено: $out"

echo "=== Деградация модели ==="

# Три страницы 31 июля пришли как "response. response." на 18 КБ и осели
# в базе знаний как наблюдения.
degen=$(python3 -c "print('response. ' * 300)")
is_degenerate "$degen" \
    && ok "повтор одного слова отбраковывается" \
    || fail "повтор одного слова принят как текст"

normal=$(python3 -c "print('Изменён лимит запросов для Standard accounts с 30 до 60 в минуту. ' * 6)")
is_degenerate "$normal" \
    && fail "осмысленный текст принят за деградацию" \
    || ok "осмысленный текст не отбраковывается"

short="Косметическая правка, breaking changes нет."
is_degenerate "$short" \
    && fail "короткий ответ принят за деградацию" \
    || ok "короткий ответ не отбраковывается"

echo "=== Переносимость awk ==="

# На дефолтной Ubuntu awk — это mawk, где трёхаргументный match() (расширение
# gawk) падает синтаксической ошибкой. Ноль строк на выходе неотличим от
# "Lighter сменил формат индекса", так что агент молча останавливался бы каждый
# день, не сообщая ничего осмысленного.
AWK_PROG="$WORK/parser.awk"
sed -n '/^awk .$/,/^. "\$INDEX"/p' "$AGENT" | sed -e '1s/^awk .//' -e '$d' > "$AWK_PROG"
cat > "$WORK/idx_sample.txt" <<'SAMPLE'
## Guides
- [Get Started](https://apidocs.lighter.xyz/docs/get-started.md)
- [Rate Limits](https://apidocs.lighter.xyz/docs/rate-limits.md): описание
## API Reference
- [status](https://apidocs.lighter.xyz/reference/status.md)
- [Относительная](/local/path.md)
SAMPLE

for impl in gawk mawk awk; do
    command -v "$impl" >/dev/null || { ok "$impl не установлен — пропуск"; continue; }
    got=$("$impl" -f "$AWK_PROG" "$WORK/idx_sample.txt" 2>"$WORK/awk.err" | wc -l)
    if [[ "$got" -eq 3 ]]; then
        ok "$impl: индекс разобран (3 страницы, относительный URL отброшен)"
    else
        fail "$impl: разобрано $got вместо 3" "$(head -1 "$WORK/awk.err")"
    fi
done

echo "=== Прогон агента на битых входных данных ==="

# Локальный сервер вместо сети: агент должен вести себя одинаково с любым
# источником, а тест не должен зависеть от доступности apidocs.lighter.xyz.
SRV_ROOT="$WORK/www"; mkdir -p "$SRV_ROOT"
python3 -m http.server 8731 --directory "$SRV_ROOT" >/dev/null 2>&1 &
SRV_PID=$!
STUB_PIDS="$STUB_PIDS $SRV_PID"
sleep 1

# Готовит изолированную копию агента, смотрящую на локальный индекс.
setup_sandbox() {
    local box="$WORK/$1"; shift
    rm -rf "$box"; mkdir -p "$box"
    sed -e "s|^INDEX_URLS=.*|INDEX_URLS=\"http://127.0.0.1:8731/llms.txt\"|" \
        "$AGENT" > "$box/archivarius.sh"
    chmod +x "$box/archivarius.sh"
    printf 'sk-not-used-in-these-tests\n' > "$box/.key"
    printf '%s' "$box"
}

run_agent() {  # box -> stdout+stderr, код возврата в $?
    local box="$1"
    ( cd "$box" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box/.key" \
        timeout 60 ./archivarius.sh 2>&1 )
}

# 1. Индекс недоступен: состояние не должно пострадать.
box=$(setup_sandbox idx_down)
mkdir -p "$box/state/snapshots"
printf 'СТАРОЕ СОДЕРЖИМОЕ\n' > "$box/state/snapshots/aaa.md"
before=$(sha256sum "$box/state/snapshots/aaa.md" | cut -d' ' -f1)
rm -f "$SRV_ROOT/llms.txt"
out=$(run_agent "$box"); rc=$?
after=$(sha256sum "$box/state/snapshots/aaa.md" | cut -d' ' -f1)
if [[ "$rc" -ne 0 && "$before" == "$after" ]]; then
    ok "недоступный индекс: прогон прерван, снимки целы"
else
    fail "недоступный индекс" "rc=$rc, снимок изменился: $([[ $before != $after ]] && echo да || echo нет)"
fi

# 2. Индекс есть, но распарсился в ноль страниц (сменился формат) —
#    состояние не должно затираться пустотой.
box=$(setup_sandbox idx_empty)
mkdir -p "$box/state/snapshots"
printf 'СТАРОЕ СОДЕРЖИМОЕ\n' > "$box/state/snapshots/aaa.md"
before=$(ls "$box/state/snapshots" | wc -l)
printf '# Заголовок без единой ссылки\n\nтекст\n' > "$SRV_ROOT/llms.txt"
out=$(run_agent "$box"); rc=$?
after=$(ls "$box/state/snapshots" | wc -l)
if [[ "$rc" -ne 0 && "$before" -eq "$after" ]] && grep -q "0 страниц" <<<"$out"; then
    ok "пустой индекс: прогон прерван, снимки на месте"
else
    fail "пустой индекс" "rc=$rc, снимков было $before стало $after"
fi

# 3. Часть страниц недоступна: прогон продолжается, а не падает.
box=$(setup_sandbox page_404)
{ printf '## Guides\n'
  printf -- '- [Живая](http://127.0.0.1:8731/live.md)\n'
  printf -- '- [Мёртвая](http://127.0.0.1:8731/missing.md)\n'
} > "$SRV_ROOT/llms.txt"
printf 'содержимое живой страницы\n' > "$SRV_ROOT/live.md"
rm -f "$SRV_ROOT/missing.md"
out=$(run_agent "$box"); rc=$?
if [[ "$rc" -eq 0 ]] && grep -q "не открылось: 1" <<<"$out"; then
    ok "недоступная страница: учтена, прогон продолжен"
else
    fail "недоступная страница" "rc=$rc; $(grep -o 'скачано:.*' <<<"$out")"
fi

# 4. Первый прогон не должен обращаться к модели: сравнивать не с чем.
box=$(setup_sandbox first_run)
out=$(run_agent "$box"); rc=$?
if [[ "$rc" -eq 0 ]] && grep -q "обращений к модели не было" <<<"$out"; then
    ok "первый прогон: слепок без вызовов модели"
else
    fail "первый прогон" "rc=$rc"
fi

# 5. Повторный прогон без изменений — тоже без вызовов модели.
out=$(run_agent "$box"); rc=$?
if [[ "$rc" -eq 0 ]] && grep -q "изменений нет" <<<"$out"; then
    ok "прогон без изменений: модель не вызывается"
else
    fail "прогон без изменений" "rc=$rc"
fi

# 6. Изменение есть, но ключ негодный: снимок обязан откатиться, иначе
#    правка будет считаться описанной и потеряется навсегда.
printf 'содержимое живой страницы, ИЗМЕНЁННОЕ\n' > "$SRV_ROOT/live.md"
key=$(printf '%s' "http://127.0.0.1:8731/live.md" | sha256sum | cut -d' ' -f1)
before=$(sha256sum "$box/state/snapshots/$key.md" | cut -d' ' -f1)
out=$(run_agent "$box"); rc=$?
after=$(sha256sum "$box/state/snapshots/$key.md" | cut -d' ' -f1)
if [[ "$before" == "$after" ]]; then
    ok "модель недоступна: снимок откачен, изменение не потеряно"
else
    fail "модель недоступна" "снимок затёрт — правка больше не всплывёт"
fi

echo "=== Два сайта документации ==="

# Второй индекс (docs.lighter.xyz) добавлен 2026-08-03. Проверяем не то, что
# «работает», а три вещи, которые молча ломаются: недоступность ОДНОГО из
# индексов, различимость источника в событии, и сохранность маркеров аудита
# в шапке журнала.

SRV2_ROOT="$WORK/www2"; mkdir -p "$SRV2_ROOT"
python3 -m http.server 8735 --directory "$SRV2_ROOT" >/dev/null 2>&1 &
SRV2_PID=$!

# Своя заглушка модели для этого блока: та, что ниже (порт 8734), поднимается
# позже по файлу, и обращаться к ней отсюда значило бы стучать в мёртвый порт.
cat > "$WORK/api_ok.py" <<PY_EOF
import json, os, time
from http.server import BaseHTTPRequestHandler, HTTPServer

# Файл-флаг с задержкой в секундах. Без него ответ мгновенный: локальная
# заглушка успевала обработать все страницы до kill, и тест на обрыв проверял
# бы happy path.
SLOW_FLAG = "$WORK/stub_slow"

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if os.path.exists(SLOW_FLAG):
            try:
                time.sleep(float(open(SLOW_FLAG).read().strip()))
            except ValueError:
                pass
        body = json.dumps({"summary": "Изменение описано", "kind": "docs",
                           "breaking": False, "highlights": ["\u043f\u0443\u043d\u043a\u0442"]})
        out = json.dumps({"choices": [{"message": {"content": body},
                                       "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

HTTPServer(('127.0.0.1', 8736), H).serve_forever()
PY_EOF
python3 "$WORK/api_ok.py" >/dev/null 2>&1 &
API_OK_PID=$!
STUB_PIDS="$STUB_PIDS $SRV2_PID $API_OK_PID"
for _ in $(seq 30); do
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:8735/" && break
    sleep 0.1
done
for _ in $(seq 30); do
    curl -s -o /dev/null --max-time 1 -X POST "http://127.0.0.1:8736/" && break
    sleep 0.1
done

setup_two_site_box() {
    local box="$WORK/$1"
    rm -rf "$box"; mkdir -p "$box"
    sed -e "s|^INDEX_URLS=.*|INDEX_URLS=\"http://127.0.0.1:8731/llms.txt http://127.0.0.1:8735/llms.txt\"|" \
        "$AGENT" > "$box/archivarius.sh"
    chmod +x "$box/archivarius.sh"
    printf 'sk-stub\n' > "$box/.key"
    printf '%s' "$box"
}

# 1. Оба индекса на месте — страницы обоих сайтов попадают в обход.
{ printf '## A\n'; printf -- '- [Первый](http://127.0.0.1:8731/one.md)\n'; } > "$SRV_ROOT/llms.txt"
{ printf '## B\n'; printf -- '- [Второй](http://127.0.0.1:8735/two.md)\n'; } > "$SRV2_ROOT/llms.txt"
printf 'страница один\n' > "$SRV_ROOT/one.md"
printf 'страница два\n'  > "$SRV2_ROOT/two.md"
box=$(setup_two_site_box two_sites)
out=$( cd "$box" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box/.key" \
       timeout 60 ./archivarius.sh 2>&1 )
if grep -q "в индексе страниц: 2" <<<"$out"; then
    ok "два индекса: страницы обоих сайтов в обходе"
else
    fail "два индекса" "$(grep -o 'в индексе страниц: [0-9]*' <<<"$out")"
fi

# 2. Один индекс недоступен — прогон обязан прерваться, а не молча обойти
#    половину. Иначе страницы пропавшего сайта на следующем прогоне сочтутся
#    новыми, и модель опишет 35 «новых» страниц как изменения.
snaps_before=$(ls "$box/state/snapshots" 2>/dev/null | wc -l)
rm -f "$SRV2_ROOT/llms.txt"
out=$( cd "$box" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box/.key" \
       timeout 60 ./archivarius.sh 2>&1 ); rc=$?
snaps_after=$(ls "$box/state/snapshots" 2>/dev/null | wc -l)
if [[ "$rc" -ne 0 && "$snaps_before" -eq "$snaps_after" ]] \
   && grep -q "индекс недоступен" <<<"$out"; then
    ok "один индекс упал: прогон прерван, снимки целы"
else
    fail "один индекс упал" "rc=$rc, снимков $snaps_before->$snaps_after"
fi

# 3. Источник в событии различает сайты. Без этого потребитель не отличит
#    правку технического референса от правки концептуальной доки.
{ printf '## B\n'; printf -- '- [Второй](http://127.0.0.1:8735/two.md)\n'; } > "$SRV2_ROOT/llms.txt"
box2=$(setup_two_site_box source_tag)
sed -i "s|^API_URL=.*|API_URL=\"http://127.0.0.1:8736/\"|" "$box2/archivarius.sh"
( cd "$box2" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box2/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
printf 'страница один, правка\n' > "$SRV_ROOT/one.md"
printf 'страница два, правка\n'  > "$SRV2_ROOT/two.md"
( cd "$box2" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box2/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
srcs=$(jq -r '.source' "$box2/knowledge/events.jsonl" 2>/dev/null | sort -u | paste -sd,)
if [[ "$srcs" == "lighter-unknown" ]]; then
    # Локальные заглушки не совпадают ни с одним доменом Lighter — это
    # правильное поведение: неизвестный домен помечается, а не приписывается
    # к apidocs по умолчанию.
    ok "источник: незнакомый домен помечен явно (lighter-unknown)"
else
    fail "источник события" "получено: $srcs"
fi

# 4. Маркеры аудита в шапке журнала обязаны выжить при дописывании.
#    Раньше шапка отрезалась счётом строк (`tail -n +6`), а в живом журнале над
#    заголовком лежат 4 строки markdown-комментариев про удалённые выдумки и
#    негодные ответы. Следующая же правка снесла бы обе записи об аудите.
box3=$(setup_two_site_box audit_markers)
sed -i "s|^API_URL=.*|API_URL=\"http://127.0.0.1:8736/\"|" "$box3/archivarius.sh"
mkdir -p "$box3/knowledge"
cat > "$box3/knowledge/api_changelog.md" <<'OLDLOG'
<!-- marker one: synthetic entries purged -->
<!-- marker two: unusable replies replaced -->
# Lighter API — журнал изменений документации

Ведёт Архивариус автоматически. Источник: <http://old>
Новые записи сверху.

## 2026-07-31 — изменений: 1

### [Старая запись](http://old/page.md) — изменена
_Раздел: Old · тип: docs_

старое тело

OLDLOG
printf 'страница один\n' > "$SRV_ROOT/one.md"
printf 'страница два\n'  > "$SRV2_ROOT/two.md"
( cd "$box3" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box3/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
printf 'страница один, правка\n' > "$SRV_ROOT/one.md"
( cd "$box3" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box3/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
m1=$(grep -c 'marker one' "$box3/knowledge/api_changelog.md")
m2=$(grep -c 'marker two' "$box3/knowledge/api_changelog.md")
hdr=$(grep -c '^# Lighter API' "$box3/knowledge/api_changelog.md")
oldrec=$(grep -c 'Старая запись' "$box3/knowledge/api_changelog.md")
stale=$(grep -c 'Источник: <http://old>' "$box3/knowledge/api_changelog.md")
if [[ "$m1" -eq 1 && "$m2" -eq 1 && "$hdr" -eq 1 && "$oldrec" -eq 1 && "$stale" -eq 0 ]]; then
    ok "журнал: маркеры аудита целы, шапка одна, старые записи на месте"
else
    fail "журнал при дописывании" \
         "маркеры $m1/$m2, шапок $hdr, старых записей $oldrec, осиротевшая строка $stale"
fi

# 5. Отрезание шапки не должно зависеть от числа строк над заголовком.
#    Маркеров аудита может быть любое количество — их дописывает человек,
#    разбирая базу. При счёте строк (`tail -n +6`) лишние строки маркеров
#    съедали бы первые записи журнала, а недостающие — оставляли обрывки шапки.
#    Здесь маркеров ДВА. Число выбрано не произвольно: при одном или четырёх
#    `tail -n +6` случайно попадает верно, и тест был бы зелёным на сломанном
#    коде. Начиная с двух в журнале остаются обрывки прежней шапки.
box4=$(setup_two_site_box header_offset)
sed -i "s|^API_URL=.*|API_URL=\"http://127.0.0.1:8736/\"|" "$box4/archivarius.sh"
mkdir -p "$box4/knowledge"
cat > "$box4/knowledge/api_changelog.md" <<'ONEMARKER'
<!-- marker alpha -->
<!-- marker beta -->
# Lighter API — журнал изменений документации

Ведёт Архивариус автоматически. Источник: <http://old>
Новые записи сверху.

## 2026-07-30 — изменений: 1

### [Самая первая запись](http://old/first.md) — изменена
_Раздел: Old · тип: docs_

тело первой записи

ONEMARKER
printf 'страница один\n' > "$SRV_ROOT/one.md"
printf 'страница два\n'  > "$SRV2_ROOT/two.md"
( cd "$box4" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box4/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
printf 'страница один, правка\n' > "$SRV_ROOT/one.md"
( cd "$box4" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box4/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
kept=$(grep -c 'Самая первая запись' "$box4/knowledge/api_changelog.md")
mk=$(grep -cE 'marker alpha|marker beta' "$box4/knowledge/api_changelog.md")
# Симптом счёта строк — не осиротевший «Источник», а ДУБЛЬ фразы шапки,
# уехавший в тело журнала: при двух маркерах tail -n +6 отрезает на две строки
# меньше нужного. Проверено вручную на этом же входе.
orphan=$(grep -c '^Новые записи сверху' "$box4/knowledge/api_changelog.md")
if [[ "$kept" -eq 1 && "$mk" -eq 2 && "$orphan" -eq 1 ]]; then
    ok "шапка отрезается по содержимому, а не по счёту строк"
else
    fail "отрезание шапки по счёту строк" \
         "первая запись $kept (ждём 1), маркеров $mk (ждём 2), фраз шапки $orphan (ждём 1)"
fi

# 6. Прогон, убитый на середине, не теряет прозу.
#    Случилось на живом прогоне 2026-08-03: агент прибит по таймауту на 27-й из
#    35 страниц. 27 событий уже лежали в JSONL (дописывается построчно), в
#    журнал не попало ни одного (собирался в WORK_DIR и удалялся trap-ом), а
#    снимки успели обновиться — то есть следующий прогон увидел бы «изменений
#    нет», и эти 27 страниц не попали бы в прозу никогда.
box5=$(setup_two_site_box crash_midway)
sed -i "s|^API_URL=.*|API_URL=\"http://127.0.0.1:8736/\"|" "$box5/archivarius.sh"
{ printf '## A\n'
  for i in 1 2 3 4 5 6; do printf -- '- [Стр %s](http://127.0.0.1:8731/p%s.md)\n' "$i" "$i"; done
} > "$SRV_ROOT/llms.txt"
: > "$SRV2_ROOT/llms.txt"
for i in 1 2 3 4 5 6; do printf 'версия один\n' > "$SRV_ROOT/p$i.md"; done
( cd "$box5" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box5/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
for i in 1 2 3 4 5 6; do printf 'версия два, правка\n' > "$SRV_ROOT/p$i.md"; done

# Убиваем на середине. Заглушку замедляем, иначе шесть страниц успевают
# обработаться до kill и обрыв не воспроизводится.
printf '1.5' > "$WORK/stub_slow"
( cd "$box5" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box5/.key" \
    timeout -s TERM 5 ./archivarius.sh >/dev/null 2>&1 )
rm -f "$WORK/stub_slow"
ev_after_kill=$(wc -l < "$box5/knowledge/events.jsonl" 2>/dev/null || echo 0)
draft=$([[ -s "$box5/state/pending_block.md" ]] && echo да || echo нет)

# Следующий прогон обязан влить черновик, даже если новых изменений нет.
out=$( cd "$box5" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box5/.key" \
       timeout 60 ./archivarius.sh 2>&1 )
md_now=$(grep -c '^### \[' "$box5/knowledge/api_changelog.md" 2>/dev/null || echo 0)
ev_now=$(wc -l < "$box5/knowledge/events.jsonl" 2>/dev/null || echo 0)
left=$([[ -s "$box5/state/pending_block.md" ]] && echo да || echo нет)

if [[ "$ev_after_kill" -gt 0 && "$draft" == "да" \
      && "$md_now" -eq "$ev_now" && "$left" == "нет" ]]; then
    ok "прогон убит на середине: проза догнала JSONL ($md_now = $ev_now)"
else
    fail "потеря прозы при обрыве" \
         "событий после kill=$ev_after_kill, черновик=$draft; после: md=$md_now jsonl=$ev_now, черновик=$left"
fi

echo "=== Ответ прозой не теряет событие ==="

# Модель отвечает прозой примерно в каждом десятом случае (10 из 103, 31 июля).
# Такой ответ обязан попасть и в журнал, и в JSONL: если он есть только в прозе,
# два выхода расходятся, и потом не отличить «страница потерялась» от «её не было».
#
# Дефект, который этот тест ловит: при пустом $parsed команда
# `jq .highlights` по пустому входу завершается УСПЕШНО и печатает ничего,
# поэтому `|| echo '[]'` не срабатывает, `--argjson hl ""` роняет jq,
# и событие тихо не пишется. Найдено 2026-08-03 на копии этого кода в Телеграфе.
cat > "$WORK/api_prose.py" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        out = json.dumps({"choices": [{"message": {
            "content": "Модель ответила прозой, без всякого JSON."},
            "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
HTTPServer(('127.0.0.1', 8734), H).serve_forever()
PY
python3 "$WORK/api_prose.py" >/dev/null 2>&1 &
PROSE_PID=$!
STUB_PIDS="$STUB_PIDS $PROSE_PID"
for _ in $(seq 30); do
    curl -s -o /dev/null --max-time 1 -X POST http://127.0.0.1:8734/ && break
    sleep 0.1
done

box="$WORK/prose_box"; rm -rf "$box"; mkdir -p "$box"
sed -e "s|^INDEX_URLS=.*|INDEX_URLS=\"http://127.0.0.1:8731/llms.txt\"|" \
    -e "s|^API_URL=.*|API_URL=\"http://127.0.0.1:8734/\"|" \
    "$AGENT" > "$box/archivarius.sh"
chmod +x "$box/archivarius.sh"
printf 'sk-stub\n' > "$box/.key"
{ printf '## Guides\n'; printf -- '- [Живая](http://127.0.0.1:8731/live.md)\n'; } > "$SRV_ROOT/llms.txt"
printf 'версия один\n' > "$SRV_ROOT/live.md"
( cd "$box" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )
printf 'версия два\n' > "$SRV_ROOT/live.md"
( cd "$box" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box/.key" \
    timeout 60 ./archivarius.sh >/dev/null 2>&1 )

md_entries=$(grep -c '^### \[' "$box/knowledge/api_changelog.md" 2>/dev/null || echo 0)
jsonl_entries=$(wc -l < "$box/knowledge/events.jsonl" 2>/dev/null || echo 0)
if [[ "$jsonl_entries" -eq 1 && "$md_entries" -eq 1 ]] \
   && [[ "$(jq -r '.kind' "$box/knowledge/events.jsonl")" == "?" ]]; then
    ok "проза: событие записано, выходы сходятся (kind='?')"
else
    fail "проза теряет событие" "в md $md_entries, в jsonl $jsonl_entries"
fi

echo "=== Уведомление при breaking ==="

# Агент, который нашёл ломающее изменение и промолчал, бесполезен: журнал надо
# пойти и открыть. Здесь модель заменена заглушкой, отдающей breaking: true,
# а канал доставки — скриптом, который лишь записывает то, что ему передали.
#
# Заглушка API отдаёт ответ в форме DeepSeek: choices[0].message.content.
cat > "$WORK/fake_api.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
BODY = json.dumps({"summary": "Удалён эндпоинт /v1/orders",
                   "kind": "api", "breaking": True,
                   "highlights": ["endpoint removed"]})

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        out = json.dumps({"choices": [{"message": {"content": BODY}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass

HTTPServer(('127.0.0.1', PORT), H).serve_forever()
PY
python3 "$WORK/fake_api.py" 8732 >/dev/null 2>&1 &
API_PID=$!
STUB_PIDS="$STUB_PIDS $API_PID"
for _ in $(seq 30); do
    curl -s -o /dev/null --max-time 1 -X POST http://127.0.0.1:8732/ && break
    sleep 0.1
done

# Песочница с подменённой моделью и каналом доставки.
setup_notify_box() {  # $1 = имя, $2 = код возврата канала
    local box="$WORK/$1"
    rm -rf "$box"; mkdir -p "$box"
    sed -e "s|^INDEX_URLS=.*|INDEX_URLS=\"http://127.0.0.1:8731/llms.txt\"|" \
        -e "s|^API_URL=.*|API_URL=\"http://127.0.0.1:8732/\"|" \
        "$AGENT" > "$box/archivarius.sh"
    chmod +x "$box/archivarius.sh"
    printf 'sk-stub\n' > "$box/.key"
    { printf '#!/bin/sh\n'
      printf 'printf "%%s" "$1" > "%s/notified.sender"\n' "$box"
      printf 'cat > "%s/notified.body"\n' "$box"
      printf 'exit %s\n' "$2"
    } > "$box/notify.sh"
    chmod +x "$box/notify.sh"
    printf '%s' "$box"
}

run_notify_box() {
    local box="$1"
    ( cd "$box" && env -u DEEPSEEK_API_KEY DEEPSEEK_KEY_FILE="$box/.key" \
        NOTIFY_CMD="$box/notify.sh" timeout 60 ./archivarius.sh 2>&1 )
}

{ printf '## Guides\n'
  printf -- '- [Живая](http://127.0.0.1:8731/live.md)\n'
} > "$SRV_ROOT/llms.txt"

# 7. breaking: true — канал обязан быть вызван, и с содержательным текстом.
box=$(setup_notify_box notify_ok 0)
printf 'версия один\n' > "$SRV_ROOT/live.md"
run_notify_box "$box" >/dev/null           # первый прогон: только слепок
printf 'версия два, эндпоинт удалён\n' > "$SRV_ROOT/live.md"
out=$(run_notify_box "$box")
if [[ -f "$box/notified.body" ]] \
   && grep -q "BREAKING" "$box/notified.body" \
   && [[ "$(cat "$box/notified.sender")" == "archivarius" ]] \
   && grep -q "уведомление отправлено" <<<"$out"; then
    ok "breaking: уведомление отправлено с именем отправителя"
else
    fail "breaking: уведомление" "файл: $([[ -f "$box/notified.body" ]] && echo есть || echo нет); $(grep -o 'уведомлени.*' <<<"$out")"
fi

# 8. Канал упал — прогон обязан завершиться успешно, но пожаловаться в лог.
#    Молчаливый провал доставки неотличим от «изменений не было».
box=$(setup_notify_box notify_fail 3)
printf 'версия один\n' > "$SRV_ROOT/live.md"
run_notify_box "$box" >/dev/null
printf 'версия два, эндпоинт удалён\n' > "$SRV_ROOT/live.md"
out=$(run_notify_box "$box"); rc=$?
if [[ "$rc" -eq 0 ]] && grep -q "уведомление НЕ отправлено (код 3)" <<<"$out"; then
    ok "канал упал: прогон цел, провал доставки виден в логе"
else
    fail "канал упал" "rc=$rc; $(grep -o 'уведомлени.*' <<<"$out")"
fi

# 9. Без breaking канал не дёргается вовсе — иначе алерты обесценятся.
box=$(setup_notify_box notify_quiet 0)
cat > "$WORK/fake_api_quiet.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
BODY = json.dumps({"summary": "Опечатка", "kind": "cosmetic",
                   "breaking": False, "highlights": []})
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        out = json.dumps({"choices": [{"message": {"content": BODY}}]}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(out)))
        self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
HTTPServer(('127.0.0.1', 8733), H).serve_forever()
PY
python3 "$WORK/fake_api_quiet.py" >/dev/null 2>&1 &
QUIET_PID=$!
STUB_PIDS="$STUB_PIDS $QUIET_PID"
for _ in $(seq 30); do
    curl -s -o /dev/null --max-time 1 -X POST http://127.0.0.1:8733/ && break
    sleep 0.1
done
sed -i "s|^API_URL=.*|API_URL=\"http://127.0.0.1:8733/\"|" "$box/archivarius.sh"
printf 'версия один\n' > "$SRV_ROOT/live.md"
run_notify_box "$box" >/dev/null
printf 'версия два, опечатка исправлена\n' > "$SRV_ROOT/live.md"
run_notify_box "$box" >/dev/null
if [[ ! -f "$box/notified.body" ]]; then
    ok "не-breaking: канал не вызывается"
else
    fail "не-breaking" "уведомление ушло на косметическую правку"
fi

printf '\n%s\n' "----------------------------------------"
printf 'пройдено: %d, провалено: %d\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
