# example_python_future

一个最小可用的期货 Agent 交易核心原型。Agent 拥有一个资金账户,可以在一篮子期货合约
池里自选开平仓。参考了仓库内的
[futures_rules.md](futures_rules.md) 和
[HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader) 的“Agent 只能提交交易意图、撮合与风控由系统硬约束”的设计思路。

> 仅用于原型与模拟,不构成投资建议。期货是高杠杆产品,可能损失超过本金。

所有类、方法、LangChain 工具、命令行参数和 JSON 结构的详细接口见
[INTERFACES.md](INTERFACES.md)。

## 目录结构

```
example_python_future/
├── config.json          # 合约规格与运行参数
├── config.py            # 配置加载
├── main.py              # 命令行入口
├── requirements.txt
├── agent/               # LangChain 工具 + Agent 运行器
├── data/                # Tushare 数据引擎 + 本地 JSON 存储
│   ├── market/          # 日线数据,如 RB2601.SHF.json
│   └── state/           # 账户、持仓、订单
└── trade/               # 交易逻辑:模型、风控、撮合、结算
```

## 环境

使用已有 conda 环境 `agent-finance-arena`:

```powershell
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' -m pip install -r requirements.txt
```

密钥统一放在 `.env` 文件里(已提供模板,不会提交到版本库):

```text
TUSHARE_TOKEN=
OPENAI_API_KEY=
NVIDIA_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
```

## 快速开始(离线)

在 `example_python_future` 目录下:

```powershell
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py seed
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py run
```

`run` 默认使用确定性的双均线规则,在 `config.json` 配置的合约池里自选,不依赖 LLM;
若本地没有日线数据会自动生成演示数据。
查看账户:

```powershell
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py account
```

## 使用真实数据(Tushare)

```powershell
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py sync --start 20260101 --end 20261231
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py run
```

`sync` 会同步 `config.json` 里 `contracts` 的全部合约;也可以用 `--symbol` 只同步某一个。
Tushare 使用 `fut_daily` 日线接口,返回的 `settle` 作为当日结算价,数据会落到
`data/market/{symbol}.json`。

## 使用 LangChain LLM Agent

默认规则策略用于离线验证。接 LLM 时使用 LangChain 的 `create_agent`,并给 Agent
提供只读行情、账户工具和一个受约束的 `place_order` 工具:

```powershell
& 'D:\Miniforge3\envs\agent-finance-arena\python.exe' main.py run --model openai
```

模型名、温度、自定义 base_url、超时、重试等都在 `config.json` 的 `model` 字段里配置;
API key 从 `model.api_key_env` 指定的环境变量读取(例如 `NVIDIA_API_KEY`)。

工具清单见 [agent/tools.py](agent/tools.py):`get_quote`、`get_kline`、
`get_contract_spec`、`get_account`、`get_positions`、`get_risk_state`、
`place_order`。

## 保留的核心期货规则

- 双向持仓,多空分开记录(`long_qty` / `short_qty`);
- 合约乘数、最小变动价位、保证金率与杠杆(杠杆 ≈ 1 / 保证金率);
- 开仓占用保证金,可用资金不足则拒单;
- 涨跌停价、持仓限额、单笔最大下单量;
- 每日盯市结算(Mark-to-Market),持仓盈亏实际划转到账户;
- 平今 / 平昨手续费差异;
- 风险度超限时强制平仓(穿仓场景)。

有意简化的部分:单账户、日线撮合(市价按收盘成交、限价在触及当日区间时成交)、
暂不处理夜盘日历、移仓换月、部分成交与复杂订单类型。
