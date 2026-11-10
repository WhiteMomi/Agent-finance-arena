# example_python_future 接口文档

本文档描述 `example_python_future` 原型对外暴露的接口,包括:

- 配置接口
- 数据层接口(JSON 存储 + Tushare 数据引擎)
- 交易层接口(模型、风控、撮合、Broker、结算)
- Agent 接口(LangChain 工具与运行器)
- 命令行入口
- 本地 JSON 文件结构
- 环境变量

所有接口均为 Python 3.12 面向对象实现,包结构如下:

```text
example_python_future/
├── config.py        # Config
├── data/
│   ├── store.py     # JsonStore
│   └── engine.py    # TushareDataEngine
├── trade/
│   ├── models.py    # 数据类与枚举
│   ├── risk.py      # RiskManager / OrderRejected
│   ├── matching.py  # MatchingEngine
│   ├── broker.py    # Broker
│   └── settlement.py# SettlementEngine
├── agent/
│   ├── tools.py     # build_tools
│   └── runner.py    # RuleBasedAgent / LangChainAgent / build_agent
└── main.py          # 命令行入口
```

## 1. 配置接口

### 1.1 `config.Config`

加载 `config.json` 并生成运行配置。

```python
@dataclass
class Config:
    symbols: list[str]
    initial_balance: float
    margin_call_threshold: float
    force_close_threshold: float
    data_dir: Path
    contracts: dict[str, ContractSpec]
    model: dict[str, Any]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbols` | `list[str]` | 合约池顺序 |
| `initial_balance` | `float` | 初始资金 |
| `margin_call_threshold` | `float` | 追保风险度阈值 |
| `force_close_threshold` | `float` | 强平风险度阈值 |
| `data_dir` | `Path` | 本地 JSON 数据根目录 |
| `contracts` | `dict[str, ContractSpec]` | `{symbol: ContractSpec}` 合约规格表 |
| `model` | `dict[str, Any]` | LLM 配置,含 name、basemodel、openai_base_url、api_key_env 等 |

类方法:

```python
Config.load(path: Path | None = None) -> Config
```

不传 `path` 时读取 `PROJECT_ROOT/config.json`。

常量:

```python
PROJECT_ROOT: Path  # example_python_future 目录
```

### 1.2 `config.json` 结构

```json
{
  "symbols": [
    "IH2601.CFX",
    "RB2601.SHF",
    "CU2601.SHF"
  ],
  "initial_balance": 5000000.0,
  "margin_call_threshold": 0.8,
  "force_close_threshold": 1.0,
  "model": {
    "name": "gpt-oss-20b-advanced",
    "basemodel": "openai/gpt-oss-20b",
    "openai_base_url": "https://integrate.api.nvidia.com/v1",
    "api_key_env": "NVIDIA_API_KEY",
    "temperature": 0.01,
    "timeout_seconds": 90,
    "max_retries": 2,
    "enabled": true,
    "signature": "",
    "json_mode": false
  },
  "contracts": [
    {
      "symbol": "IH2601.CFX",
      "name": "上证50股指2601(演示)",
      "multiplier": 300,
      "tick_size": 0.2,
      "margin_rate": 0.12,
      "limit_pct": 0.10,
      "fee_open": 30.0,
      "fee_close": 30.0,
      "fee_close_today": 90.0,
      "last_trade_date": "2026-12-18",
      "cash_settled": true,
      "position_limit": 20,
      "max_order_qty": 5,
      "demo_start_price": 2600.0
    }
  ]
}
```

## 2. 数据层接口

### 2.1 `data.store.JsonStore`

```python
class JsonStore:
    def __init__(self, base_dir: Path) -> None
    def load(self, rel: str, default: Any = None) -> Any
    def save(self, rel: str, data: Any) -> None
```

| 方法 | 说明 |
|---|---|
| `load(rel, default)` | 读取 `base_dir/rel`,不存在时返回 `default` |
| `save(rel, data)` | 以 UTF-8 原子写入 `base_dir/rel`,自动创建父目录 |

存储使用相对路径,典型路径为 `market/RB2601.SHF.json` 和
`state/account.json`。

### 2.2 `data.engine.TushareDataEngine`

```python
class TushareDataEngine:
    def __init__(self, store: JsonStore, token: str | None = None) -> None
    def set_current_date(self, date: str | None) -> None
    def get_bars(self, symbol: str, limit: int | None = None) -> list[Bar]
    def latest_bar(self, symbol: str) -> Bar | None
    def save_bars(self, symbol: str, bars: list[Bar]) -> None
    def sync(self, symbol: str, start_date: str, end_date: str) -> int
```

| 方法 | 说明 |
|---|---|
| `__init__` | `token` 为空时读取环境变量 `TUSHARE_TOKEN` |
| `set_current_date(date)` | 设置回测可见数据的截止日,传 `None` 清除限制,用于避免未来函数 |
| `get_bars(symbol, limit)` | 按日期升序返回日线,受 `set_current_date` 限制,`limit` 为 `None` 时返回全部 |
| `latest_bar(symbol)` | 返回最后一根可见日线 |
| `save_bars(symbol, bars)` | 将日线写入 `data/market/{symbol}.json` |
| `sync(symbol, start_date, end_date)` | 调用 Tushare `fut_daily`,把 YYYYMMDD 转成 YYYY-MM-DD 后落盘,返回写入根数 |

`sync` 需要 Tushare token,失败会抛出 `RuntimeError`。

## 3. 交易层接口

### 3.1 枚举

```python
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
```

### 3.2 `ContractSpec`

```python
@dataclass
class ContractSpec:
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
```

属性:

```python
tick_value: float  # tick_size * multiplier
```

方法:

```python
def contract_value(self, price: float, qty: int) -> float
def margin_required(self, price: float, qty: int) -> float
def price_limit_up(self, prev_settle: float) -> float
def price_limit_down(self, prev_settle: float) -> float
def round_to_tick(self, price: float) -> float
def to_dict(self) -> dict
@classmethod
def from_dict(cls, data: dict) -> ContractSpec
```

`price_limit_up/down` 基于 `prev_settle * (1 ± limit_pct)` 并按 `tick_size` 取整。

### 3.3 `Bar`

```python
@dataclass
class Bar:
    date: str            # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    settle: float        # 同时作为当日结算价
    volume: float = 0.0
    open_interest: float = 0.0
```

方法:

```python
def to_dict(self) -> dict
@classmethod
def from_dict(cls, data: dict) -> Bar
```

### 3.4 `Position`

```python
@dataclass
class Position:
    symbol: str
    long_qty: int = 0
    short_qty: int = 0
    long_avg: float = 0.0
    short_avg: float = 0.0
    long_today: int = 0
    short_today: int = 0
    prev_settle: float = 0.0
```

属性与方法:

```python
net_qty: int  # long_qty - short_qty
def to_dict(self) -> dict
@classmethod
def from_dict(cls, data: dict) -> Position
```

`long_avg` / `short_avg` 用于平仓盈亏,`prev_settle` 用于当日盯市基准价。

### 3.5 `Order`

```python
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
```

方法:

```python
def to_dict(self) -> dict
```

### 3.6 `Trade`

```python
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
```

方法:

```python
def to_dict(self) -> dict
```

### 3.7 `Account`

```python
@dataclass
class Account:
    balance: float
    initial_balance: float
    positions: dict[str, Position]
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    frozen: float = 0.0
```

方法:

```python
def position(self, symbol: str) -> Position
def floating_pnl(self, prices: dict, specs: dict) -> float
def equity(self, prices: dict, specs: dict) -> float
def margin_used(self, prices: dict, specs: dict) -> float
def available(self, prices: dict, specs: dict) -> float
def risk_ratio(self, prices: dict, specs: dict) -> float
def to_dict(self) -> dict
@classmethod
def from_dict(cls, data: dict) -> Account
```

其中 `prices` 为 `{symbol: price}`,`specs` 为 `{symbol: ContractSpec}`。

关键口径:

```text
浮动盈亏 = sum((price - long_avg) * multiplier * long_qty)
        + sum((short_avg - price) * multiplier * short_qty)
动态权益 = balance + 浮动盈亏
占用保证金 = sum(price * multiplier * margin_rate * (long_qty + short_qty))
可用资金 = 动态权益 - 占用保证金 - frozen
风险度 = 占用保证金 / 动态权益,权益 <= 0 时为 inf
```

### 3.8 `trade.risk.RiskManager`

```python
class RiskManager:
    def __init__(
        self,
        margin_call_threshold: float = 0.8,
        force_close_threshold: float = 1.0,
    ) -> None

    def validate(
        self,
        order: Order,
        account: Account,
        spec: ContractSpec,
        ref_price: float,
        prev_settle: float,
        current_date: str,
    ) -> None
```

`validate` 不通过时抛出:

```python
class OrderRejected(Exception)
```

检查项:

1. 手数是否为正整数;
2. 手数是否超过 `max_order_qty`;
3. 合约是否超过 `last_trade_date`;
4. `ref_price` 是否在涨跌停范围内;
5. `ref_price` 是否为最小变动价位的整数倍;
6. 开仓时是否超过持仓限额;
7. 开仓时可用资金是否覆盖保证金和手续费;
8. 平仓时是否有足够可平仓位。

### 3.9 `trade.matching.MatchingEngine`

```python
class MatchingEngine:
    def __init__(self, slippage_rate: float = 0.0) -> None
    def match(self, order: Order, bar: Bar) -> tuple[float | None, str]
```

返回 `(成交价, 消息)`;未成交时成交价为 `None`。

撮合规则:

- 市价单按 `bar.close` 成交,`slippage_rate` 沿不利方向滑点;
- 限价买单在 `bar.low <= order.price` 时按限价成交;
- 限价卖单在 `bar.high >= order.price` 时按限价成交;
- 否则返回 `None` 和未成交原因。

### 3.10 `trade.broker.Broker`

```python
class Broker:
    def __init__(
        self,
        account: Account,
        specs: dict[str, ContractSpec],
        store: Any | None = None,
        matching: MatchingEngine | None = None,
        risk: RiskManager | None = None,
    ) -> None

    def begin_day(self, bars: dict[str, Bar], prev_settles: dict[str, float]) -> None
    def place_order(
        self,
        symbol: str,
        side: str,
        offset: str,
        qty: int,
        price: float | None = None,
        order_type: str = "LIMIT",
    ) -> Order
    def save_state(self) -> None
```

属性:

```python
account: Account
specs: dict[str, ContractSpec]
store: Any | None
matching: MatchingEngine
risk: RiskManager
current_bars: dict[str, Bar]
prev_settles: dict[str, float]
orders: list[Order]
trades: list[Trade]
```

`place_order` 流程:

1. 校验 `symbol` 与 `side`、`offset`、`order_type`;
2. 经 `RiskManager.validate` 做报单前风控;
3. 经 `MatchingEngine.match` 撮合;
4. 开仓更新平均价、今仓数量、盯市基准价,并扣手续费;
5. 平仓按平昨优先分配手续费,计算平仓盈亏,更新账户;
6. 调用 `store.save` 持久化账户和订单。

平仓参数含义:

- `side=SELL, offset=CLOSE/CLOSE_TODAY` 平多;
- `side=BUY, offset=CLOSE/CLOSE_TODAY` 平空;
- `CLOSE` 优先平昨仓,不足再平今仓;
- `CLOSE_TODAY` 只平今仓。

### 3.11 `trade.settlement.SettlementEngine`

```python
class SettlementEngine:
    def __init__(
        self,
        specs: dict[str, ContractSpec],
        margin_call_threshold: float = 0.8,
        force_close_threshold: float = 1.0,
    ) -> None

    def settle(self, account: Account, bars: dict[str, Bar]) -> None
```

`settle` 依次执行:

1. 按各合约 `bar.settle` 与 `Position.prev_settle` 计算当日盯市盈亏并划转到 `balance`;
2. 将各合约持仓成本价与基准价重置为今日结算价;
3. 将今仓转为昨仓;
4. 若风险度达到强平阈值或权益小于等于 0,清空全部持仓并扣平仓手续费。

`bars` 为 `{symbol: Bar}`,只有当日有行情的合约才会参与结算。

## 4. Agent 接口

### 4.1 `agent.tools.build_tools`

```python
def build_tools(
    broker: Broker,
    engine: TushareDataEngine,
    specs: dict[str, ContractSpec],
) -> list[Any]
```

返回 7 个 LangChain `@tool` 工具,均返回 JSON 字符串:

| 工具名 | 参数 | 说明 |
|---|---|---|
| `get_quote` | `symbol: str` | 最新行情与涨跌停价 |
| `get_kline` | `symbol: str`, `limit: int = 20` | 最近 N 根日线 |
| `get_contract_spec` | `symbol: str` | 合约规格 |
| `get_account` | 无 | 权益、可用资金、风险度 |
| `get_positions` | 无 | 持仓与浮动盈亏 |
| `get_risk_state` | 无 | 距追保线、强平线的状态 |
| `place_order` | `symbol, side, offset, qty, price=0.0, order_type="LIMIT"` | 提交交易意图 |

`place_order` 参数约束:

- `side` 为 `BUY` 或 `SELL`;
- `offset` 为 `OPEN`、`CLOSE` 或 `CLOSE_TODAY`;
- `order_type` 为 `LIMIT` 或 `MARKET`;
- `price <= 0` 时按当前收盘价作为限价。

工具被拒绝时,`place_order` 返回 `status=REJECTED` 和 `message`,不会改变账户持仓。

### 4.2 `agent.runner.RuleBasedAgent`

```python
class RuleBasedAgent:
    def __init__(
        self,
        broker: Broker,
        engine: TushareDataEngine,
        specs: dict[str, ContractSpec],
    ) -> None

    def step(self, symbols: list[str]) -> str
```

离线确定性策略:

- 对池内每个合约,收盘价短均线(3)上穿长均线(8)时买开;
- 短均线下穿长均线时卖开;
- 反向信号时平仓;
- 账户可用资金不足时该合约跳过;
- 不足 8 根日线的合约返回“数据不足”。

### 4.3 `agent.runner.LangChainAgent`

```python
class LangChainAgent:
    def __init__(self, model: Any, tools: list[Any]) -> None
    def step(self, context: str) -> str
```

内部使用 `langchain.agents.create_agent`,需要模型支持 `bind_tools`。

系统提示词常量:

```python
SYSTEM_PROMPT: str
```

### 4.4 `agent.runner.build_agent`

```python
def build_agent(
    mode: str,
    broker: Broker,
    engine: TushareDataEngine,
    specs: dict[str, ContractSpec],
    tools: list[Any],
) -> Any
```

- `mode == "rule"`:返回 `RuleBasedAgent`;
- `mode == "openai"`:读取 `OPENAI_API_KEY`,用 `langchain_openai.ChatOpenAI`
  创建 `LangChainAgent`,未设置 key 时抛出 `RuntimeError`。

## 5. 命令行入口

```text
python main.py [command] [options]
```

子命令:

| 命令 | 说明 |
|---|---|
| `seed` | 生成演示日线并重置账户 |
| `sync` | 用 Tushare 拉取日线到本地 JSON |
| `run` | 回放日线并运行 Agent |
| `account` | 打印当前账户与持仓 |

省略 `command` 时默认为 `run`。

选项:

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--symbol` | `None` | 只运行/同步单个合约;省略则使用 `config.json` 的 `symbols` 池 |
| `--start` | `20260101` | Tushare 同步开始日期 YYYYMMDD |
| `--end` | `20261231` | Tushare 同步结束日期 YYYYMMDD |
| `--days` | `0` | `run` 回放的最多天数,`0` 表示全部 |
| `--model` | `rule` | `rule` 或 `openai` |

示例:

```powershell
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py seed
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py run --model rule
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py account
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py sync --start 20260101 --end 20261231
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py run --symbol RB2601.SHF
```

## 6. 本地 JSON 文件结构

### 6.1 日线 `data/market/{symbol}.json`

`Bar` 数组:

```json
[
  {
    "date": "2026-09-01",
    "open": 3500.0,
    "high": 3510.0,
    "low": 3492.0,
    "close": 3510.0,
    "settle": 3510.0,
    "volume": 1200000.0,
    "open_interest": 900000.0
  }
]
```

### 6.2 账户 `data/state/account.json`

```json
{
  "balance": 100000.0,
  "initial_balance": 100000.0,
  "realized_pnl": 0.0,
  "fees_paid": 0.0,
  "frozen": 0.0,
  "positions": {
    "RB2601.SHF": {
      "symbol": "RB2601.SHF",
      "long_qty": 0,
      "short_qty": 0,
      "long_avg": 0.0,
      "short_avg": 0.0,
      "long_today": 0,
      "short_today": 0,
      "prev_settle": 3500.0
    }
  }
}
```

### 6.3 订单与成交 `data/state/orders.json`

```json
{
  "orders": [
    {
      "id": "O000001",
      "symbol": "RB2601.SHF",
      "side": "BUY",
      "offset": "OPEN",
      "order_type": "LIMIT",
      "price": 3510.0,
      "qty": 5,
      "status": "FILLED",
      "message": "限价成交",
      "filled_price": 3510.0,
      "date": "2026-09-01"
    }
  ],
  "trades": [
    {
      "id": "T000001",
      "order_id": "O000001",
      "symbol": "RB2601.SHF",
      "side": "BUY",
      "offset": "OPEN",
      "price": 3510.0,
      "qty": 5,
      "fee": 20.0,
      "pnl": 0.0,
      "date": "2026-09-01"
    }
  ]
}
```

## 7. 环境变量

| 变量 | 说明 |
|---|---|
| `TUSHARE_TOKEN` | Tushare `fut_daily` 接口 token |
| `OPENAI_API_KEY` | OpenAI 兼容接口的 API key |
| `NVIDIA_API_KEY` | NVIDIA NIM 等 OpenAI 兼容接口的 API key(由 `model.api_key_env` 指定) |
| `OPENAI_MODEL` | 使用的模型名,默认 `gpt-4o-mini` |
| `OPENAI_BASE_URL` | OpenAI 兼容接口的 base_url,可选 |

以上变量支持从 `example_python_future/.env` 读取;`.env` 中已有值不会被覆盖。
模型名、温度、base_url、超时、重试在 `config.json` 的 `model` 字段配置;API key 从
`model.api_key_env` 指定的环境变量读取。`basemodel` 是实际传给 LLM 的模型 ID,
`name` 仅作为本地显示名。

## 8. 简化边界

当前原型为单账户、多合约池、日线撮合:

- 市价单按收盘价成交,限价单在当日区间触及时成交;
- 不处理夜盘交易日历;
- 不处理移仓换月、部分成交和 OCO/Stop 等复杂订单;
- 强平简化为风险度达到阈值后按结算价清空全部持仓。
