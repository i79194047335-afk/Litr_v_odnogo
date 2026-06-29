"""
Построение range-баров из потока сделок.

Range-бар закрывается, когда диапазон (high-low) внутри бара достигает
range_size (в ЦЕНОВЫХ шагах инструмента, умноженных на tick_size).
Это «фрактал цены», не привязанный ко времени — основа стратегии Беггса
и дневника mrcvokka.

ВАЖНО про бэктест-честность: бар отдаётся ТОЛЬКО когда он закрыт. Внутри
бара мы не «знаем» будущую цену. При моделировании исполнения лимиток
опирайся на цену внутри бара по ходу её движения, а не на close готового бара.
"""
from dataclasses import dataclass, field
from typing import Iterator, Iterable


@dataclass
class RangeBar:
    open: float
    high: float
    low: float
    close: float
    start_ts: int
    end_ts: int
    n_ticks: int = 0


@dataclass
class RangeBarBuilder:
    range_size: float          # размер бара в единицах цены (range_ticks * tick_size)
    _cur: RangeBar | None = field(default=None, init=False)

    def update(self, price: float, ts: int) -> Iterator[RangeBar]:
        """Подать одну сделку. Возвращает закрытые бары (0..n)."""
        if self._cur is None:
            self._cur = RangeBar(price, price, price, price, ts, ts, 1)
            return

        b = self._cur
        b.high = max(b.high, price)
        b.low = min(b.low, price)
        b.close = price
        b.end_ts = ts
        b.n_ticks += 1

        # пока диапазон укладывается в range_size — бар живёт
        while (b.high - b.low) >= self.range_size:
            # закрываем бар на границе диапазона в сторону движения
            if b.close >= b.open:
                close_px = b.low + self.range_size
                yield RangeBar(b.open, close_px, b.low, close_px, b.start_ts, ts, b.n_ticks)
                new_open = close_px
            else:
                close_px = b.high - self.range_size
                yield RangeBar(b.open, b.high, close_px, close_px, b.start_ts, ts, b.n_ticks)
                new_open = close_px
            # начинаем новый бар от границы; остаток движения переносим
            b = RangeBar(new_open, max(new_open, price), min(new_open, price), price, ts, ts, 0)
            self._cur = b


def build_from_ticks(ticks: Iterable[tuple[float, int]], range_size: float) -> list[RangeBar]:
    """ticks: итерабельность (price, ts). Возвращает список закрытых баров."""
    builder = RangeBarBuilder(range_size=range_size)
    out: list[RangeBar] = []
    for price, ts in ticks:
        out.extend(builder.update(price, ts))
    return out
