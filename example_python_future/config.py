"""加载 example_python_future 的运行配置。

配置分为两类:
1. 合约规格(contract):决定保证金率、乘数、涨跌停等核心规则;
2. 运行参数:初始资金、风控阈值、数据目录等。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trade.models import ContractSpec


PROJECT_ROOT = Path(__file__).resolve().parent


def load_env(path: Path | None = None) -> None:
    """极简 .env 加载:只设置缺失的环境变量,不覆盖已有值。"""
    path = Path(path) if path else PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    symbols: list[str]
    initial_balance: float
    margin_call_threshold: float
    force_close_threshold: float
    data_dir: Path
    contracts: dict[str, ContractSpec]
    model: dict[str, Any]

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        load_env()
        path = Path(path) if path else PROJECT_ROOT / "config.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "contracts" in raw:
            contracts = {
                item["symbol"]: ContractSpec.from_dict(item)
                for item in raw["contracts"]
            }
            symbols = raw.get("symbols") or list(contracts.keys())
        else:
            # 兼容旧的单合约配置。
            contract = ContractSpec.from_dict(raw["contract"])
            contracts = {contract.symbol: contract}
            symbols = [raw.get("symbol", contract.symbol)]
        data_dir = PROJECT_ROOT / "data"
        return cls(
            symbols=symbols,
            initial_balance=float(raw.get("initial_balance", 100000.0)),
            margin_call_threshold=float(raw.get("margin_call_threshold", 0.8)),
            force_close_threshold=float(raw.get("force_close_threshold", 1.0)),
            data_dir=data_dir,
            contracts=contracts,
            model=raw.get("model", raw.get("models", {})),
        )
