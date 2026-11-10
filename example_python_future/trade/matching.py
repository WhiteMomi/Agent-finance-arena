"""极简撮合。

原型只使用日线,因此:
- 市价单按当日收盘价成交,并可附加滑点;
- 限价单只要当日价格区间触及限价即可成交,成交价取限价。
"""

from __future__ import annotations

from trade.models import Bar, Order, OrderType, Side


class MatchingEngine:
    def __init__(self, slippage_rate: float = 0.0) -> None:
        self.slippage_rate = slippage_rate

    def match(self, order: Order, bar: Bar) -> tuple[float | None, str]:
        if order.order_type == OrderType.MARKET:
            direction = 1 if order.side == Side.BUY else -1
            price = bar.close * (1 + direction * self.slippage_rate)
            return price, "市价成交"

        if order.side == Side.BUY:
            if bar.low <= order.price:
                return order.price, "限价成交"
            return None, "未成交:当日最低价未触及买价"

        if bar.high >= order.price:
            return order.price, "限价成交"
        return None, "未成交:当日最高价未触及卖价"
