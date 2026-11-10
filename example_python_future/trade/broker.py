"""Broker:负责把 Agent 的下单意图变成真实的账户变更。"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

from trade.matching import MatchingEngine
from trade.models import (
    Account,
    Bar,
    ContractSpec,
    Offset,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Trade,
)
from trade.risk import OrderRejected, RiskManager


class Broker:
    def __init__(
        self,
        account: Account,
        specs: dict[str, ContractSpec],
        store: Any | None = None,
        matching: MatchingEngine | None = None,
        risk: RiskManager | None = None,
        state_dir: str = "state",
    ) -> None:
        self.account = account
        self.specs = specs
        self.store = store
        self.state_dir = state_dir
        self.matching = matching or MatchingEngine()
        self.risk = risk or RiskManager()
        self.current_bars: dict[str, Bar] = {}
        self.prev_settles: dict[str, float] = {}
        self.orders: list[Order] = []
        self.trades: list[Trade] = []
        self._order_ids = itertools.count(1)
        self._trade_ids = itertools.count(1)

    def begin_day(self, bars: dict[str, Bar], prev_settles: dict[str, float]) -> None:
        self.current_bars = dict(bars)
        self.prev_settles = dict(prev_settles)

    def place_order(
        self,
        symbol: str,
        side: str,
        offset: str,
        qty: int,
        price: float | None = None,
        order_type: str = "LIMIT",
    ) -> Order:
        spec = self.specs.get(symbol)
        if spec is None:
            raise KeyError(f"未知合约 {symbol}")
        bar = self.current_bars.get(symbol)
        if bar is None:
            raise RuntimeError(f"{symbol} 在当前交易日没有行情")
        prev_settle = self.prev_settles.get(symbol, bar.settle)

        side_enum = Side(side)
        offset_enum = Offset(offset)
        type_enum = OrderType(order_type)
        limit_price = float(price) if price is not None else bar.close
        order = Order(
            id=f"O{next(self._order_ids):06d}",
            symbol=symbol,
            side=side_enum,
            offset=offset_enum,
            order_type=type_enum,
            price=limit_price,
            qty=int(qty),
            date=bar.date,
        )

        ref_price = bar.close if type_enum == OrderType.MARKET else limit_price
        prices = {sym: b.close for sym, b in self.current_bars.items()}
        try:
            self.risk.validate(
                order,
                self.account,
                spec,
                ref_price,
                prev_settle,
                bar.date,
                prices=prices,
                specs=self.specs,
            )
            fill_price, message = self.matching.match(order, bar)
        except OrderRejected as exc:
            order.status = OrderStatus.REJECTED
            order.message = str(exc)
            self.orders.append(order)
            self._save_state()
            return order

        if fill_price is None:
            order.status = OrderStatus.REJECTED
            order.message = message
            self.orders.append(order)
            self._save_state()
            return order

        fill_price = spec.round_to_tick(fill_price)
        fee, pnl = self._apply_fill(order, spec, fill_price)
        order.status = OrderStatus.FILLED
        order.message = message
        order.filled_price = fill_price
        self.orders.append(order)
        self.trades.append(
            Trade(
                id=f"T{next(self._trade_ids):06d}",
                order_id=order.id,
                symbol=symbol,
                side=side_enum,
                offset=offset_enum,
                price=fill_price,
                qty=order.qty,
                fee=fee,
                pnl=pnl,
                date=bar.date,
            )
        )
        self._save_state()
        return order

    def _apply_fill(self, order: Order, spec: ContractSpec, price: float) -> tuple[float, float]:
        pos = self.account.position(order.symbol)
        qty = order.qty

        if order.offset == Offset.OPEN:
            if order.side == Side.BUY:
                self._open_long(pos, price, qty)
            else:
                self._open_short(pos, price, qty)
            fee = spec.fee_open * qty
            self.account.balance -= fee
            self.account.fees_paid += fee
            return fee, 0.0

        if order.side == Side.SELL:
            today_close = self._close_allocation(
                total_qty=pos.long_qty,
                today_qty=pos.long_today,
                offset=order.offset,
                qty=qty,
            )
            pnl = (price - pos.long_avg) * spec.multiplier * qty
            fee = spec.fee_close_today * today_close + spec.fee_close * (qty - today_close)
            pos.long_qty -= qty
            pos.long_today -= today_close
            if pos.long_qty == 0:
                pos.long_avg = 0.0
                pos.long_today = 0
        else:
            today_close = self._close_allocation(
                total_qty=pos.short_qty,
                today_qty=pos.short_today,
                offset=order.offset,
                qty=qty,
            )
            pnl = (pos.short_avg - price) * spec.multiplier * qty
            fee = spec.fee_close_today * today_close + spec.fee_close * (qty - today_close)
            pos.short_qty -= qty
            pos.short_today -= today_close
            if pos.short_qty == 0:
                pos.short_avg = 0.0
                pos.short_today = 0

        self.account.balance += pnl - fee
        self.account.realized_pnl += pnl
        self.account.fees_paid += fee
        return fee, pnl

    def _open_long(self, pos: Position, price: float, qty: int) -> None:
        old_qty = pos.long_qty
        pos.long_avg = (pos.long_avg * old_qty + price * qty) / (old_qty + qty)
        pos.prev_settle = (
            (pos.prev_settle * old_qty + price * qty) / (old_qty + qty)
            if old_qty > 0
            else price
        )
        pos.long_qty += qty
        pos.long_today += qty

    def _open_short(self, pos: Position, price: float, qty: int) -> None:
        old_qty = pos.short_qty
        pos.short_avg = (pos.short_avg * old_qty + price * qty) / (old_qty + qty)
        pos.prev_settle = (
            (pos.prev_settle * old_qty + price * qty) / (old_qty + qty)
            if old_qty > 0
            else price
        )
        pos.short_qty += qty
        pos.short_today += qty

    @staticmethod
    def _close_allocation(
        total_qty: int, today_qty: int, offset: Offset, qty: int
    ) -> int:
        if offset == Offset.CLOSE_TODAY:
            return qty
        # CLOSE 优先平昨仓,不足再平今仓。
        yesterday_qty = total_qty - today_qty
        yesterday_closed = min(yesterday_qty, qty)
        return qty - yesterday_closed

    def save_state(self) -> None:
        self._save_state()

    def _save_state(self) -> None:
        if self.store is None:
            return
        self.store.save(f"{self.state_dir}/account.json", self.account.to_dict())
        self.store.save(
            f"{self.state_dir}/orders.json",
            {
                "orders": [o.to_dict() for o in self.orders],
                "trades": [t.to_dict() for t in self.trades],
            },
        )
