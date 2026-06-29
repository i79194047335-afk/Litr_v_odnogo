"""
Сборщик сделок (тиков) с Lighter по WebSocket → построчный JSON в data/ticks.

СТАТУС: СТАБ. Здесь намеренно НЕТ готового кода под конкретный SDK, потому что:
  - на форуме отмечали, что официальный SDK Lighter сырой и его допиливают руками
    (Python и TypeScript версии); реальный интерфейс надо проверить перед кодом;
  - для СБОРА публичного потока сделок приватный ключ не нужен — только WS-эндпоинт.

TODO перед запуском:
  1. Уточнить реальный WS-URL и формат сообщения trades у Lighter (docs/SDK/репо).
  2. Подставить парсинг в _parse_trade().
  3. Прогнать на 1 символе, проверить, что пишутся (price, ts) и tick_size корректен.
"""
import json, os, asyncio
from datetime import datetime, timezone

import websockets            # см. requirements.txt
from loguru import logger


def _parse_trade(msg: dict) -> tuple[float, int] | None:
    """Вернуть (price, ts_ms) из сообщения Lighter. TODO: заполнить по факту."""
    raise NotImplementedError("Подставить парсинг под реальный формат Lighter WS")


async def collect(ws_url: str, symbols: list[str], out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    # TODO: подписка на каналы trades для symbols — формат subscribe уточнить
    async for ws in websockets.connect(ws_url, ping_interval=20):
        try:
            # await ws.send(json.dumps({"type": "subscribe", ...}))
            async for raw in ws:
                trade = _parse_trade(json.loads(raw))
                if not trade:
                    continue
                price, ts = trade
                day = datetime.now(timezone.utc).strftime("%Y%m%d")
                with open(f"{out_dir}/trades_{day}.jsonl", "a") as f:
                    f.write(json.dumps({"p": price, "t": ts}) + "\n")
        except websockets.ConnectionClosed:
            logger.warning("WS closed, reconnecting...")
            continue


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    asyncio.run(collect(
        ws_url=os.environ["LIGHTER_WS_URL"],
        symbols=cfg["collector"]["symbols"],
        out_dir=cfg["collector"]["out_dir"],
    ))
