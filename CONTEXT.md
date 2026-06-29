# Контекст проекта Litr_v_odnogo — handoff

> Этот файл — для возобновления работы в новом чате. Прочитай его целиком,
> а не пересказ. Дальше в репозитории смотри `docs/ytc_scalper_skeleton.md`
> и `docs/kb_mrcvokka_diary.md` — там полные ресёрчи.

## TL;DR
Бэктест range-bar скальпинг-стратегии (семейство Lance Beggs / Songer / mrcvokka)
под Lighter perp DEX. Пользователь — новичок в крипте/трейдинге, осознанно
идёт по безопасному пути: **сначала сбор тиков → бэктест → пейпер**,
живой торговли в репо нет и не должно быть до доказанного бэктестом edge.
Это не финансовая рекомендация — учебно-исследовательский проект.

## Где код
- Репо: https://github.com/i79194047335-afk/Litr_v_odnogo (публичный)
- VPS: `vm1744139.vds.as210546.net`, проект в `/root/projects/Litr_v_odnogo`, venv в `.venv/`
- Workflow: GitHub source of truth → `git pull` на VPS → правки через bash heredoc → commit → push
- Защита секретов: pre-commit с gitleaks (локально) + GitHub Action gitleaks (бэкстоп)

## Что РАБОТАЕТ (на момент handoff)
1. **Range-bar builder** (`src/rangebars/builder.py`) — чистая логика, 2 теста проходят
2. **Lighter trade collector** (`src/collector/lighter_ticks.py`) — крутится 24/7 как systemd-сервис `lighter-ticks.service`
   - Сырой WS `wss://mainnet.zklighter.elliot.ai/stream`, без авторизации
   - Подписка на `trade/{market_id}`
   - JSONL по дням в `data/ticks/trades_{market_id}_{YYYYMMDD}.jsonl`
   - Формат: `{"m": int, "p": float, "s": float, "t": ms, "side": "buy"|"sell"}`
   - Side из `is_maker_ask` (taker side = aggressor)
   - Tenacity-backoff + systemd Restart=always

## Подтверждённые факты о Lighter (выяснили в чате)
- **SDK** на pypi называется `lighter-sdk` (импорт `import lighter`). Ставится из git: `pip install git+https://github.com/elliottech/lighter-python.git`
- **WS**: `wss://mainnet.zklighter.elliot.ai/stream`, ping/pong через `{"type":"pong"}`, требует фрейм каждые 2 минуты
- **Публичные WS-каналы** (без auth): `order_book`, `trade`, `ticker`, `market_stats`, `spot_market_stats`
- **Встроенный `lighter.WsClient` поддерживает только** `order_book` и `account_all` — для трейдов нужен raw WS (что мы и делаем)
- **Market IDs**: ETH = 0, BTC = 1, ZK = 56 (всего ~150+ маркетов; используй `python -m src.collector.list_markets`)
- **Комиссии**: maker = taker = **0.0000** на ETH и BTC perp (проверено). Это меняет картину — скальп тут жизнеспособнее, чем на CEX
- **Поля trade-сообщения** (фактический пример, не из доков):
  ```
  trade_id, market_id (int), size (str), price (str), usd_amount (str),
  is_maker_ask (bool), timestamp (ms), block_height, ask/bid_account_id, ...
  ```
- **Размеры/точность**: ETH supported_size_decimals=4, supported_price_decimals=2, min_base_amount=0.005
  BTC supported_size_decimals=5, supported_price_decimals=1, min_base_amount=0.0002
- **Архитектура**: Lighter технически на Arbitrum (zkL2), почти анонимно (ZK), per-account чужие сделки не отследить
- **Колокация для low-latency live**: AWS Tokyo `ap-northeast-1a` (это для live-этапа, не сейчас)
- **Историческая дата** на Lighter доступна на Tardis.dev с 2026-04-17 (платный с бесплатными samples первого числа месяца) — это резервный вариант, если не хотим ждать накопления

## Стратегическое ядро (что бэктестим)
Полный разбор в `docs/ytc_scalper_skeleton.md`. Сжато:
- **Источник**: Lance Beggs «YTC Scalper» + дневник mrcvokka на binguru.net (полный разбор в `docs/kb_mrcvokka_diary.md`, все 19 страниц прочитаны)
- **Подмножество, которое кодируется** (механический скелет):
  - HTF=5m, TF=1m, scalping chart = 1-range bars (строим сами из тиков)
  - Bias: EMA(15)/EMA(20) кросс на 1m (грубый прокси дискреционного определения тренда у Беггса)
  - Канал Кельтнера: Keltner(35, 4) + Keltner(35, 8) на range-барах
  - Wholesale/retail zones между линиями 0 / ¼ / ½ / ¾ / 1
  - **Только WF (with-flow) сетапы**, CF отбрасываем (требует чтения ленты)
  - Вход: лимит на ¼ и ½ линии, стоп за 0, 2 части
- **Эвристика размера range-bar (от mrcvokka)**: ~30% от средней минутной свечи за сессию, калибруется на истории
- **Альтернатива каналу** (опционально): стохастик (3,2,3) — использовал mrcvokka
- **Трал позиции критичен** — у mrcvokka доработка трейлинга дала ×5 к суточному PnL. Моделировать в бэктесте так же тщательно, как вход
- **Главное предупреждение**: без дискреции (выбор среды, чтения ленты) edge Беггса заметно слабее. Бэктест нужен, чтобы измерить *насколько*

## Что НЕ перенесли в бот (умышленно)
- Дискреционное определение bias (только PA + структура свингов у Беггса)
- Классификация среды (тренд/волатильно/пила) — а от неё зависит выбор линии входа
- Counter-flow (CF) сетапы — требуют чтения order flow / ленты
- Зоны S/R на M5 — рисуются глазами, не свингами

## План работ
1. ✅ Range-bar builder + tests
2. ✅ Lighter trade collector + systemd
3. **Сбор данных** (идёт сейчас, накапливается в data/ticks/)
4. **Опционально**: Tardis.dev интеграция для исторических данных
5. **Индикаторы**: EMA, Keltner (с формулой NT: `centerline = SMA(close, 35); band = centerline ± (mult * SMA(High-Low, 35))`), Stochastic — все с юнит-тестами
6. **Event-driven бэктестер**:
   - Загрузка тиков → конструктор range-баров → индикаторы → стратегия
   - Модель исполнения: лимитки (maker, на Lighter — fee=0), но **fill rate** обязательно моделировать (часть 2 у Беггса часто не исполняется)
   - Слиппедж в тиках, funding rate для перпов
7. **Метрики**: экспектанси в R, win-rate, профит-фактор, max DD, fill-rate части 2, walk-forward, sensitivity к параметрам
8. **Только после положительного бэктеста**: пейпер на живом WS-потоке
9. **Только после успешного пейпера**: разговор про live (этого пока нет в скоупе)

## Антипаттерны — НЕ повторять
- Bot полностью генерируется ИИ без понимания кода (у mrcvokka так — на коротких работает, на сутках разваливается)
- AI-агент с auto-execute (Claude Code запускающий код без ревью)
- Live торговля до прохождения бэктеста и пейпера
- Заявления типа "+20-162%/день" из дневника как бенчмарк — это ручной режим на макс плече, не воспроизводимо ботом

## Структура репо
```
src/
  collector/   lighter_ticks.py    ✅ работает (systemd)
               list_markets.py     ✅ работает
  rangebars/   builder.py          ✅ + tests
  indicators/  (пусто, следующий шаг)
  backtest/    (пусто, следующий шаг)
data/ticks/    JSONL накапливается
docs/
  ytc_scalper_skeleton.md          стратегический разбор Беггса
  kb_mrcvokka_diary.md             полный ресёрч 19 страниц форума
scripts/lighter-ticks.service      template (реальный unit в /etc/systemd/system/)
tests/test_rangebars.py            2 теста, проходят
.pre-commit-config.yaml            gitleaks локально
.github/workflows/gitleaks.yml     gitleaks серверный
config.example.yaml                эталон
config.yaml                        локальный, не в git
```

## Команды-памятка
```bash
# статус сборщика
sudo systemctl status lighter-ticks --no-pager
tail -f /var/log/lighter-ticks.log
wc -l data/ticks/*.jsonl

# рестарт после правок
sudo systemctl restart lighter-ticks

# пуш с правками
git add -A && git commit -m "..." && git push
```

## Профиль пользователя (важно учитывать)
- Новичок в крипте и трейдинге — начинал в этом проекте с нуля
- Понимает базу: wallets, stablecoins, DEX/CEX, leverage, TVL/APR/maker/taker, gas, RPC
- Программирование: не пишет код сам, ведёт работу через bash на VPS (создаёт файлы heredoc'ами), не использует IDE
- Принимает предупреждения о рисках, но иногда переоценивает темп — в новом чате стоит **держать порядок «бэктест → пейпер → live»** жёстко
- Предпочитает английский язык в общении (раньше был русский — переключился в середине этого чата)
- Тон: прямой, конструктивный, краткий; без сюсюканья; честно говорить о трейдоффах и о том, что я (Claude) могу и не могу

## Что я (Claude) НЕ могу
- Доступа к VPS у меня нет — все команды через пользователя
- Доступа к git-репо как «живому» источнику тоже нет — пользователь приносит срез
- Не могу создать репо на чужом GitHub за пользователя
- Долгосрочной памяти между чатами нет, поэтому существует этот файл
