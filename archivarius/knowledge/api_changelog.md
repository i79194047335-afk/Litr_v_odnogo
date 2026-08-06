<!-- unparsed entries replaced 2026-07-31: 10 entries whose model reply was unusable
     (3 of them degenerate repetition) reduced to a marker -->
<!-- synthetic entries purged 2026-07-31: two hand-edited rate-limit
     snapshots used to test the model were recorded as real observations -->
# Lighter API — журнал изменений документации

Ведёт Архивариус автоматически. Источники: <https://apidocs.lighter.xyz/llms.txt> <https://docs.lighter.xyz/llms.txt> 
Новые записи сверху.

## 2026-08-06 — изменений: 1

### [Futures Contract Price Rolling Mechanism](https://docs.lighter.xyz/trading/real-world-assets-rwas/futures-contract-price-rolling-mechanism.md) — изменена
_Раздел: Lighter Docs · тип: docs_

Документация обновлена: изменён пример расписания роллирования фьючерсных контрактов с июльского окна (2026-07-08 — 2026-07-14) на августовское (2026-08-07 — 2026-08-13, 5:30 PM ET). В таблицу Roll Schedules для WTI, NATGAS, WHEAT, COPPER добавлен отсутствовавший ранее сентябрьский период роллирования 2026-09-08 5:30 PM — 2026-09-14 5:30 PM ET; значения контрактов для сентября: WTI V6 to X6, NATGAS V26 to X26, WHEAT Z26, COPPER Z6. Расписание BRENTOIL и общие правила (20% в день, время 5:30/7:00 PM ET) не изменились. API, параметры, поля и лимиты не затрагивались.

<!-- Счёт записей: в прозе 132, в events.jsonl 130. Расхождение постоянное и
     объяснимое: две записи от 30 июля сделаны до появления JSONL-потока
     (поле добавлено 31 июля). За все периоды, где есть оба выхода, счёт
     сходится: 103 (31.07) + 27 (03.08). -->

<!-- reconstructed 2026-08-03: этот блок собран из events.jsonl, а не написан
     агентом по ходу прогона. Прогон был убит по таймауту на 27-й из 35
     страниц: события успели лечь в JSONL построчно, а проза собиралась в
     WORK_DIR и исчезла вместе с ним, при уже обновившихся снимках. Дефект
     закрыт (черновик переехал в state/pending_block.md, есть тест на обрыв),
     но эти 27 записей восстановлены постфактум из машинного потока.
     Остальные 8 из 35 страниц не описаны вовсе — снимки ушли вперёд. -->
## 2026-08-03 — новый источник docs.lighter.xyz: 27 страниц

_Подключён второй сайт документации (концептуальный). Записи ниже
восстановлены из `events.jsonl` — см. маркер выше._

### [Introduction](https://docs.lighter.xyz/about-lighter/introduction.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница Introduction в разделе Lighter Docs. Она описывает Lighter как децентрализованную торговую платформу с нулевой комиссией (zero-fee), верифицируемым сопоставлением ордеров и ликвидациями, а также производительностью на уровне традиционных бирж. Для разработчика бота важно, что документация доступна в Markdown (добавление `.md` к URL) и есть общий индекс `llms.txt` для навигации. Также приведены актуальные адреса платформы: `https://app.lighter.xyz/` и `https://lighter.exchange/trade/ETH`.

### [Technical Architecture: Lighter Core](https://docs.lighter.xyz/about-lighter/technical-architecture-lighter-core.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации **Technical Architecture: Lighter Core**, описывающая архитектуру Lighter: исполнение с использованием succinct proof (ZK), Ethereum как слой расчётов и хранения состояния, а также механизм escape hatch для гарантированного вывода средств. Разработчику торгового бота важно понимать, что все операции исполняются детерминированно через Sequencer, пользовательские средства находятся на Ethereum в некастодиальных смарт-контрактах, а для критичных операций (вывод, выход из пула, reduce-only IOC) можно отправлять приоритетные запросы напрямую в сеть Ethereum. При необработке запросов Sequencer'ом активируется Escape Hatch, позволяющий пользователям восстановить состояние и вывести активы без участия off-chain компонентов.

### [LIT Utility](https://docs.lighter.xyz/about-lighter/lit-utility.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница **LIT Utility** (https://docs.lighter.xyz/about-lighter/lit-utility.md) о стейкинге LIT и байбэках. Для разработчика важно: LLP теперь доступен только стейкерам LIT, пропорция 1 LIT = 10 USDC депозита в LLP, при unstaking действует lockup 3 дня. Стейкинг даёт фиксированные 6% APR, награды покупаются из адреса `0x5E52363E65C99fefC0E356F0DC6c37b75bf8FC91`; байбэки проводятся ежедневными TWAP за счёт торговых комиссий.

### [Trading Fees](https://docs.lighter.xyz/trading/trading-fees.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница Trading Fees, которая описывает комиссии и типы аккаунтов Lighter: Standard (0 maker/taker, taker latency 300ms), Premium (комиссии зависят от стейка LIT, со скидками на fee и latency) и Plus (0.5 bps, повышенные rate limits). Для разработчика торгового бота важно: Standard Account позволяет торговать бесплатно, но с базовой латентностью; Premium требует стейка LIT для снижения комиссий; Plus включается через `changeAccountTier` и даёт 8000 `sendTx`/min и 120000 read-only запросов/min. Скидки за стейк агрегируются на уровне L1-адреса вместе с субаккаунтами.

### [LIT Fee Credits](https://docs.lighter.xyz/trading/trading-fees/lit-fee-credits.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации **LIT Fee Credits**, описывающая программу, которая позволяет участникам Premium Accounts докупать кредиты LIT вместо полного стейкинга для достижения нужного уровня торговой комиссии и задержки. Для разработчика бота важно, что торговый тир может быть активирован без полного стейкинга, но требует L1-подписи и разового платежа LIT; все платежи распределяются среди стейкеров как ежедневные награды. Это новая страница, поэтому существующие интеграции не ломаются.

### [Unified Trading Accounts](https://docs.lighter.xyz/trading/unified-trading-accounts.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации «Unified Trading Accounts», описывающая два типа счетов: Unified Trading Account (UTA) с единым маржинальным обеспечением на спотовых и перпетуал-балансах в USDC, и Simple Trading Account, где спот и перпетуалы разделены, а кросс-маржа действует только внутри перпетуалов. Для разработчика важно понимать различия при выборе режима: UTA — первый шаг к использованию спотовых активов как залога в перпетуал-маркетах, а Simple Trading Account ограничивает обеспечение settlement assets.

### [Order Types & Matching](https://docs.lighter.xyz/trading/order-types-and-matching.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации «Order Types & Matching», описывающая все типы ордеров Lighter: Market, Limit, Stop-loss/Take-profit, TWAP, Advanced TWAP, Chase Limit, Atomic, а также механику Order Margin, Price Checks и Order Matching. Для разработчика бота важны детали execution options (Post-Only, Reduce-Only), time-in-force (Good 'Til Time, Immediate or Cancel), формулы расчёта TWAP, условия триггеров SL/TP, проверки fat finger и правило price-time priority. Также объясняется, что risk checks после каждого трейда могут авто-отменять ордера, и как рассчитывается доступный order margin.

### [Real World Assets (RWAs)](https://docs.lighter.xyz/trading/real-world-assets-rwas.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации о Real World Assets (RWAs). RWAs торгуются 24/7 и включают commodities, equities и fixed income. Ранее основным провайдером ликвидности был XLP, теперь ликвидностью и ликвидациями управляет LLP. Рынки RWA поддерживают Isolated и Cross Margin, плечо не меняется вне торговых часов, но возможна повышенная волатильность на открытии.

### [RWA Pricing Mechanism](https://docs.lighter.xyz/trading/real-world-assets-rwas/rwa-pricing-mechanism.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации **RWA Pricing Mechanism**, объясняющая, как формируются цены для RWA-рынков. Цена основывается на внешних oracle-источниках (Chainlink, Pyth, Stork) и внутреннем ценообразовании; при устаревании оракулов вес плавно переходит на внутреннюю цену по экспоненциальному закону. Внутренняя цена считается как time-weighted EMA от impact price стакана заказов, с разными постоянными времени для index price (τ=30 мин) и mark price (τ=2 мин), и ограничена коридором относительно последней оракулской цены и плеча рынка. С 2026-07-10 на ряде рынков (SPY, US500, QQQ, US100, XAU, XAG, NVDA, TSLA и др.) ценовые капы отменены, но внутренние цены продолжают валидироваться против других торговых площадок.

### [Futures Contract Price Rolling Mechanism](https://docs.lighter.xyz/trading/real-world-assets-rwas/futures-contract-price-rolling-mechanism.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации, описывающая механизм постепенного перехода цен фьючерсных контрактов для рынков WTI, NATGAS, BRENTOIL, XCU и WHEAT. В течение 5 торговых дней между 5-м и 10-м бизнес-днём каждого месяца цена каждый день сдвигается на 20% от текущего месяца к следующему. Для разработчика бота важно учитывать время ролла (5:30 PM ET для WTI/NATGAS, 7:00 PM ET для BRENTOIL) и окна закрытия базового рынка, а также конкретные расписания роллов на 2026 год.

### [Market Specifications](https://docs.lighter.xyz/trading/real-world-assets-rwas/market-specifications.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Это новая справочная страница документации Lighter API, которая описывает текущие спецификации RWA-рынков (commodities, FX, equities). Для каждого рынка приведены тип, Current Open Interest Cap (M), отслеживаемый базовый актив (Tracked Underlying), используемый Oracle и ссылка на страницу прайс-фида. Разработчику торгового бота важно учитывать эти лимиты open interest и источники цен при выборе рынков и оценке рисков. Параметры могут обновляться Lighter team, поэтому нужно следить за официальными анонсами.

### [Pre-IPO Markets](https://docs.lighter.xyz/trading/real-world-assets-rwas/pre-ipo-markets.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Новая страница описывает Pre-IPO Markets: они устроены как RWA-рынки, но без price caps, только isolated margin, с ликвидационными комиссиями (LLP выступает маркет-мейкером) и стандартным funding. Сеттлмент произойдёт после IPO или цена будет скорректирована после прояснения числа разводнённых акций; любые изменения анонсируются минимум за день. Разработчику бота важно учитывать отсутствие ценовых ограничений и особый режим маржи.

### [US Equity Indices](https://docs.lighter.xyz/trading/real-world-assets-rwas/us-equity-indices.md) — новая страница
_Раздел: Lighter Docs · тип: api_

Добавлена новая страница документации **US Equity Indices**, описывающая механизм ценообразования для американских фондовых индексов (например, US100): цена формируется на основе фьючерсной цены с корректировкой к спотовой, ролл происходит в пятницу. Для разработчика торгового бота важно, что появился новый эндпоинт `/syntheticSpotInfo`, который отдаёт текущую конфигурацию рынка: базовый фьючерс, провайдера, expiry, bps/day и spot close. Также приведена формула расчёта индекса из цены Pyth, что позволяет самостоятельно воспроизводить расчёт.

### [perpRFQ](https://docs.lighter.xyz/trading/perprfq.md) — новая страница
_Раздел: Lighter Docs · тип: api_

Добавлена страница документации по perpRFQ — новому типу ордеров для торговли крупными объёмами при ограниченной видимой ликвидности. Трейдер отправляет запрос котировки (RFQ), маркет-мейкеры видят только размер и отвечают ценой в течение 10 секунд; у трейдера есть до 2 минут на исполнение. Адрес должен быть в whitelist. Функция в бета-версии, доступна на многих крипто и RWA рынках.

### [Public Pools](https://docs.lighter.xyz/trading/public-pools.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации **Public Pools** — раздел про публичные пулы, где оператор (пока только whitelisted) управляет объединённым капиталом участников. Торговля ведётся через Sub Account оператора, прибыль распределяется между участниками за вычетом комиссии оператора. Описаны ключевые параметры для интеграции: operator fee, minimum operator share, а также ограничение — isolated positions не поддерживаются. Для депозиторов указано, что они получают pool shares, lockup-периода нет, вывод средств возможен в любой момент.

### [Contract Specifications](https://docs.lighter.xyz/trading/contract-specifications.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации **Contract Specifications**, содержащая таблицу спецификаций перпетуальных фьючерсных контрактов на Lighter Test Network. Для каждого рынка (BTC, ETH, SOL, XRP и др.) указаны Price Step, Amount Step, Leverage, IMR, MMR, CMR. Разработчикам торговых ботов эти данные критичны для расчёта объёмов ордеров, маржи и уровней ликвидации. Дополнительно отмечено, что текущий funding period для всех рынков — 1 час, но для новых развёртываний он может отличаться.

### [Prelaunch Markets](https://docs.lighter.xyz/trading/prelaunch-markets.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена страница документации о prelaunch markets. Для разработчика важно: все prelaunch-рынки работают только в isolated mode, ликвидность обеспечивает новый пул XLP вместо LLP, отсутствует liquidation fee, а закрытие позиций происходит через IoC-ордер или ADL в зависимости от уровня маржи.

### [Liquidations & LLP (Insurance Fund)](https://docs.lighter.xyz/trading/liquidations-and-llp-insurance-fund.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации Lighter API — «Liquidations & LLP (Insurance Fund)». Она описывает три уровня маржинальных требований (initial, maintenance, close-out) и пошаговый процесс ликвидации: healthy → pre-liquidation → partial liquidation → full liquidation → ADL. Разработчику торгового бота важно учитывать, что в pre-liquidation нельзя увеличивать позиции, при partial liquidation биржа отменяет все открытые ордера и отправляет IoC-ордера по zero price на полный объём позиции, а при полной ликвидации позиции забирает LLP/страховой фонд. Также описаны формулы zero price, комиссия ликвидации до 1% в пользу LLP и условия авто-делевереджинга.

### [LLP Strategies](https://docs.lighter.xyz/trading/liquidations-and-llp-insurance-fund/llp-strategies.md) — новая страница
_Раздел: Lighter Docs · тип: behavior_

Добавлена новая страница документации **LLP Strategies**. В ней объясняется, что пул LLP выступает единым контрагентом и полностью обеспечивает Auto-Deleverage (ADL), а для изоляции рисков залог делится по стратегическим корзинам (buckets), каждая из которых привязана к своим рынкам. Для разработчика важно: стратегии не являются отдельными аккаунтами, но работают как сегрегированные с точки зрения риска, поэтому при полном исчерпании залога ADL затрагивает только конкретную стратегию. Отдельно уточнено, что RWA-перпетуалы теперь облагаются стандартными ликвидационными комиссиями, как и остальные рынки.

### [Multi-Asset Margin](https://docs.lighter.xyz/trading/multi-asset-margin.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации **Multi-Asset Margin**, описывающая возможность использовать не-USDC активы (например, ETH) в качестве маржи для торговли. Для разработчика важно: маржинальные активы учитываются в Total Account Value с дисконтом LTV, ликвидация использует единый health check для перпетуалов и спот-активов, а параметры каждого актива (LTV, LT, LF, ликвидационная комиссия, лимиты) доступны через endpoint `assetDetails` с полем `margin_mode: "enabled"`. На запуске поддерживаются только perpetual futures; USDC spot с не-USDC залогом появится позже. При отсутствии не-USDC активов поведение аккаунта идентично стандартному cross-margin.

### [Collateral Supply Limits](https://docs.lighter.xyz/trading/multi-asset-margin/collateral-supply-limits.md) — новая страница
_Раздел: Lighter Docs · тип: limits_

Добавлена новая страница документации «Collateral Supply Limits», описывающая лимиты обеспечения в multi-asset margin. Пока только ETH поддерживается как первый не-USDC залог, с глобальным лимитом 2000 ETH. Пользователь может выбирать, какую часть доступного коллатерала направить в маржу по каждому активу. Указано, что в будущем активы и функции будут расширяться.

### [Funding](https://docs.lighter.xyz/trading/funding.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации «Funding» (https://docs.lighter.xyz/trading/funding.md). Она описывает механизм периодических funding-платежей по бессрочным фьючерсам: выплаты происходят каждый час, peer-to-peer, без комиссии биржи. Приводятся формулы расчёта ставки от mark-to-index premium, interest rate и клампов (SmallClamp 0.05%, BigClamp 4%), а также формула платежа для позиции. Для разработчика бота важно, что при premium в пределах ±0.05% ставка равна InterestRate/8 (1 bps за 8 часов), а максимум — 4% за 8 часов.

### [Funding Rate Rebates](https://docs.lighter.xyz/trading/funding/funding-rate-rebates.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации, описывающая программу Funding Rate Rebates для RWA рынков. Программа позволяет получать до 15% rebate на funding payments, комбинируя 6% за Premium Account и до 9% за стейкинг LIT. Для расчета используется min(Funding Rate, Interest Rate Component) × Position Value. Важно: программа будет прекращена 15 мая 2025 в 15:00 UTC, выплаты производятся ежедневно в 00:00 UTC при сумме > $1.

### [PnL And Total Account Value](https://docs.lighter.xyz/trading/pnl-and-total-account-value.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации по расчёту PnL и общей стоимости счёта в Lighter. Описаны формулы unrealized/realized PnL для перпетуалов, пересчёт средней цены входа при увеличении позиции и total account value. Отдельно указано, что средства изолированной позиции (Allocated Margin) учитываются как collateral. Разработчику торгового бота это полезно для самостоятельного воспроизведения расчётов.

### [Fair Price Marking](https://docs.lighter.xyz/trading/fair-price-marking.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница документации **Fair Price Marking**, описывающая расчёт mark price для бессрочных контрактов Lighter. Разработчику торгового бота важно понимать, что mark price формируется как медиана из impact price, price1 и price2, где price1 учитывает индексную цену и ограниченную премию перпетуала через EMA, а price2 — медиану mark price централизованных бирж. Поскольку именно mark price используется при ликвидациях, его формула и оракулы (Chainlink, Stork, Pyth) помогают оценить риск принудительного закрытия позиции.

### [Self-Trade Prevention](https://docs.lighter.xyz/trading/self-trade-prevention.md) — новая страница ⚠️ **BREAKING**
_Раздел: Lighter Docs · тип: behavior_

Добавлена новая страница документации Self-Trade Prevention. Сейчас Lighter предотвращает самоторговлю: если пересекаются ордера одного аккаунта, resting order (maker) отменяется, а не исполняется. С 31 мая 2026, 08:00 ET (timestamp: 1780228800) поведение по умолчанию изменится с "reduce both" на "cancel maker": resting order будет отменён полностью, а incoming taker продолжит заполняться по книге. Явно указанные режимы self-trade behavior (cancel maker, cancel taker, cancel both, reduce both) останутся доступны как атрибуты ордера, изменение затрагивает только ордера без явного указания.

### [API](https://docs.lighter.xyz/trading/api.md) — новая страница
_Раздел: Lighter Docs · тип: docs_

Добавлена новая страница `API` в разделе Lighter Docs. Она содержит общие сведения о работе с Lighter: ссылки на полную документацию (`https://apidocs.lighter.xyz/docs/get-started`) и Telegram-канал обновлений, правила использования API-ключей (до 256 на аккаунт или суб-аккаунт) и описание модели аккаунтов на основе Ethereum-кошелька, включая создание суб-аккаунтов.

## 2026-07-31 — изменений: 103

### [Get Started](https://apidocs.lighter.xyz/docs/get-started.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка с указанием на llms.txt для получения полного индекса документации.

### [SDK](https://apidocs.lighter.xyz/docs/repos.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка с рекомендацией использовать https://apidocs.lighter.xyz/llms.txt для навигации по документации перед изучением страниц.

### [API keys](https://apidocs.lighter.xyz/docs/api-keys.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка в начале страницы с ссылкой на полный индекс документации (`https://apidocs.lighter.xyz/llms.txt`). Остальной текст без изменений.

### [Rate Limits](https://apidocs.lighter.xyz/docs/rate-limits.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка в начале страницы с указанием на `https://apidocs.lighter.xyz/llms.txt` для получения полного индекса документации.

### [Account Types](https://apidocs.lighter.xyz/docs/account-types.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка с указанием на файл llms.txt для навигации по документации. Остальное содержимое без изменений.

### [Volume Quota](https://apidocs.lighter.xyz/docs/volume-quota-program.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена инструкция в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` Это не влияет на API, лимиты или поведение.

### [Signing Transactions](https://apidocs.lighter.xyz/docs/trading.md) — изменена
_Раздел: Guides · тип: docs_

В начало страницы добавлена строка с ссылкой на полный индекс документации (https://apidocs.lighter.xyz/llms.txt). Остальной текст без изменений.

### [WebSocket](https://apidocs.lighter.xyz/docs/websocket-reference.md) — изменена
_Раздел: Guides · тип: docs_

В начало страницы добавлена ссылка на полный индекс документации (`https://apidocs.lighter.xyz/llms.txt`) с рекомендацией использовать его для навигации. Остальное содержимое без изменений.

### [Historical Data](https://apidocs.lighter.xyz/docs/historical-data.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена ссылка на индекс документации `https://apidocs.lighter.xyz/llms.txt` в начале страницы, чтобы помочь пользователям находить другие страницы документации.

### [Partner Attribution](https://apidocs.lighter.xyz/docs/partner-integration.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена инструкция в начале страницы: "Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further." Остальное содержимое (текст, примеры, поля) без изменений.

### [Manage Referrals](https://apidocs.lighter.xyz/docs/referrals.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка с инструкцией о получении полного индекса документации по ссылке https://apidocs.lighter.xyz/llms.txt. Текст рекомендации перед основным содержанием: "Fetch the complete documentation index at:...". Содержание раздела Manage Referrals не изменилось.

### [Priority Transactions](https://apidocs.lighter.xyz/docs/priority-transactions.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена ссылка на `llms.txt` (`https://apidocs.lighter.xyz/llms.txt`) в начале страницы для навигации по полному индексу документации.

### [Manage Public Pools](https://apidocs.lighter.xyz/docs/manage-public-pools-shares.md) — изменена
_Раздел: Guides · тип: docs_

В начало страницы добавлена строка с ссылкой на llms.txt для полного индекса документации. Содержание, эндпоинты, параметры и примеры не изменились.

### [Multi-signature and smart wallets](https://apidocs.lighter.xyz/docs/multi-signature-wallets.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка с инструкцией по получению полного индекса документации по ссылке https://apidocs.lighter.xyz/llms.txt.

### [Create accounts programmatically](https://apidocs.lighter.xyz/docs/create-accounts-programmatically.md) — изменена
_Раздел: Guides · тип: docs_

В начало страницы добавлена строка с рекомендацией загрузить полный индекс документации по адресу https://apidocs.lighter.xyz/llms.txt для поиска всех доступных страниц. Остальной текст не изменился.

### [Deposits, Transfers and Withdrawals](https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals.md) — изменена
_Раздел: Guides · тип: docs_

Добавлена строка-подсказка в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` Остальное содержимое без изменений.

### [Data Structures, Constants and Errors](https://apidocs.lighter.xyz/docs/data-structures-constants-and-errors.md) — изменена
_Раздел: Guides · тип: docs_

В начало страницы добавлена строка-инструкция `Fetch the complete documentation index at: https...`, предлагающая использовать llms.txt для навигации по документации. Остальное содержимое полностью идентично старой версии.

### [systemConfig](https://apidocs.lighter.xyz/reference/systemconfig.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с рекомендацией использовать https://apidocs.lighter.xyz/llms.txt для получения полного индекса документации. Сама спецификация API не изменилась.

### [status](https://apidocs.lighter.xyz/reference/status.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая инструкция с ссылкой на llms.txt для навигации по документации. API и схемы без изменений.

### [info](https://apidocs.lighter.xyz/reference/info-1.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-рекомендация в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt` — указание на индекс всех страниц документации.

### [layer1BasicInfo](https://apidocs.lighter.xyz/reference/layer1basicinfo.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начале страницы с ссылкой на llms.txt для навигации по документации. API не изменилось.

### [account](https://apidocs.lighter.xyz/reference/account-1.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена ссылка на полный индекс документации `https://apidocs.lighter.xyz/llms.txt` для навигации по всем страницам. Остальное содержимое (описание, OpenAPI-спецификация, схемы) не изменилось.

### [accountLimits](https://apidocs.lighter.xyz/reference/accountlimits.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с рекомендацией использовать полный индекс документации (`https://apidocs.lighter.xyz/llms.txt`) в начале страницы. API и OpenAPI-спецификация не изменились.

### [accountMetadata](https://apidocs.lighter.xyz/reference/accountmetadata.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция по получению полного индекса документации (`https://apidocs.lighter.xyz/llms.txt`) в начале страницы. Остальное — без изменений.

### [accountsByL1Address](https://apidocs.lighter.xyz/reference/accountsbyl1address.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на полный индекс документации (`llms.txt`) в начале страницы.

### [changeAccountTier](https://apidocs.lighter.xyz/reference/changeaccounttier.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена ссылка на полный индекс документации: `https://apidocs.lighter.xyz/llms.txt` для удобства навигации. Сами спецификации endpoint'а `changeAccountTier`, его параметры, схемы и лимиты не изменились.

### [l1Metadata](https://apidocs.lighter.xyz/reference/l1metadata.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая подсказка `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` перед заголовком. Эндпоинт, параметры, схемы и лимиты не изменились.

### [liquidations](https://apidocs.lighter.xyz/reference/liquidations.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена строка: "Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.". Эндпоинты, параметры, схемы и лимиты не изменились.

### [pnl](https://apidocs.lighter.xyz/reference/pnl.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt`. Остальное содержимое без изменений.

### [positionFunding](https://apidocs.lighter.xyz/reference/positionfunding.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с рекомендацией использовать `llms.txt` для навигации по документации (`Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt`). Остальное содержимое страницы (эндпоинт, параметры, схемы, лимиты) не изменилось.

### [publicPoolsMetadata](https://apidocs.lighter.xyz/reference/publicpoolsmetadata.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая инструкция в начале страницы: ссылка на https://apidocs.lighter.xyz/llms.txt для получения полного индекса документации. Эндпоинт, параметры, лимиты и схемы не изменились.

### [accountOrders](https://apidocs.lighter.xyz/reference/accountorders.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с рекомендацией загрузить полный индекс документации по ссылке https://apidocs.lighter.xyz/llms.txt перед дальнейшим изучением.

### [accountActiveOrders](https://apidocs.lighter.xyz/reference/accountactiveorders.md) — изменена
_Раздел: API Reference · тип: docs_

Изменений в API нет. Добавлена ссылка на полный индекс документации (`llms.txt`) в начале страницы. Все эндпоинты, параметры, поля, схемы и лимиты остались без изменений.

### [accountInactiveOrders](https://apidocs.lighter.xyz/reference/accountinactiveorders.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с инструкцией по получению полного индекса документации по ссылке https://apidocs.lighter.xyz/llms.txt. API-спецификация и поведение эндпоинта не изменились.

### [export](https://apidocs.lighter.xyz/reference/export.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt` в начале страницы — подсказка для навигации по документации. Эндпоинты, параметры, лимиты и схемы ответа не изменились.

### [assetDetails](https://apidocs.lighter.xyz/reference/assetdetails.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с рекомендацией использовать `llms.txt` для навигации по документации. API-спецификация и схемы не изменились.

### [orderBookDetails](https://apidocs.lighter.xyz/reference/orderbookdetails.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-инструкция в начале страницы с ссылкой на llms.txt для навигации по документации. Само API — эндпоинт, параметры, схемы — не изменилось.

### [orderBookOrders](https://apidocs.lighter.xyz/reference/orderbookorders.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на `https://apidocs.lighter.xyz/llms.txt` для навигации по полной документации. Остальное содержимое страницы не изменилось.

### [orderBooks](https://apidocs.lighter.xyz/reference/orderbooks.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` — ссылка на индекс документации для навигации.

### [recentTrades](https://apidocs.lighter.xyz/reference/recenttrades.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели не разобрался — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [trades](https://apidocs.lighter.xyz/reference/trades.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начале страницы — ссылка на полный индекс документации по адресу `https://apidocs.lighter.xyz/llms.txt`

### [sendTx](https://apidocs.lighter.xyz/reference/sendtx.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с инструкцией загрузить llms.txt для навигации по документации.

### [sendTxBatch](https://apidocs.lighter.xyz/reference/sendtxbatch.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на индекс документации (`https://apidocs.lighter.xyz/llms.txt`) в начале страницы. Эндпоинт `/api/v1/sendTxBatch`, его параметры, схемы `ReqSendTxBatch` и `RespSendTxBatch` не изменились.

### [tx](https://apidocs.lighter.xyz/reference/tx.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена ссылка на https://apidocs.lighter.xyz/llms.txt с предложением использовать её для поиска всех доступных страниц. Само OpenAPI-определение и структура ответа не изменились.

### [txFromL1TxHash](https://apidocs.lighter.xyz/reference/txfroml1txhash.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая инструкция в начале страницы с предложением загрузить полный индекс документации по ссылке https://apidocs.lighter.xyz/llms.txt. Само API-описание эндпоинта `/api/v1/txFromL1TxHash`, его параметры, схемы и ответы не изменились.

### [deposit_history](https://apidocs.lighter.xyz/reference/deposit_history.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на индекс документации `https://apidocs.lighter.xyz/llms.txt` для навигации по страницам. Структура API, параметры и схемы не изменились.

### [transfer_history](https://apidocs.lighter.xyz/reference/transfer_history.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена строка с рекомендацией использовать индекс документации по адресу https://apidocs.lighter.xyz/llms.txt. Сама спецификация API не изменилась.

### [withdraw_history](https://apidocs.lighter.xyz/reference/withdraw_history.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на полный индекс документации (`https://apidocs.lighter.xyz/llms.txt`) в начале страницы; остальное содержимое без изменений

### [announcement](https://apidocs.lighter.xyz/reference/announcement-1.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена текстовая инструкция с ссылкой на полный индекс документации (https://apidocs.lighter.xyz/llms.txt). API, параметры, поля и лимиты не изменились.

### [apikeys](https://apidocs.lighter.xyz/reference/apikeys.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-подсказка в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt`, которая рекомендует использовать этот файл для навигации. API, схемы, параметры и поведение эндпоинта `/api/v1/apikeys` не изменились.

### [nextNonce](https://apidocs.lighter.xyz/reference/nextnonce.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на индекс документации (`https://apidocs.lighter.xyz/llms.txt`) в начале страницы, рекомендующая использовать его для навигации. OpenAPI-определение эндпоинта `/api/v1/nextNonce` не изменилось.

### [tokens_create](https://apidocs.lighter.xyz/reference/tokens_create.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с указанием на индекс документации (`https://apidocs.lighter.xyz/llms.txt`) в начале страницы для удобства навигации. Сама спецификация API и все поля/параметры не изменились.

### [tokens_revoke](https://apidocs.lighter.xyz/reference/tokens_revoke.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-подсказка в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.`

### [tokens](https://apidocs.lighter.xyz/reference/tokens.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена инструкция с ссылкой на общий индекс документации `https://apidocs.lighter.xyz/llms.txt`.

### [setMakerOnlyApiKeys](https://apidocs.lighter.xyz/reference/setmakeronlyapikeys.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на llms.txt и инструкция по её использованию для навигации по документации. API, параметры, лимиты и поведение не изменились.

### [getMakerOnlyApiKeys](https://apidocs.lighter.xyz/reference/getmakeronlyapikeys.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с рекомендацией использовать `llms.txt` для навигации по документации. OpenAPI-спецификация и поведение эндпоинта не изменились.

### [marketPriceCharts](https://apidocs.lighter.xyz/reference/marketpricecharts.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлен текст с рекомендацией использовать llms.txt для навигации. Спецификация OpenAPI и всё поведение API остались без изменений.

### [markPriceCandles](https://apidocs.lighter.xyz/reference/markpricecandles.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая строка с рекомендацией загрузить полный индекс документации по ссылке `https://apidocs.lighter.xyz/llms.txt` перед заголовком страницы. Остальное содержимое без изменений.

### [candles](https://apidocs.lighter.xyz/reference/candles.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt`. Смысловых изменений в API, параметрах, полях или лимитах нет.

### [createIntentAddress](https://apidocs.lighter.xyz/reference/createintentaddress.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начало страницы: ссылка на `https://apidocs.lighter.xyz/llms.txt` для получения полного индекса документации. API, параметры и поля не изменились.

### [fastbridge_info](https://apidocs.lighter.xyz/reference/fastbridge_info.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на индекс документации `llms.txt` в начале страницы. Само API-описание не изменилось.

### [deposit_latest](https://apidocs.lighter.xyz/reference/deposit_latest.md) — изменена
_Раздел: API Reference · тип: cosmetic_

Добавлена ссылка на полный индекс документации (https://apidocs.lighter.xyz/llms.txt) в начале страницы. Никаких изменений в эндпоинтах, параметрах, полях ответа или схемах нет.

### [deposit_networks](https://apidocs.lighter.xyz/reference/deposit_networks.md) — изменена
_Раздел: API Reference · тип: docs_

В начало документации добавлена инструкция: «Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt». Никаких изменений в эндпоинтах, параметрах, полях, лимитах или поведении нет.

### [fastwithdraw](https://apidocs.lighter.xyz/reference/fastwithdraw.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена строка: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` — это инструкция для навигации по API-документации. Содержание OpenAPI-спецификации (`/api/v1/fastwithdraw`) не изменилось.

### [fastwithdraw_info](https://apidocs.lighter.xyz/reference/fastwithdraw_info.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая инструкция в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` Само содержимое эндпоинта (параметры, поля, схема ответа, лимиты) не изменилось.

### [fundings](https://apidocs.lighter.xyz/reference/fundings.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-подсказка с призывом использовать индексный файл `https://apidocs.lighter.xyz/llms.txt` для поиска всех страниц документации. API и схемы данных не изменились.

### [funding-rates](https://apidocs.lighter.xyz/reference/funding-rates.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлено примечание со ссылкой на индекс документации `https://apidocs.lighter.xyz/llms.txt`. Остальное содержимое (эндпоинт, схемы, лимиты) не изменилось.

### [notification_ack](https://apidocs.lighter.xyz/reference/notification_ack.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-подсказка в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` Само определение API (`notification_ack`) не изменилось.

### [exchangeStats](https://apidocs.lighter.xyz/reference/exchangestats.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена рекомендация использовать `llms.txt` для навигации по документации. Эндпоинт `/api/v1/exchangeStats`, его параметры, поля ответа и схемы не изменились.

### [exchangeMetrics](https://apidocs.lighter.xyz/reference/exchangemetrics.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлен текст с рекомендацией использовать `https://apidocs.lighter.xyz/llms.txt` для поиска других страниц документации. OpenAPI-спецификация эндпоинта `/api/v1/exchangeMetrics` не изменилась.

### [partnerStats](https://apidocs.lighter.xyz/reference/partnerstats.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.` перед заголовком страницы. Сам эндпоинт `/api/v1/partnerStats`, его параметры, схема ответа и лимиты не изменились.

### [executeStats](https://apidocs.lighter.xyz/reference/executestats.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлено примечание в начале страницы с ссылкой на полный индекс документации `https://apidocs.lighter.xyz/llms.txt` для навигации по всем страницам. Сам эндпоинт `/api/v1/executeStats`, его параметры, схемы ответов и лимиты не изменились.

### [transferFeeInfo](https://apidocs.lighter.xyz/reference/transferfeeinfo.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена инструкция о полном индексе документации по адресу https://apidocs.lighter.xyz/llms.txt. Само API-определение `/api/v1/transferFeeInfo` не изменилось.

### [withdrawalDelay](https://apidocs.lighter.xyz/reference/withdrawaldelay.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на полный индекс документации (`https://apidocs.lighter.xyz/llms.txt`) в начале страции. API-спецификация `withdrawalDelay` не изменилась.

### [tokenlist](https://apidocs.lighter.xyz/reference/tokenlist-1.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на `llms.txt` в начале страницы для навигации по всей документации. OpenAPI-спецификация и поведение эндпоинта `/api/v1/tokenlist` не изменились.

### [syntheticSpotInfo](https://apidocs.lighter.xyz/reference/syntheticspotinfo.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начале страницы: 'Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.' Само описание API и OpenAPI-спецификация не изменились.

### [referral_create](https://apidocs.lighter.xyz/reference/referral_create.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена рекомендация использовать `llms.txt` для навигации по документации.

### [referral_get](https://apidocs.lighter.xyz/reference/referral_get.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена строка с ссылкой на полный индекс документации (`https://apidocs.lighter.xyz/llms.txt`) для упрощения навигации. Остальное содержимое (эндпоинт, параметры, схемы ответов) не изменилось.

### [referral_kickback_update](https://apidocs.lighter.xyz/reference/referral_kickback_update.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-ссылка на полный индекс документации (llms.txt) в начале страницы. API и схемы не изменились.

### [referral_update](https://apidocs.lighter.xyz/reference/referral_update.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на индекс документации `https://apidocs.lighter.xyz/llms.txt` в начало страницы для удобной навигации. API-спецификация не изменилась.

### [referral_use](https://apidocs.lighter.xyz/reference/referral_use.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка в начале страницы с инструкцией получить полный индекс документации по адресу `https://apidocs.lighter.xyz/llms.txt`. API, эндпоинт, параметры, схемы и лимиты не изменились.

### [userReferrals](https://apidocs.lighter.xyz/reference/referral_userreferrals.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая инструкция в начале страницы: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt` — ссылка на индекс документации. API и схема не изменились.

### [leaseOptions](https://apidocs.lighter.xyz/reference/leaseoptions.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели не разобрался — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [leases](https://apidocs.lighter.xyz/reference/leases.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена ссылка на индекс документации `https://apidocs.lighter.xyz/llms.txt` в начале страницы. Эндпоинт `/api/v1/leases`, параметры, схемы ответов и лимиты не изменились.

### [litLease](https://apidocs.lighter.xyz/reference/litlease.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-инструкция в начале страницы, рекомендующая использовать llms.txt для поиска страниц документации. API не изменилось.

### [rfq_create](https://apidocs.lighter.xyz/reference/rfq_create.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-подсказка с ссылкой на индекс документации `llms.txt`. Остальное содержимое без изменений.

### [rfq_respond](https://apidocs.lighter.xyz/reference/rfq_respond.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начале страницы: предлагается загрузить полный индекс документации по ссылке https://apidocs.lighter.xyz/llms.txt перед дальнейшим изучением. API-спецификация не изменилась.

### [rfq_update](https://apidocs.lighter.xyz/reference/rfq_update.md) — изменена
_Раздел: API Reference · тип: docs_

В начало страницы добавлена инструкция с ссылкой на полный индекс документации `https://apidocs.lighter.xyz/llms.txt`. OpenAPI-спецификация и все эндпоинты, параметры, поля и лимиты остались без изменений.

### [rfq_get](https://apidocs.lighter.xyz/reference/rfq_get.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели не разобрался — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [rfq_list](https://apidocs.lighter.xyz/reference/rfq_list.md) — изменена
_Раздел: API Reference · тип: docs_

В начале документации добавлена текстовая строка с указанием загружать полный индекс документации по ссылке `https://apidocs.lighter.xyz/llms.txt`. Сама OpenAPI-спецификация эндпоинта `/api/v1/rfq/list` не изменилась — все параметры, поля и схемы ответа остались прежними.

### [logs](https://apidocs.lighter.xyz/reference/get_accounts-param-logs.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели не разобрался — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [positions](https://apidocs.lighter.xyz/reference/get_accounts-param-positions.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели выродился в повтор — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [assets](https://apidocs.lighter.xyz/reference/get_accounts-param-assets.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка с рекомендацией использовать `https://apidocs.lighter.xyz/llms.txt` для навигации по документации перед дальнейшим изучением.

### [batches](https://apidocs.lighter.xyz/reference/get_batches.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция для загрузки полного индекса документации через `llms.txt`. Сама OpenAPI-спецификация не изменилась.

### [batchId](https://apidocs.lighter.xyz/reference/get_batches-batchid.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели не разобрался — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [blocks](https://apidocs.lighter.xyz/reference/get_blocks.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели не разобрался — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [blockId](https://apidocs.lighter.xyz/reference/get_blocks-blockid.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели выродился в повтор — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [hash](https://apidocs.lighter.xyz/reference/get_logs-hash.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели не разобрался — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [markets](https://apidocs.lighter.xyz/reference/get_markets.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начале страницы: «Fetch the complete documentation index at https://apidocs.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further.» API-спецификация не изменилась.

### [logs](https://apidocs.lighter.xyz/reference/get_markets-symbol-logs.md) — изменена
_Раздел: API Reference · тип: ?_

_(ответ модели выродился в повтор — отброшен при чистке 2026-07-31, страница будет перепроверена)_

### [search](https://apidocs.lighter.xyz/reference/get_search.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена текстовая строка в начале страницы с рекомендацией использовать `llms.txt` для навигации по документации: `Fetch the complete documentation index at: https://apidocs.lighter.xyz/llms.txt`. API-спецификация, эндпоинты, параметры и схемы не изменились.

### [tx](https://apidocs.lighter.xyz/reference/get_stats-tx.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена инструкция в начале страницы со ссылкой на `https://apidocs.lighter.xyz/llms.txt` для навигации по документации. API, параметры, схемы и лимиты остались без изменений.

### [total](https://apidocs.lighter.xyz/reference/get_total.md) — изменена
_Раздел: API Reference · тип: docs_

Добавлена строка-подсказка с ссылкой на полный индекс документации (`llms.txt`) в начале страницы. Само API, эндпоинты, параметры, поля и лимиты не изменились.

## 2026-07-30 — изменений: 2

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

