"""期货领域对象。

这里刻意做了最小化简化,只保留构建一个可运行原型所需的字段:
- 多空分开记录(long_qty / short_qty),支持锁仓;
- 合约乘数、保证金率、涨跌停幅度是核心规则;
- 手续费按开/平/平今三档,平今更贵。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Offset(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    CLOSE_TODAY = "CLOSE_TODAY"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"


def _round(value: float, ndigits: int = 6) -> float:
    return round(float(value), ndigits)


@dataclass
class ContractSpec:
    """合约规格,字段含义见 futures_rules.md 第 2 节。"""

    symbol: str
    name: str = ""
    multiplier: float = 1.0
    tick_size: float = 1.0
    margin_rate: float = 0.1
    limit_pct: float = 0.06
    fee_open: float = 0.0
    fee_close: float = 0.0
    fee_close_today: float = 0.0
    last_trade_date: str = "9999-12-31"
    cash_settled: bool = False
    position_limit: int = 100
    max_order_qty: int = 20
    demo_start_price: float = 0.0

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.multiplier

    def contract_value(self, price: float, qty: int) -> float:
        return price * self.multiplier * qty

    def margin_required(self, price: float, qty: int) -> float:
        return self.contract_value(price, qty) * self.margin_rate

    def price_limit_up(self, prev_settle: float) -> float:
        return self.round_to_tick(prev_settle * (1 + self.limit_pct))

    def price_limit_down(self, prev_settle: float) -> float:
        return self.round_to_tick(prev_settle * (1 - self.limit_pct))

    def round_to_tick(self, price: float) -> float:
        ticks = round(price / self.tick_size)
        return ticks * self.tick_size

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "multiplier": self.multiplier,
            "tick_size": self.tick_size,
            "margin_rate": self.margin_rate,
            "limit_pct": self.limit_pct,
            "fee_open": self.fee_open,
            "fee_close": self.fee_close,
            "fee_close_today": self.fee_close_today,
            "last_trade_date": self.last_trade_date,
            "cash_settled": self.cash_settled,
            "position_limit": self.position_limit,
            "max_order_qty": self.max_order_qty,
            "demo_start_price": self.demo_start_price,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContractSpec":
        return cls(**data)


@dataclass
class Bar:
    """日线,settle 同时作为当日结算价(原型简化)。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    settle: float
    volume: float = 0.0
    open_interest: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "settle": self.settle,
            "volume": self.volume,
            "open_interest": self.open_interest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Bar":
        return cls(**data)


@dataclass
class Position:
    """双向持仓模型。

    long_avg / short_avg 是平仓盈亏的成本价;
    prev_settle 是用于当日盯市的基准价(昨结算价或当日开仓价)。
    """

    symbol: str
    long_qty: int = 0
    short_qty: int = 0
    long_avg: float = 0.0
    short_avg: float = 0.0
    long_today: int = 0
    short_today: int = 0
    prev_settle: float = 0.0

    @property
    def net_qty(self) -> int:
        return self.long_qty - self.short_qty

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "long_qty": self.long_qty,
            "short_qty": self.short_qty,
            "long_avg": self.long_avg,
            "short_avg": self.short_avg,
            "long_today": self.long_today,
            "short_today": self.short_today,
            "prev_settle": self.prev_settle,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        return cls(**data)


@dataclass
class Order:
    id: str
    symbol: str
    side: Side
    offset: Offset
    order_type: OrderType
    price: float
    qty: int
    status: OrderStatus = OrderStatus.PENDING
    message: str = ""
    filled_price: float = 0.0
    date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side.value,
            "offset": self.offset.value,
            "order_type": self.order_type.value,
            "price": self.price,
            "qty": self.qty,
            "status": self.status.value,
            "message": self.message,
            "filled_price": self.filled_price,
            "date": self.date,
        }


@dataclass
class Trade:
    id: str
    order_id: str
    symbol: str
    side: Side
    offset: Offset
    price: float
    qty: int
    fee: float
    pnl: float
    date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "offset": self.offset.value,
            "price": self.price,
            "qty": self.qty,
            "fee": self.fee,
            "pnl": self.pnl,
            "date": self.date,
        }


@dataclass
class Account:
    """资金与持仓总账。

    balance 是静态权益(盯市后实际划转的现金)。
    动态权益 equity = balance + 持仓浮动盈亏。
    """

    balance: float
    initial_balance: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    frozen: float = 0.0

    def position(self, symbol: str) -> Position:
        pos = self.positions.get(symbol)
        if pos is None:
            pos = Position(symbol=symbol)
            self.positions[symbol] = pos
        return pos

    def floating_pnl(self, prices: dict[str, float], specs: dict[str, ContractSpec]) -> float:
        total = 0.0
        for symbol, pos in self.positions.items():
            spec = specs[symbol]
            price = prices[symbol]
            total += (price - pos.long_avg) * spec.multiplier * pos.long_qty
            total += (pos.short_avg - price) * spec.multiplier * pos.short_qty
        return total

    def equity(self, prices: dict[str, float], specs: dict[str, ContractSpec]) -> float:
        return self.balance + self.floating_pnl(prices, specs)

    def margin_used(self, prices: dict[str, float], specs: dict[str, ContractSpec]) -> float:
        total = 0.0
        for symbol, pos in self.positions.items():
            spec = specs[symbol]
            price = prices[symbol]
            total += spec.margin_required(price, pos.long_qty + pos.short_qty)
        return total

    def available(self, prices: dict[str, float], specs: dict[str, ContractSpec]) -> float:
        return self.equity(prices, specs) - self.margin_used(prices, specs) - self.frozen

    def risk_ratio(self, prices: dict[str, float], specs: dict[str, ContractSpec]) -> float:
        equity = self.equity(prices, specs)
        if equity <= 0:
            return float("inf")
        return self.margin_used(prices, specs) / equity

    def to_dict(self) -> dict[str, Any]:
        return {
            "balance": self.balance,
            "initial_balance": self.initial_balance,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "frozen": self.frozen,
            "positions": {sym: pos.to_dict() for sym, pos in self.positions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Account":
        positions = {
            sym: Position.from_dict(item) for sym, item in data.get("positions", {}).items()
        }
        return cls(
            balance=float(data["balance"]),
            initial_balance=float(data["initial_balance"]),
            positions=positions,
            realized_pnl=float(data.get("realized_pnl", 0.0)),
            fees_paid=float(data.get("fees_paid", 0.0)),
            frozen=float(data.get("frozen", 0.0)),
        )
