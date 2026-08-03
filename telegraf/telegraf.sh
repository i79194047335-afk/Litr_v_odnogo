#!/usr/bin/env bash
#
# Телеграф — следит за официальным каналом API-апдейтов Lighter в Telegram.
#
# Читает публичное превью t.me/s/<канал> (авторизация не нужна), берёт только
# посты новее последнего виденного номера и просит DeepSeek разобрать каждый.
# Результат копится в knowledge/updates.md и knowledge/events.jsonl.
#
# Зачем отдельно от Архивариуса: тот следит за apidocs.lighter.xyz и видит
# только то, что дошло до документации. Эндпоинт accountOrders был анонсирован
# в этом канале 23 июля — Архивариус его не заметил.
#
# Состояние — один файл с номером последнего разобранного поста. Номера в
# Telegram монотонные, поэтому диффа не нужно: пост либо новее, либо нет.
#
# Запуск: ./telegraf.sh  (или из cron, см. README.md)

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CHANNEL="${TELEGRAF_CHANNEL:-lighter_api_updates}"
BASE_URL="${TELEGRAF_BASE_URL:-https://t.me/s}"
MODEL="deepseek-v4-flash"          # дешёвый тир: пересказывать, не рассуждать
API_URL="${TELEGRAF_API_URL:-https://api.deepseek.com/chat/completions}"

STATE_DIR="$HERE/state"
WATERMARK="$STATE_DIR/last_post_id"            # состояние целиком: один номер
UPDATES="$HERE/knowledge/updates.md"           # для человека
EVENTS="$HERE/knowledge/events.jsonl"          # для машин: одна строка на пост
LOG_FILE="$HERE/logs/run.log"
PARSER="$HERE/parse_posts.py"
NOTIFY="${NOTIFY_CMD:-$HERE/../scripts/notify.sh}"

MAX_CHARS=6000                     # потолок на текст поста в промпте
MAX_NEW=25                         # предохранитель: больше за прогон не берём
TODAY="$(date -u +%Y-%m-%d)"

mkdir -p "$STATE_DIR" "$HERE/knowledge" "$HERE/logs"

log() { printf '%s [%s] %s\n' "$(date -u '+%F %T')" "$1" "$2" | tee -a "$LOG_FILE" >&2; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

log INFO "=== Телеграф: старт ==="

# --- ключ ---------------------------------------------------------------
# Тот же порядок, что у Архивариуса: env, потом отдельный файл, потом .env.
KEY_FILE="${DEEPSEEK_KEY_FILE:-$HOME/.deepseek_key}"
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    if [[ -r "$KEY_FILE" ]]; then
        DEEPSEEK_API_KEY="$(tr -d ' \t\n\r' < "$KEY_FILE")"
    elif [[ -r "$HERE/../.env" ]]; then
        DEEPSEEK_API_KEY="$(grep -m1 '^DEEPSEEK_API_KEY=' "$HERE/../.env" | cut -d= -f2- | tr -d '"'"'"' \t\n\r')"
    fi
fi
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    log ERROR "нет ключа DeepSeek (ни env, ни $KEY_FILE, ни .env)"
    exit 1
fi

# --- страница канала ------------------------------------------------------
PAGE="$WORK_DIR/page.html"
if ! curl -sS --fail --retry 2 --retry-delay 3 --max-time 30 \
        -H 'Accept-Language: en' \
        "$BASE_URL/$CHANNEL" -o "$PAGE" 2>"$WORK_DIR/curl.err"; then
    log ERROR "канал недоступен: $(head -1 "$WORK_DIR/curl.err")"
    exit 1
fi

# Разбор вынесен в отдельную тулзу: это единственное место, где можно ошибиться
# незаметно, и его надо было покрыть тестами отдельно от агента.
POSTS="$WORK_DIR/posts.jsonl"
if ! python3 "$PARSER" < "$PAGE" > "$POSTS" 2>"$WORK_DIR/parse.err"; then
    # Отличаем «наш разбор сломался» от «канал молчит»: иначе агент показывает
    # пальцем на Telegram вместо себя (прецедент с mawk в Архивариусе).
    log ERROR "разбор страницы не дал постов: $(head -1 "$WORK_DIR/parse.err")"
    log ERROR "это либо смена формата t.me, либо поломка парсера — состояние не трогаю"
    exit 1
fi

TOTAL=$(wc -l < "$POSTS")
log INFO "на странице постов: $TOTAL"

# --- что из этого новое ---------------------------------------------------
LAST_SEEN=0
if [[ -r "$WATERMARK" ]]; then
    LAST_SEEN=$(tr -cd '0-9' < "$WATERMARK")
    LAST_SEEN=${LAST_SEEN:-0}
fi

NEWEST=$(jq -s 'map(.id) | max // 0' "$POSTS")

# Первый прогон: запоминаем позицию и не зовём модель. Разбирать всю историю
# канала смысла нет — она уже случилась, а токены стоят денег.
if [[ "$LAST_SEEN" -eq 0 ]]; then
    printf '%s\n' "$NEWEST" > "$WATERMARK"
    log INFO "первый прогон: отметка поставлена на пост $NEWEST, модель не вызывалась"
    log INFO "=== готово ==="
    exit 0
fi

NEW_POSTS="$WORK_DIR/new.jsonl"
jq -c --argjson since "$LAST_SEEN" 'select(.id > $since and (.text | length) > 0)' \
    "$POSTS" > "$NEW_POSTS"
NEW_COUNT=$(wc -l < "$NEW_POSTS")

if [[ "$NEW_COUNT" -eq 0 ]]; then
    log INFO "новых постов нет (последний виденный: $LAST_SEEN) — обращений к модели не было"
    # Отметку всё равно двигаем: посты без текста (фото, стикеры) иначе будут
    # пересматриваться вечно.
    [[ "$NEWEST" -gt "$LAST_SEEN" ]] && printf '%s\n' "$NEWEST" > "$WATERMARK"
    log INFO "=== готово ==="
    exit 0
fi

if [[ "$NEW_COUNT" -gt "$MAX_NEW" ]]; then
    # Столько новых постов за раз означает либо взрыв активности в канале, либо
    # потерянную отметку. И то и другое стоит увидеть глазами до траты токенов.
    log ERROR "новых постов $NEW_COUNT при пороге $MAX_NEW — останавливаюсь, проверьте $WATERMARK"
    exit 1
fi

log INFO "новых постов: $NEW_COUNT (с $LAST_SEEN по $NEWEST)"

# --- разбор моделью -------------------------------------------------------
SYSTEM_PROMPT='Ты — технический аналитик, читающий официальный канал API-апдейтов криптобиржи Lighter.
Отвечай ТОЛЬКО одним JSON-объектом, без markdown-обрамления и пояснений:
{"summary": "...", "kind": "...", "breaking": true|false, "highlights": ["...", "..."]}
- summary — 1-3 предложения по-русски: что именно изменилось.
- kind — одно из: api (эндпоинты, параметры, поля), limits (лимиты, квоты, тарифы),
  behavior (правила работы, матчинг, ликвидации), release (версии SDK, релизы),
  market (листинги, делистинги, изменения рынков), other.
- breaking — true, только если изменение сломает существующую интеграцию:
  удалённое или переименованное поле, изменённая семантика, отключённый эндпоинт.
  Анонс нового рынка или новой возможности — не breaking.
- highlights — 1-4 коротких пункта с конкретикой: имена полей, эндпоинтов, даты.'

BLOCK="$WORK_DIR/block.md"
: > "$BLOCK"
summarized=0
written=0
max_done="$LAST_SEEN"

while IFS= read -r post; do
    pid=$(printf '%s' "$post" | jq -r '.id')
    purl=$(printf '%s' "$post" | jq -r '.url')
    pts=$(printf '%s' "$post" | jq -r '.ts')
    ptext=$(printf '%s' "$post" | jq -r '.text')

    prompt=$(printf 'Новый пост в официальном канале API-апдейтов Lighter.\nURL: %s\nДата: %s\n\nРазбери, что изменилось для разработчика торгового бота.\n\n--- ТЕКСТ ПОСТА ---\n%s' \
        "$purl" "$pts" "$(printf '%s' "$ptext" | head -c "$MAX_CHARS")")

    payload=$(jq -n --arg m "$MODEL" --arg s "$SYSTEM_PROMPT" --arg u "$prompt" \
        '{model:$m, temperature:0.2, max_tokens:4000,
          messages:[{role:"system",content:$s},{role:"user",content:$u}]}')

    response=$(curl -sS --fail --retry 2 --retry-delay 5 --max-time 120 \
        -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$payload" "$API_URL" 2>/dev/null)

    raw=$(printf '%s' "$response" | jq -r '.choices[0].message.content // empty' 2>/dev/null)

    if [[ -z "$raw" ]] && printf '%s' "$response" | jq -e '.choices[0]' >/dev/null 2>&1; then
        reason=$(printf '%s' "$response" | jq -r '.choices[0].finish_reason // "?"')
        log WARN "пустой content (finish_reason=$reason) для поста $pid"
    fi

    # Модель обрамляет JSON чем попало. Берём от первой { до последней } —
    # это переживает любую обёртку, в отличие от снятия конкретных маркеров.
    parsed=$(printf '%s' "$raw" | tr '\n' ' ' | sed -e 's/^[^{]*//' -e 's/[^}]*$//' \
             | jq -c '{summary, kind, breaking, highlights}' 2>/dev/null)

    if [[ -n "$parsed" ]]; then
        summary=$(printf '%s' "$parsed" | jq -r '.summary // empty')
        ckind=$(printf '%s'  "$parsed" | jq -r '.kind // "?"')
        breaking=$(printf '%s' "$parsed" | jq -r 'if .breaking == true then "true" else "false" end')
    else
        [[ -n "$raw" ]] && log WARN "ответ не разобрался как JSON: пост $pid"
        summary="$raw"
        ckind="?"
        breaking="false"
    fi

    # Модель срывается в повтор одного слова. Такой текст не наблюдение, а мусор.
    if [[ -n "$summary" ]]; then
        words=$(printf '%s' "$summary" | tr -s '[:space:]' '\n' | grep -c . || true)
        if [[ "${words:-0}" -gt 40 ]]; then
            dominant=$(printf '%s' "$summary" | tr -s '[:space:]' '\n' | sort | uniq -c | sort -rn | head -1 | awk '{print $1}')
            if [[ $(( dominant * 100 / words )) -ge 50 ]]; then
                log ERROR "ответ выродился в повтор ($dominant из $words слов) — отбрасываю пост $pid"
                summary=""
            fi
        fi
    fi

    if [[ -z "$summary" ]]; then
        # Молчание честнее мусора: отметку не двигаем дальше этого поста,
        # следующий прогон возьмёт его снова.
        log ERROR "модель не ответила по посту $pid — будет повторено"
        break
    fi

    summarized=$(( summarized + 1 ))
    max_done="$pid"

    # highlights добываем отдельно и с подстраховкой: при ответе прозой $parsed
    # пуст, `jq .highlights` по пустому входу завершается успешно и печатает
    # НИЧЕГО — а `--argjson hl ""` роняет jq целиком, и событие не пишется.
    # Проза при этом уже легла в updates.md, то есть выходы расходятся молча.
    # Ровно так Архивариус потерял 10 событий из 103 (31 июля).
    highlights=$(printf '%s' "$parsed" | jq -c '.highlights // []' 2>/dev/null)
    [[ -z "$highlights" ]] && highlights='[]'

    # Доказательство рядом с выводом: пересказ модели нечем перепроверить, если
    # исходного текста нет под рукой. Пост в канале может быть отредактирован
    # или удалён, поэтому ссылки недостаточно.
    jq -c -n \
        --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg posted "$pts" --arg url "$purl" --argjson pid "$pid" \
        --arg k "$ckind" --arg s "$summary" \
        --argjson br "$breaking" \
        --argjson hl "$highlights" \
        --arg ev "$ptext" \
        '{v:1, ts:$ts, source:"lighter-telegram", channel:"'"$CHANNEL"'",
           post_id:$pid, posted:$posted, url:$url,
           kind:$k, breaking:$br, highlights:$hl, summary:$s, evidence:$ev}' \
        >> "$EVENTS"
    written=$(( written + 1 ))

    mark=""
    [[ "$breaking" == "true" ]] && mark=" ⚠️ **BREAKING**"
    printf '### [Пост %s](%s) — %s%s\n_Тип: %s_\n\n%s\n\n' \
        "$pid" "$purl" "${pts:0:10}" "$mark" "$ckind" "$summary" >> "$BLOCK"
done < "$NEW_POSTS"

# --- запись результата ----------------------------------------------------
# Два выхода обязаны сходиться по счёту: если проза и JSONL разошлись, что-то
# потеряно молча (прецедент — 103 записи против 93 событий у Архивариуса).
if [[ "$summarized" -ne "$written" ]]; then
    log ERROR "расхождение: разобрано $summarized, записано $written — проверьте $EVENTS"
fi

if [[ "$summarized" -gt 0 ]]; then
    HEADER="## $TODAY — новых постов: $summarized"
    if [[ -f "$UPDATES" ]]; then
        { printf '%s\n\n' "$HEADER"; cat "$BLOCK"; cat "$UPDATES"; } > "$WORK_DIR/merged.md"
    else
        { printf '# Lighter API — апдейты из Telegram\n\n'
          printf 'Ведёт Телеграф автоматически. Источник: <https://t.me/%s>\n' "$CHANNEL"
          printf 'Новые записи сверху.\n\n'
          printf '%s\n\n' "$HEADER"; cat "$BLOCK"; } > "$WORK_DIR/merged.md"
    fi
    mv "$WORK_DIR/merged.md" "$UPDATES"
fi

# Отметку двигаем только до последнего успешно разобранного поста.
printf '%s\n' "$max_done" > "$WATERMARK"
log INFO "разобрано моделью $summarized из $NEW_COUNT новых постов ($MODEL), отметка: $max_done"

# --- уведомление ----------------------------------------------------------
if [[ -s "$EVENTS" ]]; then
    today_breaking=$(jq -r --arg d "$TODAY" \
        'select(.breaking == true and (.ts | startswith($d))) | "пост \(.post_id): \(.summary)"' \
        "$EVENTS" 2>/dev/null | head -5 | paste -sd$'\n')
    if [[ -n "$today_breaking" ]]; then
        log WARN "BREAKING в канале: $today_breaking"
        if [[ -x "$NOTIFY" ]]; then
            notify_rc=0
            printf 'BREAKING в канале API-апдейтов Lighter:\n\n%s\n\nЖурнал: %s' \
                "$today_breaking" "$UPDATES" | "$NOTIFY" telegraf 2>/dev/null || notify_rc=$?
            if [[ $notify_rc -eq 0 ]]; then
                log INFO "уведомление отправлено"
            else
                log WARN "уведомление НЕ отправлено (код $notify_rc) — сообщение только в журнале"
            fi
        else
            log WARN "канал доставки недоступен ($NOTIFY) — сообщение только в журнале"
        fi
    fi
fi

log INFO "=== готово ==="
