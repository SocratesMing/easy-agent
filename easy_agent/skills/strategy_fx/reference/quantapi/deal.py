"""交易接口 — 下单、撤单、订单查询、止盈止损"""

deal = None


# ── 订单公共字段说明 ──────────────────────────────────────────────────
# 以下字段在 get_order / get_orders 返回值中通用:
#   id(str): 订单ID
#   channelCode(str): 渠道
#   symbol(str): 合约
#   orderType(int): 订单类型 0-CLICK 2-LIMIT 15-SOR 28-直通限价 52-止损限价 54-止损
#   timeInForce(int): 时效性 1-GTC 4-FOK 5-FAK 6-GFD 7-GTD
#   expireTime(int): 过期时间
#   price(float): 挂单价
#   side(str): 买卖方向 B-买 S-卖
#   effect(int): 开平仓 0-中性 1-开仓 2-平仓
#   quantity(float): 下单量
#   amount(float): 交易金额
#   tradedQuantity(float): 成交量
#   orderStatus(str): 订单状态 0-初始化 1-处理中 2-拒绝 3-开仓成交 5-超时 6-撤销中 7-已撤销 8-已结束 9-已提交 99-未明
#   createTime(str): 创建时间
#   inOutMarket(int): 执行市场 1-内部 2-外部 3-内外部
#   errorMsg(str): 异常信息
#   tradedAvgPrice(float): 成交均价
#   hedgeFlag(str): 投机套保标识 1-普通 2-投机 3-套保
#   intention(str): 交易意图
#   valueDate(str): 起息日
#   maturityDate(str): 到期日
#   closeOrderId(str): 逐笔模式平仓订单ID
#   posType(int): 逐笔模式 0-不启用 1-启用
#   warnPrice(float): 预警价
#   stopPrice(float): 止损价
#   ctimeStamp(int): 时间戳(纳秒)
#   stopLossPrice(float): 平仓止损价
#   takeProfitPrice(float): 平仓止盈价
#   closeTradedQuantity(float): 平仓成交量
#   closeAmount(float): 平仓交易金额
#   closeTradedAvgPrice(float): 平仓成交价格
#   bp(int): 容忍点差
#   quoteId(str): 报价ID
# ─────────────────────────────────────────────────────────────────────


def to_order(symbol: str, side: str, price: float, quantity: float,
             effect: int = None, order_type: int = 2, in_out_market: int = 2,
             channel_code: str = None, time_in_force: int = 1,
             expire_time: str = None, hedge_flag: str = "1",
             intention: str = None, warn_price: float = None,
             stop_price: float = None, value_date: str = None,
             maturity_date: str = None, close_order_id=None,
             pos_type: int = None, currency: str = None,
             bond_quote_type: int = 9, stop_loss_price: float = None,
             take_profit_price: float = None, bp=None, quoteId=None,
             price_rate=None, sync: bool = False,
             *args, **kwargs) -> str:
    """下单接口。

    Args:
        symbol: 合约唯一代码
        side: 交易方向 B-买 S-卖
        price: 报单价格
        quantity: 报单总数量
        effect: 开平仓类型 0-中性 1-开仓 2-平仓
        order_type: 订单类型(见下方说明)
        in_out_market: 执行市场 1-内部 2-外部(默认) 3-内外部
        channel_code: 交易渠道
        time_in_force: 订单时效性 1-GFD 4-FOK 5-FAK 6-GTC 7-GTD
        expire_time: 到期时间(GTD模式必填，格式YYYYMMDDHHMMSS)
        hedge_flag: 投机套保标识 1-普通(默认) 2-投机 3-套保
        intention: 交易意图
        warn_price: 止损预警价(停止限价单使用)
        stop_price: 止损价
        value_date: 近端起息日(掉期使用，格式yyyyMMdd)
        maturity_date: 远端交割日(掉期使用，格式yyyyMMdd)
        close_order_id: 平仓订单ID(逐笔模式)
        pos_type: 逐笔模式 0-不启用(默认) 1-启用
        currency: 使用货币
        bond_quote_type: 债券报价类型 9-连续匹配 10-集中匹配
        stop_loss_price: 止损平仓价
        take_profit_price: 止盈平仓价
        bp: 容忍点差
        quoteId: 点击行情的报价ID(债券点击成交)
        price_rate: 收益率(债券)
        sync: 是否同步下单

    Returns:
        str: 订单唯一ID号

    order_type 说明:
        本币业务: 0-CLICK, 2-LIMIT, 12-OUT
        贵金属业务: 2-LIMIT, 14-STOPLIMIT
        外汇业务: 2-LIMIT, 31-Q_LIMIT, 33-Q_STOPLOSS, 37-Q_OCO, 38-Q_IF_DONE, 39-Q_IF_DONE_OCO

    算法单说明:
        Q-LIMIT(31): 限价单，买单价低于设定价买入，卖单价高于设定价卖出
            - 必填 time_in_force=6(GTC)
            - bp: 容忍点差, price_et: 扫单执行时间(min)

        Q-STOPLOSS(33): 突破单，买单价高于设定价买入，卖单价低于设定价卖出
            - 必填 time_in_force=6(GTC)
            - stop_price_bp: 容忍点差, stop_price_et: 扫单执行时间(min)

        Q-OCO(37): 止盈止损绑定单，一个执行另一个自动取消
            - 必填 time_in_force=6(GTC)
            - price: 止盈价, stop_price: 止损价
            - 买: price < stop_price; 卖: price > stop_price

        Q-IF-DONE(38): 条件单，第一个订单成立后第二个才有效
            - 必填 time_in_force=6(GTC)
            - price: 开仓价, stop_loss_price/take_profit_price: 平仓价(二选一)

        Q-IF-DONE-OCO(39): 条件+OCO组合单
            - 必填 time_in_force=6(GTC), take_profit_price
            - 买: price < take_profit_price 且 price > stop_loss_price
            - 卖: price > take_profit_price 且 price < stop_loss_price

    Example:
        >>> # 普通限价单
        >>> deal.to_order("EURUSDSP", "B", 1.0850, 10, order_type=2, time_in_force=5)
        >>> # Q-LIMIT算法单
        >>> deal.to_order("EURUSDSP", "B", 1.0850, 10, order_type=31, time_in_force=6, bp=5)
    """
    return deal.to_order(symbol, side, price, quantity, effect, order_type, in_out_market,
                         channel_code, time_in_force, expire_time, hedge_flag, intention,
                         warn_price, stop_price, value_date, maturity_date, close_order_id,
                         pos_type, currency, bond_quote_type, stop_loss_price,
                         take_profit_price, bp, quoteId, price_rate, sync, *args, **kwargs)


def fx_order_bl(symbol: str, side: str, price: float, quantity: float,
                channel_code: str):
    """外汇补录下单。

    Args:
        symbol: 合约代码，如 "USDCNYSP"
        side: 交易方向 B-买 S-卖
        price: 报单价格
        quantity: 报单总数量
        channel_code: 交易渠道
    """
    return deal.fx_order_bl(symbol, side, price, quantity, channel_code)


def get_order(orderid) -> dict:
    """获取订单信息。

    Args:
        orderid: 订单ID

    Returns:
        dict: 订单对象(字段见模块顶部公共字段说明)，不存在则返回None
    """
    return deal.get_order(orderid)


def get_orders(channel_code=None, symbol=None, side=None) -> list:
    """批量获取进行中的委托订单。

    Args:
        channel_code: 市场代码
        symbol: 合约名
        side: 买卖方向 B-买 S-卖

    Returns:
        list[dict]: 订单列表(字段见模块顶部公共字段说明)
    """
    return deal.get_orders(channel_code, symbol, side)


def cancel_order(orderid=None, channel_code=None, symbol=None, side=None):
    """撤销委托挂单。瞬时属性订单不会被撤单。

    Args:
        orderid: 订单ID(指定撤单)
        channel_code: 市场代码(按条件撤单)
        symbol: 合约名(按条件撤单)
        side: 买卖方向(按条件撤单)

    Note:
        不传参数则撤销全部委托。

    Example:
        >>> deal.cancel_order()  # 撤销全部
        >>> deal.cancel_order(216867660628103168)  # 撤销指定委托
    """
    return deal.cancel_order(orderid, channel_code, symbol, side, True)


def update_tp_sl_price(order_id, tp_price=None, sl_price=None):
    """存储止盈止损价格。

    Args:
        order_id: 订单ID
        tp_price: 止盈价
        sl_price: 止损价
    """
    return deal.update_tp_sl_price(order_id=order_id, tp_price=tp_price, sl_price=sl_price)


def get_tp_sl_price(order_id=None) -> list:
    """获取止盈止损价格。

    Args:
        order_id: 订单ID(不传则按run_id查询)

    Returns:
        list[dict]: 每条含 runId, order_id, sl_price, tp_price
    """
    return deal.get_tp_sl_price(order_id=order_id)


def to_order_exception(err_str=None, user_id=None, email_switch=True, cover_mode=1):
    """跑马灯异常信息通知。

    Args:
        err_str: 错误信息
        user_id: 用户ID
        email_switch: 邮件控制开关 True-发送 False-不发送
        cover_mode: 跑马灯模式 1-覆盖 0-追加
    """
    return deal.to_order_exception(err_str, user_id, email_switch, cover_mode)


def to_stop_strategy():
    """停止当前策略。"""
    return deal.to_stop_strategy()


def get_pm_deal_count(start_time=None, end_time=None):
    """贵金属业务查询成交统计信息。

    Args:
        start_time: 开始时间(yyyyMMddHHmmss)
        end_time: 结束时间(yyyyMMddHHmmss)
    """
    return deal.get_pm_deal_count(start_time, end_time)
