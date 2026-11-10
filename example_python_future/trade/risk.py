"""报单前风控。所有规则由代码硬约束,Agent 只能提交意图。"""

from __future__ import annotations

from trade.models import Account, ContractSpec, Offset, Order, Side


class OrderRejected(Exception):
    """订单被风控拒绝。"""


class RiskManager:
    def __init__(self, margin_call_threshold: float = 0.8, force_close_threshold: float = 1.0) -> None:
        self.margin_call_threshold = margin_call_threshold
        self.force_close_threshold = force_close_threshold

    def validate(
        self,
        order: Order,
        account: Account,
        spec: ContractSpec,
        ref_price: float,
        prev_settle: float,
        current_date: str,
        prices: dict[str, float] | None = None,
        specs: dict[str, ContractSpec] | None = None,
    ) -> None:
        if order.qty <= 0:
            raise OrderRejected("手数必须为正整数")
        if order.qty > spec.max_order_qty:
            raise OrderRejected(f"超过单笔最大下单量 {spec.max_order_qty}")

        if current_date > spec.last_trade_date:
            raise OrderRejected("合约已过最后交易日")

        up = spec.price_limit_up(prev_settle)
        down = spec.price_limit_down(prev_settle)
        if not (down - 1e-9 <= ref_price <= up + 1e-9):
            raise OrderRejected(f"价格 {ref_price} 超出涨跌停范围 [{down}, {up}]")

        rounded = spec.round_to_tick(ref_price)
        if abs(ref_price - rounded) > 1e-9:
            raise OrderRejected(f"价格不满足最小变动价位 {spec.tick_size}")

        if prices is None:
            prices = {spec.symbol: ref_price}
        if specs is None:
            specs = {spec.symbol: spec}

        if order.offset == Offset.OPEN:
            if order.side == Side.BUY:
                future = account.position(spec.symbol).long_qty + order.qty
            else:
                future = account.position(spec.symbol).short_qty + order.qty
            if future > spec.position_limit:
                raise OrderRejected(f"超过持仓限额 {spec.position_limit}")

            margin = spec.margin_required(ref_price, order.qty)
            fee = spec.fee_open * order.qty
            available = account.available(prices, specs)
            if available + 1e-9 < margin + fee:
                raise OrderRejected(f"可用资金不足:需要 {margin + fee:.2f},可用 {available:.2f}")
        else:
            pos = account.position(spec.symbol)
            if order.side == Side.SELL:
                closable = pos.long_today if order.offset == Offset.CLOSE_TODAY else pos.long_qty
            else:
                closable = pos.short_today if order.offset == Offset.CLOSE_TODAY else pos.short_qty
            if closable < order.qty:
                raise OrderRejected(f"可平仓位不足:需要 {order.qty},可平 {closable}")
