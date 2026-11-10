"""Agent 运行器。

- 有 LLM(OpenAI 兼容)时使用 LangChain create_agent;
- 无 LLM 时回退到确定性的双均线规则,保证离线可运行。
"""

from __future__ import annotations

import math
import time
from typing import Any

from langchain_core.messages import HumanMessage

from data.engine import TushareDataEngine
from trade.broker import Broker
from trade.models import ContractSpec, Side


SYSTEM_PROMPT = """你是一个实盘期货交易员,管理一个资金账户和一组可交易期货合约。
你的任务是做出可执行的下单决策,而不是写研报、给建议或聊天。

硬性要求:
- 每个交易日必须做出明确动作:开仓、平仓、加仓、减仓或持有;
- 只要出现交易机会,必须调用 place_order 实际下单,禁止只说“建议买入/可以关注”;
- 如果决定持有,必须说明持有理由和当前风险状态;
- 禁止输出与交易无关的空话、重复寒暄或只复述行情。

决策流程:
1. 先调用 get_kline、get_contract_spec、get_account、get_positions、get_risk_state 获取状态;
2. 比较不同合约的趋势、波动和保证金占用;
3. 选择 0 到多个合约执行,用 place_order 提交开平仓;
4. 最后用一句话总结当天做了什么以及为什么。

交易规则(由系统硬约束,违反会被拒单):
- side 用 BUY/SELL,offset 用 OPEN/CLOSE/CLOSE_TODAY;
- 做多=BUY+OPEN,平多=SELL+CLOSE,做空=SELL+OPEN,平空=BUY+CLOSE;
- 只能交易上下文中列出的合约代码,禁止自己编造月份或代码,不确定时先调用 get_contract_spec;
- 可用资金不足、超出涨跌停、手数非法都会直接拒绝,不要反复提交同一笔无效订单;
- 控制杠杆,不要把全部可用资金压在一个合约上。"""


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class RuleBasedAgent:
    """确定性回退:收盘价短均线上穿/下穿长均线时开多/开空。"""

    def __init__(self, broker: Broker, engine: TushareDataEngine, specs: dict[str, ContractSpec]) -> None:
        self.broker = broker
        self.engine = engine
        self.specs = specs

    def step(self, symbols: list[str]) -> str:
        actions: list[str] = []
        prices = {sym: bar.close for sym, bar in self.broker.current_bars.items()}
        for symbol in symbols:
            bars = self.engine.get_bars(symbol, limit=20)
            if len(bars) < 8:
                actions.append(f"{symbol}: 数据不足")
                continue

            closes = [bar.close for bar in bars]
            short_ma = _mean(closes[-3:])
            long_ma = _mean(closes[-8:])
            spec = self.specs[symbol]
            pos = self.broker.account.position(symbol)
            last_close = closes[-1]

            if short_ma > long_ma and pos.long_qty == 0:
                qty = self._target_qty(spec, last_close, prices)
                if qty > 0:
                    self.broker.place_order(symbol, Side.BUY.value, "OPEN", qty, price=last_close)
                    actions.append(f"{symbol}: 金叉买开 {qty}")
            elif short_ma < long_ma and pos.long_qty > 0:
                qty = pos.long_qty
                self.broker.place_order(symbol, Side.SELL.value, "CLOSE", qty, price=last_close)
                actions.append(f"{symbol}: 死叉平多 {qty}")
            elif short_ma < long_ma and pos.short_qty == 0:
                qty = self._target_qty(spec, last_close, prices)
                if qty > 0:
                    self.broker.place_order(symbol, Side.SELL.value, "OPEN", qty, price=last_close)
                    actions.append(f"{symbol}: 死叉卖开 {qty}")
            elif short_ma > long_ma and pos.short_qty > 0:
                qty = pos.short_qty
                self.broker.place_order(symbol, Side.BUY.value, "CLOSE", qty, price=last_close)
                actions.append(f"{symbol}: 金叉平空 {qty}")
            else:
                actions.append(f"{symbol}: 无信号")
        return "; ".join(actions)

    def _target_qty(
        self, spec: ContractSpec, price: float, prices: dict[str, float]
    ) -> int:
        available = self.broker.account.available(prices, self.specs)
        need = spec.margin_required(price, 1) + spec.fee_open
        if need <= 0:
            return 0
        qty = min(
            int(available // need),
            spec.max_order_qty,
            max(1, int(spec.position_limit * 0.1)),
        )
        return max(1, min(qty, 5)) if available >= need else 0


class LangChainAgent:
    def __init__(
        self,
        model: Any,
        tools: list[Any],
        symbols: list[str] | None = None,
    ) -> None:
        from langchain.agents import create_agent

        prompt = SYSTEM_PROMPT
        if symbols:
            prompt += (
                "\n\n可交易合约的精确代码(只能使用这些代码,禁止编造月份):\n"
                + ", ".join(symbols)
            )
        self.agent = create_agent(model, tools, system_prompt=prompt)

    def step(self, context: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                result = self.agent.invoke(
                    {"messages": [HumanMessage(content=context)]},
                    config={"recursion_limit": 20},
                )
                messages = result.get("messages", [])
                if messages:
                    return str(messages[-1].content)
                return ""
            except Exception as exc:  # 网关 503 等瞬时错误。
                last_error = exc
                print(f"LLM 调用失败(第 {attempt}/3 次): {exc}", flush=True)
                time.sleep(2 * attempt)
        raise last_error if last_error else RuntimeError("LLM 调用失败")


def build_agent(
    mode: str,
    broker: Broker,
    engine: TushareDataEngine,
    specs: dict[str, ContractSpec],
    tools: list[Any],
    model_config: dict[str, Any] | None = None,
    symbols: list[str] | None = None,
) -> Any:
    if mode == "openai":
        import os

        model_config = model_config or {}
        if model_config.get("enabled", True) is False:
            raise RuntimeError("模型配置 enabled=false,请先在 config.json 中启用")

        api_key_env = model_config.get("api_key_env") or "OPENAI_API_KEY"
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(f"使用 --model openai 需要设置 {api_key_env}")

        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {}
        base_url = model_config.get("openai_base_url") or os.getenv("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        timeout_seconds = model_config.get("timeout_seconds")
        if timeout_seconds:
            kwargs["timeout"] = float(timeout_seconds)
        max_retries = model_config.get("max_retries")
        if max_retries is not None:
            kwargs["max_retries"] = int(max_retries)

        model_name = (
            model_config.get("basemodel")
            or model_config.get("name")
            or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        )
        model = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            temperature=float(model_config.get("temperature", 0.0)),
            **kwargs,
        )
        return LangChainAgent(model, tools, symbols=symbols)
    return RuleBasedAgent(broker, engine, specs)
