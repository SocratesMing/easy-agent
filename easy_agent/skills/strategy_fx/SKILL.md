---
name: strategy_fx
description: >
  外汇策略研发skill，根据用户需求生成外汇策略代码(main.py)及策略参数配置文件，
  直接保存到strategy_dir目录，并支持发起回测和参数寻优。
  当用户提到外汇策略开发、生成策略代码、回测、寻优、参数优化、策略存储等关键词时触发。
---

# 外汇策略研发 (strategy_fx)

## 概述

本skill用于根据用户需求自动生成外汇（FX）策略代码及对应的策略参数配置，支持：
- **仅存储**：生成策略代码和参数后保存到strategy_dir
- **存储+回测**：生成策略后自动发起回测并监控进度
- **存储+寻优**：生成策略后自动发起参数寻优
- **存储+回测+寻优**：完整流程

策略直接通过Write工具保存到 `strategy_dir` 目录。
strategy_dir的路径为：`/home/sututu/code/finance-skills/fast_backtest/workspace`

## 反射加载机制（核心约束）

回测框架通过 `importlib.import_module("{策略目录名}.main")` 反射加载策略文件，并直接调用模块级函数。

**框架调用链：**
```
init_main(strategy_path)
  → sys.path.append(strategy_path)
  → AppContext.main = importlib.import_module("{strategy_name}.main")
  → AppContext.main.init(StrategyContext)       # 调用 init(context)
  → main_py.onData(StrategyContext, data)       # 调用 onData(context, data)
  → AppContext.main.onOrder(StrategyContext, order)  # 调用 onOrder(context, order)
  → AppContext.main.onTrade(StrategyContext, trade)  # 调用 onTrade(context, trade)
```

**反射兼容性硬性要求（违反任一条将导致回测失败）：**

1. **`__init__.py` 必须存在**：策略目录必须包含 `__init__.py`（可为空文件），使目录成为可导入的 Python 包
2. **回调函数必须在模块顶层**：`init`/`onData`/`onOrder`/`onTrade` 等必须定义为模块级函数，不能嵌套在类中
3. **函数签名必须严格匹配**：`init(context)` / `onData(context, data)` / `onOrder(context, order)` / `onTrade(context, trade)`
4. **quantapi 导入必须在模块顶层**：`import quantapi.*` 不能放在函数内部，框架在 import main.py 时需要解析这些依赖
5. **策略目录名必须合法**：strategy_name 即为 Python 包名，必须符合标识符规范（字母/下划线开头，不含连字符和空格）
6. **strategy_name 变量必须在模块顶层定义**：与目录名一致

## 工作流程

### 1. 分析用户需求

从用户描述中提取以下信息：
- 策略名称（strategy_name）
- 交易品种（symbol），如 EURUSDSP、USDJPYSP
- 数据渠道（source），如 HDATA_HO、CFETS_LC
- Bar数据频率（bar_frequency），如 1N_BAR_DEPTH、1H_BAR_DEPTH
- 策略逻辑描述（入场条件、出场条件、止损止盈等）
- 是否需要回测，默认不回测
- 是否需要寻优，默认不寻优

### 2. 生成策略名称

**命名规则**：`{指标}_{周期}_{当前时分}`

- 提取用户描述中的核心指标简称（如 EMA_RSI、MACD、BOLL 等）
- 周期简称（如 H1、M15、D1 等）
- 拼接生成时的时间（HHMM格式，如 1435）

示例：`EMA_RSI_H1_1034`

**命名约束**：策略名称必须符合 Python 标识符规范（字母/下划线开头，仅含字母/数字/下划线，不含连字符和空格），因为策略目录名即为 Python 包名，框架通过 `importlib.import_module("{strategy_name}.main")` 加载。

#### 重名检查

在写入文件前，必须先检查 `strategy_dir/{strategy_name}` 目录是否存在：

1. 使用 `LS strategy_dir/` 或 `Glob strategy_dir/{strategy_name}` 工具检查是否存在同名目录
2. 如果存在，在名称末尾追加 `_N` 序号（从1开始递增），直到名称不重复

示例：
- `EMA_RSI_H1_1034` 已存在 → 改为 `EMA_RSI_H1_1034_1`
- `EMA_RSI_H1_1034_1` 仍存在 → 改为 `EMA_RSI_H1_1034_2`

### 3. 生成策略代码

使用 **Write 工具** 直接将策略代码保存到 `strategy_dir/{strategy_name}/` 目录。

策略代码结构：
```
strategy_dir/{strategy_name}/
├── __init__.py          # 必须存在（空文件），使目录成为Python包
├── main.py              # 策略主函数（模块级函数，不可封装为类）
├── config/
│   ├── .env              # 策略标签（类型、标签、描述）
│   ├── custom.json       # 参数属性定义
│   └── plan/
│       └── 默认方案.json  # 具体参数值(包含回测配置和优化参数)
```

#### 生成 __init__.py（必须）

创建空文件，使策略目录成为可导入的 Python 包。这是反射加载的前提条件。

```
# 空文件，使本目录成为Python包
```

#### 生成 main.py

参考 `reference/strategy_template.md` 模板，必须满足反射兼容性要求：
- 所有回调函数定义在**模块顶层**：`init(context)`、`onData(context, data)`、`onOrder(context, order)` 等
- `import quantapi.*` 语句放在模块顶层
- 模块顶层定义 `strategy_name` 变量，与目录名一致
- 辅助函数（open_buy, open_sell, close_all, TradeCount 等）直接内联在 main.py 中
- Param 类从 param.get() 获取策略参数
- 止盈止损通过 open_buy/open_sell 的 tp_price/sl_price 参数传入

#### 生成 config/custom.json

参数属性定义，详见 `reference/config.md`。

注意参数名（ename）必须与 plan/*.json 中的 name 字段和 main.py 中 Param 类的属性名一致。

#### 生成 config/plan/默认方案.json

包含回测配置(config)和自定义参数(custParams)，详见 `reference/config.md`。

#### 生成 config/.env

策略标签文件，格式：
```
type=自营
tags=标签1, 标签2
desc=策略描述
BACKTEST_PLAN_TAG=默认方案
```

### 4. 反射兼容性验证（必须）

生成所有文件后，必须执行以下验证步骤，确保策略可被框架正确加载：

| # | 检查项 | 验证命令 | 预期结果 |
|---|--------|----------|----------|
| 1 | `__init__.py` 存在 | `ls strategy_dir/{name}/__init__.py` | 文件存在 |
| 2 | `main.py` 存在 | `ls strategy_dir/{name}/main.py` | 文件存在 |
| 3 | 回调函数在模块顶层 | `grep "^def init\|^def onData\|^def onOrder" main.py` | 匹配到3个函数 |
| 4 | quantapi 导入在顶层 | `grep "^import quantapi" main.py` | 匹配到导入语句 |
| 5 | strategy_name 变量 | `grep "^strategy_name" main.py` | 值与目录名一致 |
| 6 | 无语法错误 | `python3 -c "import ast; ast.parse(open('main.py').read())"` | 无报错 |
| 7 | 目录名合法 | `python3 -c "str='{name}'; assert str.isidentifier()"` | 无报错 |

如果任何检查项失败，必须立即修复后重新验证。

### 5. 新增策略记录（可选）

调用本地fast_backtest接口将策略添加到策略列表：

```bash
python .agents/skills/strategy_fx/scripts/add_strategy.py \
  --strategy-name {name} \
  --strategy_dir-path strategy_dir \
  --biz-type FX
```

### 6. 发起回测（可选）

```bash
python .agents/skills/strategy_fx/scripts/run_backtest.py \
  --strategy-name {name} \
  --strategy_dir-path strategy_dir \
  --plan-name 默认方案 \
  --user-id {userId}
```

该脚本会：
- 调用回测发起接口 POST `/api/backtest/start`
- 定时轮询 GET `/api/backtest/progress/{runId}` 查询进度
- 当progress达到100%时完成
- 如检测到错误，返回错误信息
- 所有日志（含进度查询日志）统一记录到策略目录的 `skill_log/skill.log`

> **说明**：回测和策略管理调用本地 `fast_backtest` 服务（`localhost:8080`），寻优需外部SBM服务（`localhost:8000`）。

### 7. 发起寻优（可选）

用户需要指定寻优参数范围和寻优次数（默认100次）：

```bash
python .agents/skills/strategy_fx/scripts/run_optimization.py \
  --strategy-name {name} \
  --strategy_dir-path strategy_dir \
  --plan-name 默认方案 \
  --user-id {userId} \
  --opt-params '[{"name":"ma_period","min":5,"max":50,"step":1,"type":"discrete"}]' \
  --opt-count 100
```

## 接口文档

策略开发可用的quantapi接口文档位于 `reference/quantapi/` 目录，包含：

| 模块 | 文件 | 说明 |
|------|------|------|
| base | base.py | 获取合约信息等基础接口 |
| md | md.py | 行情数据接口（get_price, query_bars, query_bars_pro） |
| deal | deal.py | 下单接口（to_order） |
| param | param.py | 策略参数获取接口 |
| pos | pos.py | 持仓信息接口 |
| qlog | qlog.py | 日志接口 |
| signal | signal.py | 信号接口 |
| scheduler | scheduler.py | 定时任务接口 |
| funds | funds.py | 资金账户接口 |
| event | event.py | 事件回调说明 |
| enums.py | enums.py | 枚举常量定义 |
| maker.py | maker.py | 做市接口 |
| object.py | object.py | 市场数据对象说明 |
| date.py | date.py | 日期工具 |

## 配置文件

所有配置放在 `config.yaml` 中。策略的strategy_dir目录固定为 `strategy_dir/`。

## 注意事项

1. **反射兼容性是第一优先级**：生成的 main.py 必须满足所有反射兼容性要求，否则回测框架无法加载策略
2. **必须生成 `__init__.py`**：即使为空文件，缺少此文件将导致 `importlib.import_module` 失败
3. 生成的策略代码自包含在 main.py 中，辅助函数（open_buy, open_sell, close_all, TradeCount 等）直接内联，不依赖外部公共库
4. 策略代码中的 Param 类参数名必须与 custom.json 中的 ename 一致
5. plan/*.json 中的参数值必须与 custom.json 定义的参数一一对应
6. 回测和寻优通过RESTful接口与SBM服务交互
7. 所有脚本操作日志统一记录到策略目录的 `skill_log/skill.log`
8. 如果回测报错，将错误信息返回给大模型进行代码修复
