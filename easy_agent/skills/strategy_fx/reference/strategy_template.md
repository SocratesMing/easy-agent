# 外汇策略 main.py 模板参考

## 基本结构

```python
"""
{策略名称} - 外汇策略
"""
import json
import os
import time

import talib as ta

import quantapi.base as base
import quantapi.md as md
import quantapi.param as param
import quantapi.qlog as qlog
from cqfnlib import *

strategy_name = "{策略名称}"


def init(context):
    """
    init 中初始化必要的参数，订阅数据
    初始化方法 - 在回测和实时模拟交易只会在启动的时候触发一次。
    """
    version_info = "v1_0"
    version_time = time.strftime("%Y-%m-%d ", time.localtime(time.time()))
    qlog.info_f("[init]初始化{} 版本号:{} ", strategy_name, version_time + "_" + version_info)

    context.param = Param()
    context.bar_last_time = None
    context.start_k_num = 50
    context.k_num_flag = False

    ##########  公共参数  ##########
    symbol_info = base.get_contract(context.param.symbol)
    context.digits = symbol_info['noDecimal']
    context.point = eval(f'1e-{context.digits}')
    context.slippage_open = 20 * context.point
    context.slippage_close = 500 * context.point
    context.sl = context.param.sl
    context.tp = context.param.tp
    context.sp = context.param.sp
    context.sl_id = {}
    context.tp_id = {}
    context.openid_closeid = {}
    context.closeid_openid = {}
    context.tc = TradeCount(context)

    ##########  策略参数  ##########
    # 在此根据用户需求初始化策略特定参数

    ##########  其他设置  ##########
    context.data = {}
    context.pwd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.txt")
    context.all_open = True

    qlog.info_f("========== 策略运行参数 ==========")
    qlog.info_f("交易品种: {}", context.param.symbol)
    qlog.info_f("合约渠道: {}", context.param.source)
    qlog.info_f("Bar频率: {}", context.param.bar_frequency)
    qlog.info_f("下单量: {}", context.param.lot)
    qlog.info_f("止盈点数: {}", context.param.tp)
    qlog.info_f("止损点数: {}", context.param.sl)
    qlog.info_f("价差点数: {}", context.param.sp)
    qlog.info_f("Digits: {}, Point: {}", context.digits, context.point)
    qlog.info_f("开仓滑点: {}, 平仓滑点: {}", context.slippage_open, context.slippage_close)
    qlog.info_f("==================================")


class Param:
    """策略方案界面自定义配置参数"""
    def __init__(self):
        self.symbol = param.get('策略合约')
        self.source = param.get('合约渠道')
        self.bar_frequency = param.get('bar数据频率')
        self.lot = param.get('下单金额') if param.get('下单金额') is not None else 1000000
        self.tp = param.get('止盈点数') if param.get('止盈点数') is not None else 1000
        self.sl = param.get('止损点数') if param.get('止损点数') is not None else 300
        self.sp = param.get('价差点数') if param.get('价差点数') is not None else 50
        # 在此添加策略自定义参数


def onData(context, data):
    """根据tick行情触发"""
    # 获取bar数据
    bar = md.query_bars_pro(context.param.symbol, context.param.bar_frequency, context.param.source,
                            count=100, fields=['time', 'close', 'high', 'low', 'open'])

    # 缓存市价
    bid_ask_cache(context, data[0])

    # 先平仓
    close(context)

    # 检查bar时间是否更新
    if not check_bar_time(context, bar):
        return

    ##########  策略指标计算  ##########
    close_arr = bar['close']
    high_arr = bar['high']
    low_arr = bar['low']
    open_arr = bar['open']

    # 在此根据用户需求编写入场/出场逻辑


def onOrder(context, order):
    """根据order状态变化触发事件"""
    qlog.info_f("OnOrder->{}", order)
    context.tc = TradeCount(context)
    if order['id'] in context.closeid_openid.keys():
        if order['orderStatus'] == '8':
            open_id = context.closeid_openid[order['id']]
            del context.openid_closeid[open_id]
            del context.closeid_openid[order['id']]
            qlog.info('OnOrder->平仓单处理完成')
        elif order['orderStatus'] == '7':
            open_id = context.closeid_openid[order['id']]
            del context.closeid_openid[order['id']]
            context.openid_closeid[open_id]['close_id'] = 0
            qlog.info('OnOrder->平仓单未成交已撤销，等待重新挂单')


def onTrade(context, trade):
    """产生成交后触发事件驱动"""
    pass


def onTime(context, time, name):
    """定时任务"""
    pass


def onBusinessDate(context, data):
    """产生切日事件触发事件驱动"""
    pass


def onMonitor(context, data):
    """接口链路启停触发事件驱动"""
    pass
```

## 关键函数说明

### 交易函数（来自 cqfnlib.py）

| 函数 | 说明 |
|------|------|
| `open_buy(context, price, sl_price, tp_price)` | 开多单 |
| `open_sell(context, price, sl_price, tp_price)` | 开空单 |
| `close(context, buy_close, sell_close)` | 平仓 |
| `close_buy(context)` | 平多头 |
| `close_sell(context)` | 平空头 |
| `close_all(context, keep_side)` | 平所有仓位 |
| `bid_ask_cache(context, data_temp)` | 缓存当前市价 |
| `check_spread(context, data_temp)` | 价差检查 |
| `check_bar(context, bar)` | 校验bar数量和时间 |
| `check_bar_time(context, bar)` | 检查bar时间是否更新 |
| `TradeCount(context)` | 交易计数工具类 |

### quantapi接口

| 模块 | 用途 | 主要函数 |
|------|------|----------|
| `base` | 基础信息 | `get_contract(code)` 获取合约信息 |
| `md` | 行情数据 | `query_bars_pro()`, `get_price()` |
| `deal` | 下单 | `to_order()` |
| `param` | 参数 | `get(key)`, `get_num()` |
| `pos` | 持仓 | `get_position()`, `get_ord_position()` |
| `qlog` | 日志 | `info()`, `info_f()`, `error()`, `error_f()` |
| `funds` | 资金 | `get_funds()` |
| `scheduler` | 定时 | `run_daily()`, `run_second()` |

## Param类参数获取规范

参数通过 `param.get('参数中文名')` 获取，参数中文名必须与 `config/custom.json` 中的 `cname` 字段和 `config/plan/*.json` 中的 `key` 字段保持一致。

## 技术指标

使用 `talib` 库计算技术指标：
- `ta.SMA(arr, period)` - 简单移动平均
- `ta.EMA(arr, period)` - 指数移动平均
- `ta.RSI(arr, period)` - 相对强弱指标
- `ta.BBANDS(arr, period, nbdevup, nbdevdn)` - 布林带
- `ta.MACD(arr, fast, slow, signal)` - MACD
- `ta.STOCH(high, low, close, ...)` - 随机指标
- `ta.ATR(high, low, close, period)` - 平均真实波幅