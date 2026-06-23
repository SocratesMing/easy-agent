# 外汇策略 main.py 模板

## 反射加载机制

回测框架通过 `importlib.import_module("{策略目录名}.main")` 反射加载策略文件，并直接调用模块级函数：
- `AppContext.main.init(StrategyContext)` → 调用 `init(context)`
- `main_py.onData(StrategyContext, data)` → 调用 `onData(context, data)`
- `AppContext.main.onOrder(StrategyContext, order)` → 调用 `onOrder(context, order)`
- `AppContext.main.onTrade(StrategyContext, trade)` → 调用 `onTrade(context, trade)`

**因此 main.py 必须满足以下反射兼容性要求：**
1. 所有回调函数必须定义在**模块顶层**（不能嵌套在类中）
2. 函数签名必须严格匹配：`init(context)`、`onData(context, data)`、`onOrder(context, order)` 等
3. 策略目录必须包含 `__init__.py`（可以为空文件），使目录成为可导入的 Python 包
4. `import quantapi.*` 语句必须放在模块顶层，框架在导入 main.py 时需要解析这些依赖
5. 策略目录名（strategy_name）即为 Python 包名，必须符合 Python 标识符命名规范（字母/下划线开头，不含连字符和空格）

## 策略文件结构

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

## main.py 完整模板

```python
"""
{策略名称} - 外汇策略

反射兼容性说明:
    本文件所有回调函数定义在模块顶层，供回测框架通过 importlib 反射调用。
    框架调用链: importlib.import_module("{strategy_name}.main") → AppContext.main.init/onData/onOrder/...
"""
import time

import talib as ta

import quantapi.base as base
import quantapi.deal as deal
import quantapi.md as md
import quantapi.param as param
import quantapi.pos as pos
import quantapi.qlog as qlog

strategy_name = "{策略名称}"


# ══════════════════════════════════════════════════════════════════════
#  策略参数定义
# ══════════════════════════════════════════════════════════════════════

class Param:
    """策略参数(与 config/custom.json 和 config/plan/*.json 一一对应)。

    参数获取规则:
        - param.get('参数中文名') 获取参数值
        - 参数中文名必须与 custom.json 中的 cname 字段一致
        - 参数属性名(如 self.symbol)必须与 plan/*.json 中的 name 字段一致
    """

    def __init__(self):
        # ── 公共参数(每个策略必须有) ────────────────────────────────
        self.symbol = param.get('策略合约')           # 合约代码，如 "EURUSDSP"
        self.source = param.get('合约渠道')           # 数据渠道，如 "HDATA_HO"
        self.bar_frequency = param.get('bar数据频率')  # Bar频率，如 "1N_BAR_DEPTH"
        self.lot = param.get('下单金额') if param.get('下单金额') is not None else 1000000
        self.tp = param.get('止盈点数') if param.get('止盈点数') is not None else 1000
        self.sl = param.get('止损点数') if param.get('止损点数') is not None else 300
        self.sp = param.get('价差点数') if param.get('价差点数') is not None else 50
        # ── 策略自定义参数(根据策略逻辑添加) ──────────────────────
        # self.ema_fast = param.get('快线EMA周期')
        # self.ema_slow = param.get('慢线EMA周期')
        # self.rsi_period = param.get('RSI周期')


# ══════════════════════════════════════════════════════════════════════
#  持仓统计工具
# ══════════════════════════════════════════════════════════════════════

class TradeCount:
    """统计当前持仓信息(多空单数量、手数、盈亏、均价等)。

    在 init 中初始化: context.tc = TradeCount(context)
    在 onOrder 中刷新: context.tc = TradeCount(context)

    Attributes:
        buy_num(int): 多单数量
        sell_num(int): 空单数量
        buy_lots_all(float): 多单总手数
        sell_lots_all(float): 空单总手数
        buy_prof(float): 多单盈亏
        sell_prof(float): 空单盈亏
        buy_avg(float): 多单盈亏平衡点
        sell_avg(float): 空单盈亏平衡点
        buy_price(float): 多单开单价
        sell_price(float): 空单开单价
        buy_id(int): 多单ID
        sell_id(int): 空单ID
    """

    def __init__(self, context):
        self.buy_num = 0
        self.sell_num = 0
        self.buy_lots_all = 0
        self.sell_lots_all = 0
        self.buy_lots_one = 0
        self.sell_lots_one = 0
        self.buy_prof = 0
        self.sell_prof = 0
        self.buy_avg = 0
        self.sell_avg = 0
        self.buy_price = 0
        self.sell_price = 0
        self.buy_id = 0
        self.sell_id = 0

        pos_data = pos.get_ord_position()
        if not pos_data:
            return
        for pos_temp in pos_data:
            open_id = pos_temp['id']
            quantity = pos_temp['quantity'] - pos_temp['frozenQuantity']
            if quantity <= 0:
                continue
            deal_price = pos_temp['costPrice']
            if pos_temp['posSide'] == 1:  # 多头
                self.buy_num += 1
                self.buy_lots_all += quantity
                self.buy_lots_one += quantity
                self.buy_price = deal_price
                self.buy_id = open_id
                self.buy_prof += pos_temp['profit']
                # 自动补全未设置的止盈止损价
                if context.sl_id.get(open_id) is None and context.sl is not None:
                    context.sl_id[open_id] = round(deal_price - context.sl * context.point, context.digits)
                if context.tp_id.get(open_id) is None and context.tp is not None:
                    context.tp_id[open_id] = round(deal_price + context.tp * context.point, context.digits)
            elif pos_temp['posSide'] == 2:  # 空头
                self.sell_num += 1
                self.sell_lots_all += quantity
                self.sell_lots_one += quantity
                self.sell_price = deal_price
                self.sell_id = open_id
                self.sell_prof += pos_temp['profit']
                if context.sl_id.get(open_id) is None and context.sl is not None:
                    context.sl_id[open_id] = round(deal_price + context.sl * context.point, context.digits)
                if context.tp_id.get(open_id) is None and context.tp is not None:
                    context.tp_id[open_id] = round(deal_price - context.tp * context.point, context.digits)

        if self.buy_lots_all > 0:
            self.buy_avg = round(context.bid - (self.buy_prof / self.buy_lots_all) * context.point, context.digits)
        if self.sell_lots_all > 0:
            self.sell_avg = round(context.ask + (self.sell_prof / self.sell_lots_all) * context.point, context.digits)


# ══════════════════════════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════════════════════════

def bid_ask_cache(context, data_temp):
    """缓存当前市价到 context。"""
    context.bid = data_temp['best_bid']
    context.ask = data_temp['best_ask']
    context.nowtime = data_temp['time'] // 1000


def check_bar_time(context, bar):
    """检查 bar 时间是否更新，更新则返回 True。"""
    new_time = bar['time'][-2]
    if context.bar_last_time == new_time:
        return False
    context.bar_last_time = new_time
    return True


def check_spread(context, data_temp):
    """价差检查，超过阈值返回 False。同时缓存市价。"""
    bid_ask_cache(context, data_temp)
    if abs(context.ask - context.bid) > context.param.sp * context.point:
        qlog.info("价差过高，暂停交易")
        context.all_open = False
        return False
    context.all_open = True
    return True


def open_buy(context, price=None, sl_price=None, tp_price=None):
    """开多单。

    Args:
        context: 策略上下文
        price: 挂单价(默认当前bid价)
        sl_price: 止损价(缓存到 context.sl_id)
        tp_price: 止盈价(缓存到 context.tp_id)
    """
    open_price = context.bid if price is None else price
    openid = deal.to_order(context.param.symbol, 'B', open_price, context.param.lot, 1,
                           channel_code=context.param.source, pos_type=1)
    context.sl_id[openid] = sl_price
    context.tp_id[openid] = tp_price
    qlog.info_f("open_buy->订单号:{}, 价格:{}, 量:{}, 止盈:{}, 止损:{}",
                openid, open_price, context.param.lot, tp_price, sl_price)


def open_sell(context, price=None, sl_price=None, tp_price=None):
    """开空单。

    Args:
        context: 策略上下文
        price: 挂单价(默认当前ask价)
        sl_price: 止损价(缓存到 context.sl_id)
        tp_price: 止盈价(缓存到 context.tp_id)
    """
    open_price = context.ask if price is None else price
    openid = deal.to_order(context.param.symbol, 'S', open_price, context.param.lot, 1,
                           channel_code=context.param.source, pos_type=1)
    context.sl_id[openid] = sl_price
    context.tp_id[openid] = tp_price
    qlog.info_f("open_sell->订单号:{}, 价格:{}, 量:{}, 止盈:{}, 止损:{}",
                openid, open_price, context.param.lot, tp_price, sl_price)


def close_all(context, keep_side=None):
    """平所有仓位。

    Args:
        context: 策略上下文
        keep_side: 保留方向 'B'-保留多单 'S'-保留空单
    """
    pos_data = pos.get_ord_position(symbol=context.param.symbol)
    for p in pos_data:
        open_id = p['id']
        if open_id in context.openid_closeid and context.openid_closeid[open_id]['close_id'] != 0:
            continue
        if p['posSide'] == 1:
            side, close_side = 'B', 'S'
            close_price = round(context.bid - context.slippage_close, context.digits)
        elif p['posSide'] == 2:
            side, close_side = 'S', 'B'
            close_price = round(context.ask + context.slippage_close, context.digits)
        else:
            continue
        if keep_side == side:
            continue
        quantity = p['quantity'] - p['frozenQuantity']
        if quantity > 0:
            close_id = deal.to_order(context.param.symbol, close_side, close_price, quantity, 2,
                                     close_order_id=open_id, channel_code=context.param.source,
                                     pos_type=1, time_in_force=5)
            context.openid_closeid[open_id] = {'close_id': close_id, 'close_side': close_side}
            context.closeid_openid[close_id] = open_id
            qlog.info_f("close_all->方向:{}, 价格:{}, 量:{}, 开仓ID:{}, 平仓ID:{}",
                        close_side, close_price, quantity, open_id, close_id)


def close_buy(context):
    """多头止盈止损检查，触发时调用 close_all。"""
    pos_data = pos.get_ord_position(symbol=context.param.symbol)
    for p in pos_data:
        open_id = p['id']
        if open_id in context.openid_closeid and context.openid_closeid[open_id]['close_id'] != 0:
            continue
        if p['posSide'] == 2:
            continue
        sl = context.sl_id.get(open_id)
        tp = context.tp_id.get(open_id)
        if sl is not None and context.bid <= sl:
            qlog.info_f("多头止损触发, open_id:{}, sl:{}", open_id, sl)
            close_all(context, keep_side='S')
        if tp is not None and context.bid >= tp:
            qlog.info_f("多头止盈触发, open_id:{}, tp:{}", open_id, tp)
            close_all(context, keep_side='S')


def close_sell(context):
    """空头止盈止损检查，触发时调用 close_all。"""
    pos_data = pos.get_ord_position(symbol=context.param.symbol)
    for p in pos_data:
        open_id = p['id']
        if open_id in context.openid_closeid and context.openid_closeid[open_id]['close_id'] != 0:
            continue
        if p['posSide'] == 1:
            continue
        sl = context.sl_id.get(open_id)
        tp = context.tp_id.get(open_id)
        if sl is not None and context.ask >= sl:
            qlog.info_f("空头止损触发, open_id:{}, sl:{}", open_id, sl)
            close_all(context, keep_side='B')
            return
        if tp is not None and context.ask <= tp:
            qlog.info_f("空头止盈触发, open_id:{}, tp:{}", open_id, tp)
            close_all(context, keep_side='B')
            return


def protect_order(context):
    """推平保护: 盈利超过阈值时将止损价移至成本价附近。

    需在 context 中初始化:
        context.protect_point(int): 触发推平保护的盈利点数
        context.protect_move_point(int): 触发后移动到成本价附近的点差值
    """
    pos_data = pos.get_ord_position()
    for p in pos_data:
        open_id = p['id']
        if open_id in context.openid_closeid and context.openid_closeid[open_id]['close_id'] != 0:
            continue
        sl_price_old = context.sl_id.get(open_id)
        deal_price = p['costPrice']
        if p['posSide'] == 1:
            if (sl_price_old is None or sl_price_old < deal_price) \
                    and context.bid - deal_price > context.protect_point * context.point:
                sl_price_new = deal_price + context.protect_move_point * context.point
                qlog.info_f("[protect_order]推平保护, id:{}, 成本:{}, 原止损:{}, 新止损:{}",
                            open_id, deal_price, sl_price_old, sl_price_new)
                context.sl_id[open_id] = sl_price_new
        if p['posSide'] == 2:
            if (sl_price_old is None or sl_price_old > deal_price) \
                    and deal_price - context.ask > context.protect_point * context.point:
                sl_price_new = deal_price - context.protect_move_point * context.point
                qlog.info_f("[protect_order]推平保护, id:{}, 成本:{}, 原止损:{}, 新止损:{}",
                            open_id, deal_price, sl_price_old, sl_price_new)
                context.sl_id[open_id] = sl_price_new


def moving_sl(context):
    """移动止损: 盈利超过阈值后止损价跟随市价移动。

    需在 context 中初始化:
        context.start_move_sl(int): 开始移动止损的点位
        context.move_point_sl(int): 移动止损的点位
    """
    pos_data = pos.get_ord_position(symbol=context.param.symbol)
    for p in pos_data:
        open_id = p['id']
        deal_price = p['costPrice']
        sl_price_old = context.sl_id.get(open_id)
        if open_id in context.openid_closeid and context.openid_closeid[open_id]['close_id'] != 0:
            continue
        if p['posSide'] == 1:
            if context.bid >= deal_price + context.start_move_sl * context.point:
                if sl_price_old is None or context.bid - sl_price_old > context.move_point_sl * context.point:
                    sl_price_new = context.bid - context.move_point_sl * context.point
                    qlog.info_f("[moving_sl]移动止损, id:{}, 成本:{}, 原止损:{}, 新止损:{}",
                                open_id, deal_price, sl_price_old, sl_price_new)
                    context.sl_id[open_id] = sl_price_new
        if p['posSide'] == 2:
            if context.ask <= deal_price - context.start_move_sl * context.point:
                if sl_price_old is None or sl_price_old - context.ask > context.move_point_sl * context.point:
                    sl_price_new = context.ask + context.move_point_sl * context.point
                    qlog.info_f("[moving_sl]移动止损, id:{}, 成本:{}, 原止损:{}, 新止损:{}",
                                open_id, deal_price, sl_price_old, sl_price_new)
                    context.sl_id[open_id] = sl_price_new


def sl_tool(context):
    """止损工具: 执行推平保护 + 移动止损(需在init中启用对应开关)。"""
    if context.protect_flag:
        protect_order(context)
    if context.move_flag:
        moving_sl(context)


# ══════════════════════════════════════════════════════════════════════
#  生命周期回调 (模块级函数 — 供框架反射调用)
# ══════════════════════════════════════════════════════════════════════

def init(context):
    """策略初始化。在回测和实时模拟交易启动时触发一次。

    用于设置初始化配置、订阅数据、注册定时器等。
    context 对象(StrategyContext实例)在所有回调方法间传递。

    Args:
        context: 策略上下文环境对象
            固有属性:
            - subscribe(list): 策略订阅信息
    """
    version_info = "v1_0"
    version_time = time.strftime("%Y-%m-%d ", time.localtime(time.time()))
    qlog.info_f("[init]初始化{} 版本号:{}", strategy_name, version_time + "_" + version_info)

    context.param = Param()
    context.bar_last_time = None
    context.start_k_num = 50
    context.k_num_flag = False

    # ── 合约信息 ──────────────────────────────────────────────────
    symbol_info = base.get_contract(context.param.symbol)
    context.digits = symbol_info['noDecimal']        # 价格小数位数
    context.point = eval(f'1e-{context.digits}')      # 最小变动价位
    context.slippage_open = 20 * context.point        # 开仓滑点
    context.slippage_close = 500 * context.point      # 平仓滑点

    # ── 止盈止损缓存 ──────────────────────────────────────────────
    context.sl = context.param.sl
    context.tp = context.param.tp
    context.sl_id = {}          # 订单ID → 止损价
    context.tp_id = {}          # 订单ID → 止盈价
    context.openid_closeid = {} # 开仓ID → {close_id, close_side}
    context.closeid_openid = {} # 平仓ID → 开仓ID
    context.tc = TradeCount(context)

    # ── 推平保护 ──────────────────────────────────────────────────
    context.protect_flag = context.param.protect_flag if hasattr(context.param, 'protect_flag') else False
    context.protect_point = 300
    context.protect_move_point = 100

    # ── 移动止损 ──────────────────────────────────────────────────
    context.move_flag = context.param.move_flag if hasattr(context.param, 'move_flag') else False
    context.start_move_sl = 250
    context.move_point_sl = 300

    # ── 交易开关 ──────────────────────────────────────────────────
    context.all_open = True

    # ── 策略特定参数(根据策略逻辑添加) ────────────────────────────

    qlog.info_f("========== 策略运行参数 ==========")
    qlog.info_f("交易品种: {}", context.param.symbol)
    qlog.info_f("合约渠道: {}", context.param.source)
    qlog.info_f("Bar频率: {}", context.param.bar_frequency)
    qlog.info_f("下单量: {}", context.param.lot)
    qlog.info_f("止盈点数: {}, 止损点数: {}, 价差点数: {}", context.param.tp, context.param.sl, context.param.sp)
    qlog.info_f("Digits: {}, Point: {}", context.digits, context.point)
    qlog.info_f("开仓滑点: {}, 平仓滑点: {}", context.slippage_open, context.slippage_close)
    qlog.info_f("推平保护: {}, 移动止损: {}", context.protect_flag, context.move_flag)
    qlog.info_f("==================================")


def onData(context, data):
    """已订阅合约tick数据更新时触发。策略核心逻辑通常在此实现。

    Args:
        context: 策略上下文
        data(list[dict]): 行情数据列表，每条含 best_bid, best_ask, time 等
    """
    # 获取bar数据
    bar = md.query_bars_pro(context.param.symbol, context.param.bar_frequency, context.param.source,
                            count=100, fields=['time', 'close', 'high', 'low', 'open'])

    # 缓存市价 + 价差检查
    if not check_spread(context, data[0]):
        return

    # 先平仓(止盈止损检查)
    if context.tc.buy_num > 0:
        close_buy(context)
    if context.tc.sell_num > 0:
        close_sell(context)

    # 止损工具(推平保护 + 移动止损)
    # sl_tool(context)

    # 检查bar时间是否更新
    if not check_bar_time(context, bar):
        return

    # 检查bar数量是否足够
    if not context.k_num_flag:
        if len(bar['close']) < context.start_k_num + 1:
            return
        context.k_num_flag = True

    # ── 策略指标计算 ──────────────────────────────────────────────
    close_arr = bar['close']
    high_arr = bar['high']
    low_arr = bar['low']
    open_arr = bar['open']

    # 示例: 使用 talib 计算指标
    # ema_fast = ta.EMA(close_arr, context.param.ema_fast)
    # ema_slow = ta.EMA(close_arr, context.param.ema_slow)
    # rsi = ta.RSI(close_arr, context.param.rsi_period)

    # ── 入场逻辑 ──────────────────────────────────────────────────
    # if context.tc.buy_num < 1 and context.all_open:
    #     if 入场条件:
    #         open_price = round(context.ask + context.slippage_open, context.digits)
    #         sl_price = round(open_price - context.param.sl * context.point, context.digits)
    #         tp_price = round(open_price + context.param.tp * context.point, context.digits)
    #         open_buy(context, open_price, sl_price=sl_price, tp_price=tp_price)
    #         return
    #
    # if context.tc.sell_num < 1 and context.all_open:
    #     if 入场条件:
    #         open_price = round(context.bid - context.slippage_open, context.digits)
    #         sl_price = round(open_price + context.param.sl * context.point, context.digits)
    #         tp_price = round(open_price - context.param.tp * context.point, context.digits)
    #         open_sell(context, open_price, sl_price=sl_price, tp_price=tp_price)
    #         return
    pass


def onOrder(context, order):
    """订单状态变化时触发。

    Args:
        context: 策略上下文
        order(dict): 订单对象(字段见 quantapi.deal 模块顶部公共字段说明)
            orderStatus: 0-初始化 1-运行中 2-拒绝 5-超时 6-撤销中 7-已撤销 8-已结束 9-已提交 99-未明
    """
    qlog.info_f("onOrder->{}", order)
    context.tc = TradeCount(context)
    if order['id'] in context.closeid_openid:
        if order['orderStatus'] == '8':
            open_id = context.closeid_openid[order['id']]
            del context.openid_closeid[open_id]
            del context.closeid_openid[order['id']]
            qlog.info('onOrder->平仓单处理完成')
        elif order['orderStatus'] == '7':
            open_id = context.closeid_openid[order['id']]
            del context.closeid_openid[order['id']]
            context.openid_closeid[open_id]['close_id'] = 0
            qlog.info('onOrder->平仓单未成交已撤销，等待重新挂单')


def onTrade(context, trade):
    """产生成交后触发。"""
    pass


def onTime(context, time, name):
    """定时任务触发。需先在 init 中通过 scheduler 注册定时器。"""
    pass


def onBusinessDate(context, data):
    """切日事件触发。"""
    pass


def onMonitor(context, data):
    """接口链路启停触发。"""
    pass
```

## 反射兼容性检查清单

生成 main.py 后，必须逐项验证以下条件：

| # | 检查项 | 要求 | 验证方法 |
|---|--------|------|----------|
| 1 | `__init__.py` 存在 | 策略目录下必须包含（可为空文件） | `ls strategy_dir/{name}/__init__.py` |
| 2 | `main.py` 存在 | 策略目录下必须包含 | `ls strategy_dir/{name}/main.py` |
| 3 | 回调函数在模块顶层 | `init`/`onData`/`onOrder` 等不能嵌套在类中 | `grep "^def init" main.py` |
| 4 | 函数签名匹配 | `init(context)` / `onData(context, data)` / `onOrder(context, order)` | 检查函数定义 |
| 5 | quantapi 导入在顶层 | `import quantapi.*` 不能在函数内部 | `grep "^import quantapi" main.py` |
| 6 | 策略目录名合法 | 符合Python标识符规范(字母/下划线开头) | `python3 -c "import {name}"` |
| 7 | 无语法错误 | main.py 可被 Python 解析 | `python3 -c "import ast; ast.parse(open('main.py').read())"` |
| 8 | strategy_name 变量 | 模块顶层定义，与目录名一致 | `grep "^strategy_name" main.py` |

## 核心接口速查

### 交易接口 (quantapi.deal)

| 函数 | 说明 |
|------|------|
| `deal.to_order(symbol, side, price, quantity, effect, ...)` | 下单，返回订单ID |
| `deal.cancel_order(orderid)` | 撤单 |
| `deal.get_order(orderid)` | 查询订单 |
| `deal.get_orders()` | 查询所有委托单 |

### 行情接口 (quantapi.md)

| 函数 | 说明 |
|------|------|
| `md.get_price(symbol, source)` | 获取最新tick行情 |
| `md.query_bars_pro(symbol, frequency, source, count, fields)` | 获取Bar数据 |

### 持仓接口 (quantapi.pos)

| 函数 | 说明 |
|------|------|
| `pos.get_position(symbol, pos_side)` | 获取合约持仓 |
| `pos.get_ord_position(symbol)` | 获取逐笔持仓(含id字段) |

### 参数接口 (quantapi.param)

| 函数 | 说明 |
|------|------|
| `param.get(key, default)` | 获取策略参数 |
| `param.get_num(key, default)` | 获取数值型参数 |

### 基础接口 (quantapi.base)

| 函数 | 说明 |
|------|------|
| `base.get_contract(code)` | 获取合约信息(含noDecimal等) |

### 日志接口 (quantapi.qlog)

| 函数 | 说明 |
|------|------|
| `qlog.info(msg)` / `qlog.info_f(fmt, *args)` | info日志 |
| `qlog.error(msg)` / `qlog.error_f(fmt, *args)` | error日志 |

## 技术指标 (talib)

```python
import talib as ta

ta.SMA(arr, timeperiod)          # 简单移动平均
ta.EMA(arr, timeperiod)          # 指数移动平均
ta.RSI(arr, timeperiod)          # 相对强弱指标
ta.BBANDS(arr, timeperiod, nbdevup, nbdevdn)  # 布林带
ta.MACD(arr, fast, slow, signal) # MACD
ta.STOCH(high, low, close, ...)  # 随机指标
ta.ATR(high, low, close, timeperiod)  # 平均真实波幅
```

## Param类参数规范

参数通过 `param.get('参数中文名')` 获取，参数中文名必须与 `config/custom.json` 中的 `cname` 字段和 `config/plan/*.json` 中的 `key` 字段保持一致。

## 止盈止损逻辑

模板中内置了止盈止损机制：
1. 开仓时通过 `open_buy`/`open_sell` 的 `sl_price`/`tp_price` 参数传入止盈止损价
2. 止盈止损价缓存在 `context.sl_id` 和 `context.tp_id` 字典中(以订单ID为key)
3. 每次 `onData` 触发时先调用 `close_buy`/`close_sell` 检查是否触发止盈止损
4. 触发后调用 `close_all` 平仓

计算止盈止损价示例：
```python
# 多单止盈止损
sl_price = round(context.bid - context.sl * context.point, context.digits)
tp_price = round(context.bid + context.tp * context.point, context.digits)

# 空单止盈止损
sl_price = round(context.ask + context.sl * context.point, context.digits)
tp_price = round(context.ask - context.tp * context.point, context.digits)
```

## 推平保护与移动止损

模板内置了推平保护和移动止损功能，在 `init` 中通过开关控制：

```python
# 推平保护: 盈利超过 protect_point 后，止损价移至成本价 + protect_move_point
context.protect_flag = True/False
context.protect_point = 300
context.protect_move_point = 100

# 移动止损: 盈利超过 start_move_sl 后，止损价跟随市价移动 move_point_sl
context.move_flag = True/False
context.start_move_sl = 250
context.move_point_sl = 300
```

在 `onData` 中调用 `sl_tool(context)` 即可启用。
 