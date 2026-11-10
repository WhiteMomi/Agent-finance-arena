"""Tushare 数据引擎:从本地 JSON 读取,需要时同步 Tushare 日线。"""

from __future__ import annotations

import os
from typing import Any

from data.store import JsonStore
from trade.models import Bar


class TushareDataEngine:
    def __init__(self, store: JsonStore, token: str | None = None) -> None:
        self.store = store
        self.token = token or os.getenv("TUSHARE_TOKEN")
        self.current_date: str | None = None

    def set_current_date(self, date: str | None) -> None:
        """回测时限定数据可见范围,避免未来函数。"""
        self.current_date = date

    def _market_file(self, symbol: str) -> str:
        return f"market/{symbol}.json"

    def get_bars(self, symbol: str, limit: int | None = None) -> list[Bar]:
        raw = self.store.load(self._market_file(symbol), default=[])
        bars = [Bar.from_dict(item) for item in raw]
        bars.sort(key=lambda bar: bar.date)
        if self.current_date is not None:
            bars = [bar for bar in bars if bar.date <= self.current_date]
        return bars[-limit:] if limit else bars

    def latest_bar(self, symbol: str) -> Bar | None:
        bars = self.get_bars(symbol)
        return bars[-1] if bars else None

    def save_bars(self, symbol: str, bars: list[Bar]) -> None:
        data = [bar.to_dict() for bar in sorted(bars, key=lambda bar: bar.date)]
        self.store.save(self._market_file(symbol), data)

    def sync(self, symbol: str, start_date: str, end_date: str) -> int:
        """拉取 Tushare fut_daily 并写入本地 JSON。

        日期参数使用 YYYYMMDD(Tushare 格式),内部统一转成 YYYY-MM-DD 存储。
        """
        if not self.token:
            raise RuntimeError("缺少 TUSHARE_TOKEN,请在 .env 中设置")

        import tushare as ts

        ts.set_token(self.token)
        pro = ts.pro_api()
        frame = pro.fut_daily(ts_code=symbol, start_date=start_date, end_date=end_date)
        if frame is None or frame.empty:
            return 0

        bars: list[Bar] = []
        for row in frame.to_dict("records"):
            trade_date = str(row["trade_date"])
            iso_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
            bars.append(
                Bar(
                    date=iso_date,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    settle=float(row.get("settle", row["close"])),
                    volume=float(row.get("vol", 0.0) or 0.0),
                    open_interest=float(row.get("oi", 0.0) or 0.0),
                )
            )
        self.save_bars(symbol, bars)
        return len(bars)
