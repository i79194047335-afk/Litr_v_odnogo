# Lighter API — журнал изменений документации

Ведёт Архивариус автоматически. Источник: <https://apidocs.lighter.xyz/llms.txt>
Новые записи сверху.

## 2026-07-30 — изменений: 3

### [Rate Limits](https://apidocs.lighter.xyz/docs/rate-limits.md) — изменена
_Раздел: Guides_

**Что изменилось:**

- **REST API Endpoint Limits** — для **Standard accounts** увеличен лимит с **30** до **60** запросов в минуту (rolling minute). Остальные аккаунты (Builder, Plus, Premium) без изменений.

**Breaking changes:** нет. Увеличение лимита не ломает существующую интеграцию.

Все остальные разделы (WebSocket, SendTx/SendTxBatch, Explorer, Orders, Transaction Type Limits, поведение при превышении, Cooldown) — без изменений.

### [WebSocket](https://apidocs.lighter.xyz/docs/websocket-reference.md) — изменена
_Раздел: Guides_

Изменений эндпоинтов, параметров, полей, лимитов или значений нет — API-контракт остался прежним. Breaking changes отсутствуют.

Косметические правки:
- Обновлены пути к файлам-примерам:
  - `Send Tx`: ссылка изменена с `ws_send_tx.py` на `orders/create_modify_cancel_order_ws.py`.
  - `Send Batch Tx`: ссылка изменена с `ws_send_batch_tx.py` на `batch-orders/send_batch_tx_ws.py`.
- В конце описания канала `Order Book` добавлен фрагмент «Re» (после «on each update the offset wil»). Скорее всего, это результат обрыва текста при копировании, без функционального значения.

### [Partner Attribution](https://apidocs.lighter.xyz/docs/partner-integration.md) — изменена
_Раздел: Guides_

Изменение косметическое: обновлён путь к примеру в Python SDK (с `integrator_approve.py` на `integrator/approve.py`). Breaking changes нет.

## 2026-07-30 — первичный снимок

Сохранено 103 страниц как базовый слепок. Изменения фиксируются со следующего прогона.

