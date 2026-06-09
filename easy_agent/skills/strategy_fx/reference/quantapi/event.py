"""事件回调说明 — 策略生命周期事件接口定义

策略必须实现以下事件回调函数，框架会在对应时机自动调用。
"""


def init(context):
    """策略初始化。在回测和实时模拟交易启动时触发一次。

    用于设置初始化配置、订阅数据、注册定时器等。

    Args:
        context: 策略上下文环境对象，在所有方法间传递。
            固有属性:
            - subscribe(list): 策略订阅信息，每条含:
                - symbol(str): 合约
                - sub_type(str): 订阅类型 1-tick 2-bar
                - kind(str): 数据类型 tick/bar
                - source(str): 渠道
                - type(str): 行情类型，如 FXSPOT / 1N_BAR_DEPTH

    Example:
        >>> def init(context):
        ...     context.subscribe = [
        ...         {"symbol": "EURUSDSP", "sub_type": "1", "kind": "tick", "source": "UBS_HO", "type": "FXSPOT"},
        ...         {"symbol": "EURUSDSP", "sub_type": "2", "kind": "bar", "source": "UBS_HO", "type": "1N_BAR_DEPTH"},
        ...     ]
    """
    pass


def onData(context, data):
    """已订阅合约tick数据更新时触发。策略核心逻辑通常在此实现。

    Args:
        context: 策略上下文环境对象
        data(list[dict]): 行情数据列表，每条字段同 md.get_price 返回值

    Example:
        >>> def onData(context, data):
        ...     tick = data[0]
        ...     bid = tick.best_bid
        ...     ask = tick.best_ask
    """
    pass


def onOrder(context, order):
    """订单状态变化时触发。

    Args:
        context: 策略上下文环境对象
        order(dict): 订单对象(字段同 deal 模块公共字段)

    Note:
        订单状态: 0-初始化 1-运行中 2-拒绝 5-超时 6-撤销中 7-已撤销 8-已结束 9-已提交 99-未明

    Example:
        >>> def onOrder(context, order):
        ...     order_id = order.id
    """
    pass


def onTrade(context, trade):
    """产生成交后触发。

    Args:
        context: 策略上下文环境对象
        trade(dict): 成交对象，主要字段:
            - id(str): 成交ID
            - orderId(str): 订单ID
            - channelCode(str): 渠道
            - symbol(str): 合约
            - valueDate(str): 起息日
            - side(str): 买卖方向
            - effect(int): 开平仓类型
            - price(float): 成交价
            - quantity(float): 成交量
            - amount(float): 成交金额
            - tradeTime(str): 成交时间
            - orderType(int): 订单类型

    Example:
        >>> def onTrade(context, trade):
        ...     trade_id = trade.id
    """
    pass


def onTime(context, time, name):
    """定时器触发时调用。需先在 init 中通过 scheduler 注册定时器。

    Args:
        context: 策略上下文环境对象
        time(str): 定时器触发时间
        name(str): 定时器名称(与scheduler注册的名称一致)

    Example:
        >>> # 在init中: scheduler.run_daily("my_job", "160000")
        >>> def onTime(context, time, name):
        ...     if name == 'my_job':
        ...         pass  # 执行定时任务
    """
    pass


def onBusinessDate(context, data):
    """切日事件触发。"""
    pass


def onMonitor(context, data):
    """接口链路启停事件触发。"""
    pass


def onSignal(context, data, operate_type):
    """信号监控触发事件(开仓/止盈/止损操作后)。

    Args:
        context: 策略上下文环境对象
        data(dict): 信号对象，当signal_type为止盈/止损/开仓时含 market_target 字段:
            - market_target.best_bid(float): 最优买价
            - market_target.best_bid_amt(float): 最优买价量
            - market_target.best_ask(float): 最优卖价
            - market_target.best_ask_amt(float): 最优卖价量
            - market_target.asks(list): 档位卖价
            - market_target.ask_vols(list): 档位卖量
            - market_target.bids(list): 档位买价
            - market_target.bid_vols(list): 档位买量
        operate_type(str): 信号操作类型

    Example:
        >>> def onSignal(context, data, operate_type):
        ...     signal_id = data.signal_id
    """
    pass


def onSignalPrice(context, signal):
    """推送最新市价事件(非必须实现)。

    若策略实现了此方法，则由策略决定推送的市价；
    若未实现，程序根据信号方向自动推送最优买/卖价。

    Args:
        context: 策略上下文环境对象
        signal(dict): 信号对象
    """
    pass


def onFolderTrade(context, trade):
    """推送账户维度成交信息(非必须实现)。

    Args:
        context: 策略上下文环境对象
        trade(dict): 成交对象
    """
    pass


def onFolderPosition(context, position):
    """推送账户维度头寸信息(非必须实现)。

    Args:
        context: 策略上下文环境对象
        position(dict): 头寸对象
    """
    pass


def onQuote(context, quote):
    """做市商报价被风控/交易中心拒绝时触发。

    Args:
        context: 策略上下文环境对象
        quote(dict): 报价对象，主要字段:
            外汇做市:
                - quoteId(str): 报价ID
                - status(str): 状态
                - symbol(str): 合约
                - time(int): 时间戳(毫秒)
                - floorCode(str): 交易分组
                - quoteTypeStr(str): 报价类型
                - quoteDateTime(int): 报价时间
                - makerDepths(list): 报价信息(含bid/ask/bidAmt/askAmt/level)
                - errorText(str): 失败原因
            债券做市:
                - quoteId(str): 报价ID
                - makerDepths(list): 报价信息(含bid/ask/bidAmt/askAmt)
                - quoteDateTime(str): 报价时间
                - symbol(str): 合约
                - errorText(str): 失败原因
                - status(int): 报价状态 1-失败

    Example:
        >>> def onQuote(context, quote):
        ...     maker.to_quote_order_confirm(id, "F")
    """
    pass


def onQuoteOrder(context, quoteOrder):
    """做市商报价被交易对手点击成交请求时触发(本币做市不触发)。

    需通过 to_quote_order_confirm 接受或拒绝请求。

    Args:
        context: 策略上下文环境对象
        quoteOrder(dict): 报价交易对象，主要字段:
            - floorCode(str): 交易分组
            - id(str): 订单ID
            - orderStatus(int): 订单状态
            - symbol(str): 合约
            - products(str): 产品类型
            - side(str): 买卖方向
            - orderType(int): 订单类型
            - quantity(float): 交易量
            - price(float): 价格

    Example:
        >>> def onQuoteOrder(context, quoteOrder):
        ...     maker.to_rfq_quote("EURUSDSP", 7.1, 7.2)
    """
    pass


def onRfqReq(context, rfqReq):
    """对手方发起询价请求时触发。

    Args:
        context: 策略上下文环境对象
        rfqReq(dict): 询价请求对象，主要字段:
            - channelCode(str): 渠道 CFETS-银行间 BC-债券通
            - quoteReqId(str): 询价请求编号
            - symbol(str): 合约
            - side(str): 交易方向 B-买入 S-卖出
            - orderQty(float): 询价量
            - price(float): 净价
            - yieldRate(float): 收益率
            - settlType(str): 清算速度
            - settlDate(str): 结算日
            - status(str): 2-待回复 3-已回复 4-已撤销 5-已过期 6-已拒绝
    """
    pass


def onRfqQuote(context, rfqQuote):
    """做市商回复报价状态变化时触发。

    Args:
        context: 策略上下文环境对象
        rfqQuote(dict): 回复报价对象，主要字段:
            - channelCode(str): 渠道
            - quoteReqId(str): 询价请求编号
            - quoteId(str): 报价回复编号
            - symbol(str): 合约
            - side(str): 交易方向
            - orderQty(float): 回价量
            - price(float): 净价
            - yieldRate(float): 收益率
            - status(str): 1-执行中 2-已成交 4-已撤销 5-已过期 8-已拒绝
            - errorCode(str): 异常代码
            - errorMsg(str): 异常信息
    """
    pass


def onRfqQuoteOrder(context, rfqQuoteOrder):
    """做市商报价被交易对手点击成交请求时触发(本币做市不触发)。

    需通过 to_rfq_quote_order_confirm 接受或拒绝请求。

    Args:
        context: 策略上下文环境对象
        rfqQuoteOrder(dict): 询价成交对象，主要字段:
            - id(str): 订单编号
            - floorCode(str): 交易分组
            - symbol(str): 合约
            - orderType(int): 订单类型
            - side(str): 买卖方向
            - orderStatus(int): 订单状态
            - effect(int): 开平仓类型
            - quantity(float): 交易量
            - price(float): 价格

    Example:
        >>> def onRfqQuoteOrder(context, rfqQuoteOrder):
        ...     trade_id = rfqQuoteOrder.id
    """
    pass
