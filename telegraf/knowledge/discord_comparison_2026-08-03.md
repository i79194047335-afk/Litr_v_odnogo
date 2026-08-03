# Сверка Discord `#api-updates` с Telegram-каналом

**Дата сверки:** 2026-08-03. **Вердикт: различий ноль, источники дублируются.**

Иван вошёл в `discord.gg/lighterxyz` личным аккаунтом, прошёл гейт `verify` и
скопировал два последних сообщения канала `#api-updates`. Ниже — они дословно,
рядом с тем, что Телеграф уже разобрал из `@lighter_api_updates`.

Зачем сохранено: вывод «Discord не нужен» опирается на это сравнение, а доступа
к серверу у агентов нет и не будет. Без дословного текста утверждение через
месяц станет призрачным числом — ссылкой на сверку, которую нечем перепроверить.

---

## Discord, как принесено

Автор обоих сообщений — `Supertramp [LIT]`, роль `lighter core`, префикс `@api`.

### 21.07.2026, 23:50

> Front-end now allows editing multiple api keys at once to toggle maker-only
> status: https://apidocs.lighter.xyz/reference/apikeys. For latency-sensitive
> traders we strongly recommend their usage, more details can be found here:
> https://apidocs.lighter.xyz/docs/api-keys#maker-only-api-keys
>
> The positionFunding endpoint now accepts a market_ids query parameter, which
> can contain a list of market indexes separated by commas:
> https://apidocs.lighter.xyz/reference/positionfunding
>
> The fundings endpoint caching has been improved, fixing some edge cases where
> the last funding payment was showing up as empty:
> https://apidocs.lighter.xyz/reference/fundings
>
> These changes apply to both Lighter Core, and the Robinhood Chain instance
> (https://apidocs.rh.lighter.xyz/).

### 23.07.2026, 21:19

> We have released v1.0.7 of the Lighter Go SDK. Release notes are available
> here: https://github.com/elliottech/lighter-go/releases/tag/v1.0.7
>
> The remaining_usage field will not be returned anymore by the referral/get and
> referral/create endpoints, starting Monday, Jul 27th. Currently, both Lighter
> Core and the RHC instance allow for unlimited invites

---

## Соответствие постам в Telegram

| Discord | Telegram | Дата поста | Совпадение |
|---|---|---|---|
| 21.07, 23:50 | [пост 153](https://t.me/lighter_api_updates/153) | 2026-07-21 | дословное |
| 23.07, 21:19 | [пост 154](https://t.me/lighter_api_updates/154) | 2026-07-23 | дословное |

Сравнение машинное, не на глаз: текст из `evidence` обоих событий приведён к
одной форме (снят маркер списка `- `, схлопнуты пробелы) и сопоставлен построчно
с текстом из Discord. Шесть содержательных строк против шести, `diff` пуст.

Единственное, чего нет в Telegram-версии, — метаданные Discord: имя автора,
роль и префикс `@api`. Они подтверждают, что Telegram-канал не пересказ, а тот
же официальный поток от того же человека.

---

## Что из этого следует

**Для `#api-updates`:** источник отклонён. Даёт то же, что Телеграф забирает без
авторизации, а стоит личного токена и риска бана на сервере в 50 748 участников.

**Чего сверка НЕ показывает.** Она про один канал и два сообщения. Остальные
каналы сервера — обсуждения, багрепорты, ответы разработчиков в ветках — не
проверялись, и именно там могло бы лежать недокументированное, о котором говорил
ndr. Возвращаться туда стоит с конкретным вопросом, ответа на который нет ни в
доке, ни в Telegram, — а не «на всякий случай».
