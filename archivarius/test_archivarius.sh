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
trap 'rm -rf "$WORK"' EXIT

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
trap 'rm -rf "$WORK"; kill $SRV_PID 2>/dev/null' EXIT
sleep 1

# Готовит изолированную копию агента, смотрящую на локальный индекс.
setup_sandbox() {
    local box="$WORK/$1"; shift
    rm -rf "$box"; mkdir -p "$box"
    sed -e "s|^INDEX_URL=.*|INDEX_URL=\"http://127.0.0.1:8731/llms.txt\"|" \
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

printf '\n%s\n' "----------------------------------------"
printf 'пройдено: %d, провалено: %d\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
