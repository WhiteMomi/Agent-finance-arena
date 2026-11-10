"""生成演示用日线数据,保证离线也能跑通。"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from data.store import JsonStore
from trade.models import Account, Bar, ContractSpec


def _next_weekday(day: date) -> date:
    day = day + timedelta(days=1)
    while day.weekday() >= 5:
        day = day + timedelta(days=1)
    return day


def generate_demo_bars(
    symbol: str,
    days: int = 45,
    start_price: float = 3500.0,
    tick_size: float = 1.0,
    start_date: date | None = None,
    seed: int = 42,
) -> list[Bar]:
    random.seed(seed)
    day = start_date or date(2026, 9, 1)
    price = float(start_price)
    bars: list[Bar] = []
    for _ in range(days):
        open_price = price
        drift = math.sin(len(bars) / 5.0) * 12.0
        shock = random.uniform(-35.0, 35.0)
        raw_close = max(tick_size, price + drift + shock)
        close = round(raw_close / tick_size) * tick_size
        high = (max(open_price, close) + random.uniform(0, 12)) / tick_size
        low = (min(open_price, close) - random.uniform(0, 12)) / tick_size
        settle = close
        bars.append(
            Bar(
                date=day.isoformat(),
                open=open_price,
                high=round(high) * tick_size,
                low=round(max(0.0, low)) * tick_size,
                close=close,
                settle=settle,
                volume=round(random.uniform(500000, 2000000), 0),
                open_interest=round(random.uniform(800000, 1600000), 0),
            )
        )
        price = close
        day = _next_weekday(day)
    return bars


def seed_demo(
    store: JsonStore,
    specs: dict[str, ContractSpec],
    initial_balance: float,
    state_dir: str = "state",
) -> None:
    for spec in specs.values():
        start_price = spec.demo_start_price or _default_start_price(spec.symbol)
        seed = sum(ord(ch) for ch in spec.symbol) % 1000
        bars = generate_demo_bars(
            spec.symbol,
            tick_size=spec.tick_size,
            start_price=start_price,
            seed=seed,
        )
        store.save(f"market/{spec.symbol}.json", [bar.to_dict() for bar in bars])
    account = Account(balance=initial_balance, initial_balance=initial_balance)
    store.save(f"{state_dir}/account.json", account.to_dict())
    store.save(f"{state_dir}/orders.json", {"orders": [], "trades": []})


def _default_start_price(symbol: str) -> float:
    upper = symbol.upper()
    if upper.startswith("IH") or upper.startswith("IF"):
        return 4000.0 if upper.startswith("IF") else 2700.0
    if upper.startswith("IC"):
        return 5800.0
    if upper.startswith("IM"):
        return 6200.0
    if upper.startswith("CU"):
        return 70000.0
    if upper.startswith("AU"):
        return 600.0
    if upper.startswith("I"):
        return 800.0
    if upper.startswith("HC"):
        return 3600.0
    if upper.startswith("M"):
        return 3000.0
    return 3500.0
