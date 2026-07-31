#!/usr/bin/env bash
#
# Архивариус — следит за документацией Lighter API.
#
# Обходит страницы из машиночитаемого индекса, сравнивает с прошлым прогоном,
# и только для изменившихся просит DeepSeek описать, что поменялось.
# Результат копится в knowledge/api_changelog.md. Claude в рантайме не нужен.
#
# Запуск: ./archivarius.sh  (или из cron, см. README.md)

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$HERE")"

INDEX_URL="https://apidocs.lighter.xyz/llms.txt"
MODEL="deepseek-v4-flash"          # дешёвый тир: пересказывать, не рассуждать
API_URL="https://api.deepseek.com/chat/completions"

STATE_DIR="$HERE/state"
SNAP_DIR="$STATE_DIR/snapshots"
CHANGELOG="$HERE/knowledge/api_changelog.md"   # для человека
EVENTS="$HERE/knowledge/events.jsonl"          # для машин: одна строка на изменение
LOG_FILE="$HERE/logs/run.log"

MAX_CHARS=12000                    # потолок на версию страницы в промпте
TODAY="$(date -u +%Y-%m-%d)"

mkdir -p "$SNAP_DIR" "$HERE/knowledge" "$HERE/logs"

log() { printf '%s [%s] %s\n' "$(date -u '+%F %T')" "$1" "$2" | tee -a "$LOG_FILE" >&2; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

log INFO "=== Архивариус: старт ==="

# --- ключ ---------------------------------------------------------------
# Порядок поиска ключа: окружение -> ~/.deepseek_key -> .env проекта.
# Отдельный файл ключа удобен тем, что его переиспользуют все будущие агенты,
# и он не привязан ни к одному проекту.
KEY_FILE="${DEEPSEEK_KEY_FILE:-$HOME/.deepseek_key}"

if [[ -z "${DEEPSEEK_API_KEY:-}" && -r "$KEY_FILE" ]]; then
    DEEPSEEK_API_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"
fi

# Запасной вариант: .env проекта. Читаем только нужную строку, а не `source`, —
# исполнять файл с секретами как код незачем.
if [[ -z "${DEEPSEEK_API_KEY:-}" && -f "$PROJECT_ROOT/.env" ]]; then
    DEEPSEEK_API_KEY="$(grep -m1 '^DEEPSEEK_API_KEY=' "$PROJECT_ROOT/.env" | cut -d= -f2- | tr -d '"'"'"' \r')"
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    log ERROR "ключ не найден: ни в окружении, ни в $KEY_FILE, ни в $PROJECT_ROOT/.env"
    exit 1
fi

# --- индекс -------------------------------------------------------------
INDEX="$WORK_DIR/llms.txt"
if ! curl -sS --fail --retry 3 --retry-delay 5 --max-time 30 -o "$INDEX" "$INDEX_URL"; then
    log ERROR "индекс недоступен — прогон прерван, состояние не тронуто"
    exit 1
fi

# Строки вида: - [Заголовок](https://...md): описание
# Вытаскиваем "url<TAB>заголовок", секцию берём из ближайшего "## " сверху.
PAGES="$WORK_DIR/pages.tsv"
awk '
    /^## / { section = substr($0, 4); next }
    match($0, /^- \[([^]]+)\]\((https?:[^)]+)\)/, m) {
        printf "%s\t%s\t%s\n", m[2], m[1], section
    }
' "$INDEX" > "$PAGES"

TOTAL=$(wc -l < "$PAGES")
if [[ "$TOTAL" -eq 0 ]]; then
    log ERROR "индекс распарсился в 0 страниц — формат сменился, прогон прерван"
    exit 1
fi
log INFO "в индексе страниц: $TOTAL"

# Первый прогон — только базовый слепок, без единого обращения к модели:
# сравнивать не с чем, а описывать 103 страницы «изменений» бессмысленно и дорого.
FIRST_RUN=0
[[ -z "$(ls -A "$SNAP_DIR" 2>/dev/null)" ]] && FIRST_RUN=1

# --- обход страниц ------------------------------------------------------
CHANGES="$WORK_DIR/changes.tsv"   # url \t title \t section \t тип \t путь_к_старой \t путь_к_новой
: > "$CHANGES"
ok=0; failed=0; unchanged=0

while IFS=$'\t' read -r url title section; do
    [[ -z "$url" ]] && continue

    key="$(printf '%s' "$url" | sha256sum | cut -d' ' -f1)"
    snap="$SNAP_DIR/$key.md"
    fresh="$WORK_DIR/$key.new"

    if ! curl -sS --fail --retry 2 --retry-delay 3 --max-time 30 \
              -A "Archivarius/1.0 (docs watcher)" -o "$fresh" "$url"; then
        log WARN "не открылась: $url"
        failed=$(( failed + 1 ))
        continue
    fi
    ok=$(( ok + 1 ))
    sleep 0.2   # вежливая пауза между запросами

    if [[ -f "$snap" ]] && cmp -s "$snap" "$fresh"; then
        unchanged=$(( unchanged + 1 ))
        continue
    fi

    if [[ -f "$snap" ]]; then
        # старую версию сохраняем отдельно: снимок ниже будет перезаписан
        cp "$snap" "$WORK_DIR/$key.old"
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$url" "$title" "$section" "изменена" "$WORK_DIR/$key.old" "$snap" >> "$CHANGES"
    else
        # "-" вместо пустого поля: пустое схлопывается при read и сдвигает колонки
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$url" "$title" "$section" "новая страница" "-" "$snap" >> "$CHANGES"
    fi

    cp "$fresh" "$snap"
done < "$PAGES"

CHANGED=$(wc -l < "$CHANGES")
log INFO "скачано: $ok, не открылось: $failed, без изменений: $unchanged, изменилось: $CHANGED"

# --- первый прогон: слепок и выход -------------------------------------
prepend() {  # дописать блок в начало журнала
    local block="$1" tmp="$WORK_DIR/changelog.new"
    {
        printf '# Lighter API — журнал изменений документации\n\n'
        printf 'Ведёт Архивариус автоматически. Источник: <%s>\n' "$INDEX_URL"
        printf 'Новые записи сверху.\n\n'
        cat "$block"
        # старый журнал без его шапки (первые 5 строк)
        [[ -f "$CHANGELOG" ]] && tail -n +6 "$CHANGELOG"
    } > "$tmp"
    mv "$tmp" "$CHANGELOG"
}

if [[ "$FIRST_RUN" -eq 1 ]]; then
    printf '## %s — первичный снимок\n\nСохранено %s страниц как базовый слепок. Изменения фиксируются со следующего прогона.\n\n' \
        "$TODAY" "$ok" > "$WORK_DIR/block.md"
    prepend "$WORK_DIR/block.md"
    log INFO "первый прогон: слепок снят, обращений к модели не было"
    exit 0
fi

if [[ "$CHANGED" -eq 0 ]]; then
    log INFO "изменений нет — обращений к модели не было"
    exit 0
fi

# --- пересказ изменений через DeepSeek ---------------------------------
SYSTEM_PROMPT='Ты — Архивариус, технический ассистент, следящий за документацией криптобиржи Lighter. Опиши сжато и точно, что изменилось, чтобы разработчик не перечитывал страницу целиком. Не выдумывай изменений, которых нет в тексте.

Отвечай СТРОГО одним JSON-объектом, без markdown-обёртки и без текста вокруг:
{"summary": "...", "kind": "...", "breaking": true|false, "highlights": ["...", "..."]}

- summary — разбор изменения по-русски (можно markdown внутри строки). Технические
  термины, названия эндпоинтов, параметров и полей оставляй на английском.
- kind — одно из: "api" (эндпоинты/параметры/поля), "limits" (лимиты, квоты, тарифы),
  "behavior" (изменилось поведение или правила), "docs" (только текст/примеры/ссылки),
  "cosmetic" (опечатки, форматирование, без смысловых изменений).
- breaking — true, только если изменение сломает существующую интеграцию.
- highlights — до 5 коротких фактов, каждый одной строкой. Пустой массив, если менять нечего.'

BLOCK="$WORK_DIR/block.md"
printf '## %s — изменений: %s\n\n' "$TODAY" "$CHANGED" > "$BLOCK"
summarized=0

while IFS=$'\t' read -r url title section kind oldfile newfile; do
    if [[ "$oldfile" != "-" && -f "$oldfile" ]]; then
        prompt=$(printf 'Страница документации Lighter API ИЗМЕНИЛАСЬ.\nРаздел: %s\nНазвание: %s\nURL: %s\n\nОпиши КОНКРЕТНО, что изменилось по сути: новые или удалённые эндпоинты, параметры, поля, лимиты, значения. Отдельно отметь breaking changes — то, что сломает существующую интеграцию.\n\n--- СТАРАЯ ВЕРСИЯ ---\n%s\n\n--- НОВАЯ ВЕРСИЯ ---\n%s' \
            "$section" "$title" "$url" \
            "$(head -c "$MAX_CHARS" "$oldfile")" \
            "$(head -c "$MAX_CHARS" "$newfile")")
    else
        prompt=$(printf 'Появилась НОВАЯ страница документации Lighter API.\nРаздел: %s\nНазвание: %s\nURL: %s\n\nОпиши в 2-4 предложениях, о чём она и что важного в ней есть для разработчика торгового бота.\n\n--- СОДЕРЖИМОЕ ---\n%s' \
            "$section" "$title" "$url" \
            "$(head -c "$MAX_CHARS" "$newfile")")
    fi

    # jq собирает payload — экранирование кавычек/переводов строк на нём, не на нас
    payload=$(jq -n --arg m "$MODEL" --arg s "$SYSTEM_PROMPT" --arg u "$prompt" \
        '{model:$m, temperature:0.2, max_tokens:4000,
          messages:[{role:"system",content:$s},{role:"user",content:$u}]}')

    response=$(curl -sS --fail --retry 2 --retry-delay 5 --max-time 120 \
        -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$payload" "$API_URL" 2>/dev/null)

    raw=$(printf '%s' "$response" | jq -r '.choices[0].message.content // empty' 2>/dev/null)

    # v4-flash — рассуждающая модель: сначала reasoning_content, потом content.
    # Если лимит токенов вышел на рассуждениях, content придёт пустым — это надо
    # отличать от сетевой ошибки, иначе чиним не то.
    if [[ -z "$raw" ]] && printf '%s' "$response" | jq -e '.choices[0]' >/dev/null 2>&1; then
        reason=$(printf '%s' "$response" | jq -r '.choices[0].finish_reason // "?"')
        log WARN "пустой content (finish_reason=$reason) для: $url"
    fi

    # Просили голый JSON, но модель обрамляет его чем попало: ```json, префиксом
    # " в JSON." и прочим. Берём кусок от первой { до последней } — это переживает
    # любую обёртку, в отличие от снятия конкретных маркеров.
    parsed=$(printf '%s' "$raw" | tr '\n' ' ' | sed -e 's/^[^{]*//' -e 's/[^}]*$//' \
             | jq -c '{summary, kind, breaking, highlights}' 2>/dev/null)

    if [[ -n "$parsed" ]]; then
        summary=$(printf '%s' "$parsed" | jq -r '.summary // empty')
        ckind=$(printf '%s'  "$parsed" | jq -r '.kind // "?"')
        breaking=$(printf '%s' "$parsed" | jq -r 'if .breaking == true then "true" else "false" end')
    else
        # Модель ответила прозой вместо JSON — текст не теряем, поля помечаем неизвестными.
        [[ -n "$raw" ]] && log WARN "ответ не разобрался как JSON: $url"
        summary="$raw"
        ckind="?"
        breaking="false"
    fi

    # Модель иногда срывается в повтор одного слова на тысячу итераций. Такой
    # текст не наблюдение, а мусор, и в базу знаний ему нельзя: место в логе.
    if [[ -n "$summary" ]]; then
        top_share=$(printf '%s' "$summary" | tr -s '[:space:]' '\n' | grep -c . || true)
        if [[ "${top_share:-0}" -gt 40 ]]; then
            dominant=$(printf '%s' "$summary" | tr -s '[:space:]' '\n' | sort | uniq -c | sort -rn | head -1 | awk '{print $1}')
            if [[ $(( dominant * 100 / top_share )) -ge 50 ]]; then
                log ERROR "ответ выродился в повтор ($dominant из $top_share слов) — отбрасываю: $url"
                summary=""   # ниже это откатит снимок и повторит на следующем прогоне
            fi
        fi
    fi

    if [[ -z "$summary" ]]; then
        log ERROR "модель не ответила по: $url"
        # снимок откатываем — следующий прогон увидит страницу изменённой и попробует снова
        if [[ "$oldfile" != "-" && -f "$oldfile" ]]; then
            cp "$oldfile" "$newfile"
        else
            rm -f "$newfile"   # была новая страница — пусть снова считается новой
        fi
        summary='_(модель не ответила — будет повторено при следующем прогоне)_'
    else
        summarized=$(( summarized + 1 ))

        # Машиночитаемое событие: одна строка на изменение, чтобы следующие
        # агенты читали это пайпом, а не разбирали прозу. Пишется и когда ответ
        # не разобрался (kind="?") — иначе проза и JSONL расходятся, и потом
        # непонятно, потерялась страница или её не было.
        jq -c -n \
            --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            --arg url "$url" --arg title "$title" --arg section "$section" \
            --arg change "$kind" --arg k "$ckind" --arg s "$summary" \
            --argjson br "$breaking" \
            --argjson hl "$(printf '%s' "$parsed" | jq -c '.highlights // []' 2>/dev/null || echo '[]')" \
            '{ts:$ts, source:"lighter-apidocs", url:$url, title:$title, section:$section,
               change:$change, kind:$k, breaking:$br, highlights:$hl, summary:$s}' \
            >> "$EVENTS"
    fi

    mark=""
    [[ "$breaking" == "true" ]] && mark=" ⚠️ **BREAKING**"
    printf '### [%s](%s) — %s%s\n_Раздел: %s · тип: %s_\n\n%s\n\n' \
        "$title" "$url" "$kind" "$mark" "$section" "$ckind" "$summary" >> "$BLOCK"
done < "$CHANGES"

prepend "$BLOCK"
log INFO "описано моделью $summarized из $CHANGED изменённых страниц ($MODEL)"

# Сводка по breaking — чтобы главное было видно в cron.log, не открывая журнал.
if [[ -s "$EVENTS" ]]; then
    today_breaking=$(jq -r --arg d "$TODAY" 'select(.breaking == true and (.ts | startswith($d))) | .title' "$EVENTS" 2>/dev/null | paste -sd', ')
    [[ -n "$today_breaking" ]] && log WARN "BREAKING CHANGES: $today_breaking"
fi

log INFO "=== готово ==="
