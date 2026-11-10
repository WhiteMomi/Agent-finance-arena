"""暴露给 LangChain Agent 的受约束工具。

Agent 只能通过这些工具观察市场并提交交易意图,账户规则由 trade 层硬约束。
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.tools import tool

from data.engine import TushareDataEngine
from trade.broker import Broker
from trade.models import ContractSpec


def build_tools(
    broker: Broker,
    engine: TushareDataEngine,
    specs: dict[str, ContractSpec],
) -> list[Any]:
    def _resolve(symbol: str) -> str | None:
        """把 LLM 可能写错大小写/月份的代码映射到配置中的真实合约。"""
        norm = symbol.strip().upper()
        if norm in specs:
            return norm
        # 去掉交易所后缀后的完整代码,如 rb2610 -> RB2610.SHF。
        if "." not in norm and any(ch.isdigit() for ch in norm):
            starts = [key for key in specs if key.upper().startswith(norm)]
            if starts:
                return starts[0]
        # 只按品种前缀匹配,要求池内该品种唯一,如 cu2609 -> CU2610.SHF。
        match = re.match(r"[A-Z]+", norm)
        if match:
            product = match.group()
            candidates = [key for key in specs if key.upper().startswith(product)]
            if len(candidates) == 1:
                return candidates[0]
        return None

    def _unknown(symbol: str) -> str:
        return json.dumps(
            {"error": f"未知合约 {symbol}", "available": sorted(specs.keys())},
            ensure_ascii=False,
        )

    def _prices() -> dict[str, float]:
        prices: dict[str, float] = {}
        for symbol in specs:
            bar = engine.latest_bar(symbol)
            if bar is not None:
                prices[symbol] = bar.close
        return prices

    @tool
    def get_quote(symbol: str) -> str:
        """获取合约最新行情与涨跌停价。"""
        key = _resolve(symbol)
        if key is None:
            return _unknown(symbol)
        spec = specs[key]
        bar = engine.latest_bar(key)
        if bar is None:
            return json.dumps({"error": f"无 {key} 行情"}, ensure_ascii=False)
        return json.dumps(
            {
                "symbol": key,
                "date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "settle": bar.settle,
                "limit_up": spec.price_limit_up(bar.settle),
                "limit_down": spec.price_limit_down(bar.settle),
            },
            ensure_ascii=False,
        )

    @tool
    def get_kline(symbol: str, limit: int = 20) -> str:
        """获取最近 N 根日线(默认 20)。"""
        key = _resolve(symbol)
        if key is None:
            return _unknown(symbol)
        bars = engine.get_bars(key, limit=limit)
        return json.dumps([bar.to_dict() for bar in bars], ensure_ascii=False)

    @tool
    def get_contract_spec(symbol: str) -> str:
        """获取合约规格:乘数、保证金率、涨跌停、手续费、到期日等。"""
        key = _resolve(symbol)
        if key is None:
            return _unknown(symbol)
        return json.dumps(specs[key].to_dict(), ensure_ascii=False)

    @tool
    def list_contracts() -> str:
        """列出当前可交易的全部合约精确代码。"""
        return json.dumps(sorted(specs.keys()), ensure_ascii=False)

    @tool
    def get_account() -> str:
        """获取账户权益、可用资金与风险度。"""
        prices = _prices()
        account = broker.account
        return json.dumps(
            {
                "balance": account.balance,
                "equity": account.equity(prices, specs),
                "available": account.available(prices, specs),
                "margin_used": account.margin_used(prices, specs),
                "risk_ratio": account.risk_ratio(prices, specs),
                "realized_pnl": account.realized_pnl,
                "fees_paid": account.fees_paid,
            },
            ensure_ascii=False,
        )

    @tool
    def get_positions() -> str:
        """获取持仓与浮动盈亏。"""
        prices = _prices()
        result = []
        for symbol, pos in broker.account.positions.items():
            spec = specs[symbol]
            price = prices.get(symbol, pos.long_avg or pos.short_avg or 0.0)
            floating = (price - pos.long_avg) * spec.multiplier * pos.long_qty
            floating += (pos.short_avg - price) * spec.multiplier * pos.short_qty
            result.append(
                {
                    "symbol": symbol,
                    "long_qty": pos.long_qty,
                    "short_qty": pos.short_qty,
                    "long_avg": pos.long_avg,
                    "short_avg": pos.short_avg,
                    "long_today": pos.long_today,
                    "short_today": pos.short_today,
                    "floating_pnl": floating,
                }
            )
        return json.dumps(result, ensure_ascii=False)

    @tool
    def get_risk_state() -> str:
        """获取距强平线、追保线的状态。"""
        prices = _prices()
        account = broker.account
        risk = broker.risk
        return json.dumps(
            {
                "risk_ratio": account.risk_ratio(prices, specs),
                "margin_call_threshold": risk.margin_call_threshold,
                "force_close_threshold": risk.force_close_threshold,
                "equity": account.equity(prices, specs),
                "margin_used": account.margin_used(prices, specs),
                "available": account.available(prices, specs),
            },
            ensure_ascii=False,
        )

    @tool
    def place_order(
        symbol: str,
        side: str,
        offset: str,
        qty: int,
        price: float = 0.0,
        order_type: str = "LIMIT",
    ) -> str:
        """提交交易意图。side 为 BUY/SELL,offset 为 OPEN/CLOSE/CLOSE_TODAY。"""
        try:
            key = _resolve(symbol)
            if key is None:
                return _unknown(symbol)
            order = broker.place_order(
                symbol=key,
                side=side,
                offset=offset,
                qty=qty,
                price=price if price > 0 else None,
                order_type=order_type,
            )
        except Exception as exc:  # 把未知合约、无行情等错误转成 JSON 返回。
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps(order.to_dict(), ensure_ascii=False)

    return [
        get_quote,
        get_kline,
        get_contract_spec,
        list_contracts,
        get_account,
        get_positions,
        get_risk_state,
        place_order,
    ]
