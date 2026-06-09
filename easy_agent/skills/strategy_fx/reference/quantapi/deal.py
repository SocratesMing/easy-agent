deal = None


def to_order(symbol,
             side,
             price,
             quantity,
             effect=None,
             order_type=2,
             in_out_market=2,
             channel_code=None,
             time_in_force=1,
             expire_time=None,
             hedge_flag="1",
             intention=None,
             warn_price=None,
             stop_price=None,
             value_date=None,
             maturity_date=None,
             close_order_id=None,
             pos_type=None,
             currency=None,
             bond_quote_type=9,
             stop_loss_price=None,
             take_profit_price=None,
             bp=None,
             quoteId=None,
             price_rate=None,
             sync=False,
             *args,
             **kwargs) -> str:
    """
    描述
        下单接口

    参数
        - symbol (string): 合约唯一代码
        - side (string): 交易方向
            - B - 买
            - S - 卖
        - price (double): 报单价格
        - quantity (double): 报单总数量
        - effect (int): 开平仓类型:
            - 0 - 中性
            - 1 - 开仓
            - 2 - 平仓
        - order_type (int): 订单类型类型:
            本币业务支持 0、2、12
            贵金属业务支持 2、14
            外汇业务支持 2、31、33、37、38、39
            - 0 -   CLICK
            - 14 -  STOPLIMIT
            - 12 -  OUT
            - 2 -   LIMIT
            - 31 -  Q-LIMIT
            - 33 -  Q-STOPLOSS
            - 37 -  Q-OCO
            - 38 -  Q-IF-DONE
            - 39 -  Q-IF-DNOE-OCO
        - in_out_market(int): 内外部市场
            - 1 - 内部市场
            - 2 - 外部市场【默认】
            - 3 - 内/外部市场
        - channel_code(string): 交易渠道 (default: {None})
        - time_in_force(int): 订单时效性
            - 1 - GFD(交易日有效)
            - 4 - FOK(极短时间全部成交，否则全部撤销)
            - 5 - FAK(极短时间成交，剩余量全部撤销)【默认】
            - 6 - GTC(订单撤销之前都有效)
            - 7 - GTD(过期时间之前有效)
        - expire_time(string): 到期时间
          如果设置了GTD模式,expire_time是必填项,仅支持YYYYMMDDHHMMSS (default: {None})
        - hedge_flag(string): 投机套保标识(期货专有))
            - 1 - 普通【默认】
            - 2 - 投机
            - 3 - 套保
        - intention(string): 交易意图
          可以根据业务场景自定义内容 (default: {None})
        - warn_price(double): 止损预警价	停止限价单使用 (default: {None})
        - stop_price(double): 止损价 (default: {None})
        - value_date(string): 近端起息日/近端交割日	掉期交易使用 格式: yyyyMMdd (default: {None})
        - maturity_date(string): 远端交割日	掉期交易使用 格式: yyyyMMdd (default: {None})
        - close_order_id(int): 平仓订单ID	逐笔模式ID (default: {None})
        - pos_type(int): 逐笔模式
            - 0 - 不启用【默认】
            - 1 - 启用 (default: {None})
        - currency(string): 使用货币 (default: {None})
        - bond_quote_type:
            - 9 - 连续匹配
            - 10 - 集中匹配
        - stop_loss_price: 止损平仓价
        - take_profit_price: 止盈平仓价
        - price_bp: 容忍点差
        - stop_price_bp: 容忍点差
        - stop_loss_price_bp: 容忍点差
        - take_profit_price_bp: 容忍点差
        - price_et: 执行时间
        - stop_price_et: 执行时间
        - stop_loss_price_et: 执行时间
        - take_profit_price_et: 执行时间
        - partial: 是否支持部分开仓时平仓
        - spot_rate: 止盈或止损价
        - trading_rule: 下单规则

    返回
        int: 订单唯一ID号

    示例
        >>> id = deal.to_order(context.ptparam.symbol,
                        side,
                        data[0].best_bid * 0.95 if side == "S" else data[0].best_ask * 1.05,
                        abs(quantity),
                        order_type=2,
                        effect=0,
                        in_out_market=2,
                        channel_code=context.ptparam.source,
                        time_in_force=5)
        216867535650689024

    算法单介绍
        - Q-LIMIT 限价单，如果是买单，则当前市价价格低于设定的价格后买入，如果是卖单，前市价价格高于设定的价格后卖出
            1. 传参
                - price: 代表下单价格
                - price_bp: 代表Q-Limit类型的容忍点差
                - price_et: 代表扫单执行时间 (以min为单位, 默认 1min)
            2. 校验: time_in_force 【必填】 6(GTC)

        >>> id = deal.to_order(context.ptparam.symbol,
                        side,
                        data[0].best_ask * 0.95 if side == "S" else data[0].best_ask * 1.05,
                        abs(quantity),
                        order_type=31,
                        effect=0,
                        in_out_market=2,
                        channel_code=context.ptparam.source,
                        time_in_force=6,
                        price_bp=5,
                        price_et=10)

        - Q-STOPLOSS: 突破单，如果是买单，则当前市价价格高于设定的价格后买入，如果是卖单，前市价价格低于设定的价格后卖出
            1. 传参
                - price: 下单价格
                - stop_price_bp: Q-StopLoss类型的容忍点差
                - stop_price_et: 扫单执行时间 (以min为单位)
            2. 校验: time_in_force 【必填】 6(GTC)

        >>> id = deal.to_order(context.ptparam.symbol,
                        side,
                        data[0].best_bid * 0.95 if side == "S" else data[0].best_ask * 1.05,
                        abs(quantity),
                        order_type=33,
                        effect=0,
                        in_out_market=2,
                        channel_code=context.ptparam.source,
                        time_in_force=6,
                        stop_price_bp=5,
                        stop_price_et=10)

        - OCO(one cancles the other): 一般是将盈利和止损单绑定到一起，确保一个执行时，另一个被取消
            1. 传参: price 代表止盈单价格
                - price_bp: 止盈容忍点差
                - price_et: 止盈单扫单执行时间 (以min 为单位)
                - stop_price: 止损单价格
                - stop_price_bp: 止损容忍点差
                - stop_price_et: 止损单扫单执行时间 (以min为单位)
            2. 校验: time_in_force 【必填】 6(GTC)
                - side为B, price < stop_price
                - side为S, price > stop_price

        >>> id = deal.to_order(context.ptparam.symbol,
                        side,
                        data[0].best_bid * 0.95 if side == "S" else data[0].best_ask * 1.05,
                        abs(quantity),
                        order_type=37,
                        effect=0,
                        in_out_market=2,
                        channel_code=context.ptparam.source,
                        time_in_force=6,
                        stop_price=data[0].best_bid * 0.0075,
                        price_bp=5,
                        price_et=10,
                        stop_price_bp=5,
                        stop_price_et=10)

        - IF-DONE: 如果顶一个订单成立，第二个订单才有效
            1. 传参
                - price: 止盈开仓单价格
                - price_bp: 止盈开仓单容忍点差
                - price_et: 止盈开仓单扫单执行时间(以min 为单位)
                - stop_price: 止损开仓单价格
                - stop_price_bp: 止损开仓单容忍点差
                - stop_price_et: 止损开仓单扫单执行时间(以min为单位)
                - stop_loss_price: 止损平仓单价格
                - stop_loss_price_bp: 止损平仓容忍点差
                - stop_loss_price_et: 止损平仓执行时间
                - take_profit_price: 止盈平仓价格
                - take_profit_price_bp: 止盈平仓容忍点差
                - take_profit_price_et: 止盈平仓执行时间
                - stop_loss_price 与 take_profit_price 二者传其一
            2. 校验:
                - time_in_force 【必填】 6(GTC)
                - side为B,  take_profit_price不为空, price < take_profit_price
                  stop_loss_price不为空, price > stop_loss_price
                - side为S,  take_profit_price不为空, price > take_profit_price
                  stop_loss_price不为空, price < stop_loss_price

        >>> id = deal.to_order(context.ptparam.symbol,
                        side,
                        data[0].best_bid * 0.95 if side == "S" else data[0].best_ask * 1.05,
                        abs(quantity),
                        order_type=38,
                        effect=0,
                        in_out_market=2,
                        channel_code=context.ptparam.source,
                        time_in_force=6,
                        take_profit_price=data[0].best_ask * 0.0075,
                        price_bp=5,
                        price_et=10,
                        take_profit_price_bp=5,
                        take_profit_price_et=10)


        - IF-DONE-OCO: 如果第一个订单不成立，第二个和第三个订单无效；
          如果第一个订单成立，则第二个和第三个中的任何一个订单成立，另一个自动取消
            1. 传参
                - stop_loss_price
                - take_profit_price 【必填】
            2. 校验:time_in_force 【必填】 6(GTC)
                - side为B,  price < take_profit_price
                  price > stop_loss_price
                - side为S,  price > take_profit_price
                  price < stop_loss_price

        >>> id = deal.to_order(context.ptparam.symbol,
                        side,
                        data[0].best_bid * 0.95 if side == "S" else data[0].best_ask * 1.05,
                        abs(quantity),
                        order_type=39,
                        effect=0,
                        in_out_market=2,
                        channel_code=context.ptparam.source,
                        time_in_force=6,
                        take_profit_price=data[0].best_ask * 0.0075,
                        stop_loss_price=data[0].best_bid * 0.00045
                        price_bp=5,
                        price_et=10,
                        take_profit_price_bp=5,
                        take_profit_price_et=10,
                        stop_loss_price_bp=5,
                        stop_loss_price_et=10)

    债券(包含点击成交)新增字段介绍
        - price_rate: 收益率
        - quoteId: 点击行情的报价id (从行情对象中获取)
        - leaves_qty: 行情剩余量 (从行情对象中获取)
        - transact_time: 行情业务发生时间(格式:YYYYMMDD-HH:MM:SS.sss)
    """
    return deal.to_order(symbol, side, price, quantity, effect, order_type, in_out_market, channel_code, time_in_force,
                         expire_time, hedge_flag, intention, warn_price, stop_price, value_date,
                         maturity_date, close_order_id, pos_type, currency, bond_quote_type, stop_loss_price,
                         take_profit_price, bp, quoteId, price_rate, sync, *args, **kwargs)


def fx_order_bl(symbol, side, price, quantity, channel_code):
    """
    描述
        下单接口

    参数
        - symbol (string): 合约唯一代码, USDCNYSP
        - side (string): 交易方向
            - B - 买
            - S - 卖
        - price (double): 报单价格
        - quantity (double): 报单总数量
        - channel_code(string): 交易渠道 (default: {None})
        """
    return deal.fx_order_bl(symbol, side, price, quantity, channel_code)


def get_order(orderid):
    """
    描述:
        获取订单信息

    参数:
        orderid(str):订单ID

    返回 object:
        一个order委托对象，如果不存在对应的order，返回None
        - id(str):订单ID
        - channelCode(string):渠道
        - symbol(string):合约
        - orderType(int):订单类型-->OrderTypeEnum[0:点击单 2:限价单(默认) 15:SOR单 28:直通限价单 52:止损限价单 54:止损单]
        - timeInForce(int):订单时效性,有效时间类型[1-GTC(撤销前一直有效),4-FOK(极短时间全部成交，否则全部撤销),5-FAK(极短时间成交，剩余量全部撤销),6-GFD(当日闭市前有效),7-GTD(当日有效,必须设置过期时间)  (default: {GTC})]
        - expireTime(int):过期时间
        - price(float):挂单价
        - side(string):持仓方向-->SideEnum[B-买入,S-卖出]  (default: {None})
        - effect(int):开平仓类型-->EffectEnum[0-中性,1-开仓,2-平仓]
        - quantity(int):下单量
        - amount(float):交易金额
        - tradedQuantity(int):成交量
        - orderStatus(int):订单状态-->OrderStatusEnum[0-初始化,1-运行中,2-订单拒绝,3-开仓成交,5-订单已超时,6-订单撤销中,7-交易已撤销,8-已结束,9-已提交,99-未明]
        - createTime(int):创建时间
        - inOutMarket(string):执行市场-->InOutMarketEnum[1-内部市场,2-外部市场,3-内/外部市场]
        - errorMsg(string):异常信息
        - tradedAvgPrice(float):成交均价
        - hedgeFlag(string):投机套保标识(期货专业))-->HedgeFlagEnum[1-普通,2-投机,3-套保]
        - intention(string):交易意图
        - valueDate(string):起息日
        - maturityDate(string):到期日
        - closeOrderId(str):逐笔模式平仓订单id
        - posType(string):逐笔模式-->PosTypeEnum[0-不启用(默认),1-启用]
        - warnPrice(float):预警价
        - stopPrice(float):止损价
        - ctimeStamp(long):生成数据纳秒
        - stopLossPrice(float):平仓止损价
        - takeProfitPrice(float):平仓止盈价
        - closeTradedQuantity(int):平仓成交量
        - closeAmount(int):平仓交易金额
        - closeTradedAvgPrice(float):平仓成交价格
        - bp(int): 容忍点差
        - quoteId(str): 报价id
        - otherAgencyId(str): 对手方机构21位码
        - otherAgencyName(str): 对手方机构简称
        - otherTraderId(str): 对手方交易员id
        - otherTraderName(str): 对手方交易员名称

    示例:
        >>> deal.get_order(216867535650689024)
        {'id': '216867660628103168', 'channelCode': 'CFETS_LC', 'symbol': 'EURUSDSP', 'orderType': 2,
        'timeInForce': 5, 'expireTime': None, 'price': 1.1027695, 'side': 'S', 'effect': 0,
        'quantity': 10, 'amount': 0, 'tradedQuantity': 0, 'orderStatus': '9',
        'createTime': '20180719174900', 'inOutMarket': 2, 'errorMsg': '', 'tradedAvgPrice': 0,
        'hedgeFlag': None, 'intention': None, 'valueDate': None, 'maturityDate': None,
        'closeOrderId': None, 'posType': None, 'warnPrice': None, 'stopPrice': None,
        'ctimeStamp': 1531993740600, 'stopLossPrice':None, 'takeProfitPrice':None,
        'closeTradedQuantity':None, 'closeAmount': None, 'closeTradedAvgPrice': None, 'bp': None}
    """
    return deal.get_order(orderid)


def get_orders(channel_code=None, symbol=None, side=None):
    """
    描述:
        批量获取进行中的委托订单

    参数:
        channel_code(string):市场代码
        symbol(string):合约名 (default: {None})
        side(string):持仓方向-->SideEnum{B-买入,S-卖出} (default: {None})

    返回 list:
        - id(str):订单ID
        - channelCode(string):渠道
        - symbol(string):合约
        - orderType(int):订单类型-->OrderTypeEnum[0:点击单 2:限价单(默认) 15:SOR单 28:直通限价单 52:止损限价单 54:止损单]
        - timeInForce(int):订单时效性,有效时间类型[1-GTC(撤销前一直有效),4-FOK(极短时间全部成交，否则全部撤销),5-FAK(极短时间成交，剩余量全部撤销),6-GFD(当日闭市前有效),7-GTD(当日有效,必须设置过期时间)  (default: {GTC})]
        - price(float):挂单价
        - side(string):持仓方向-->SideEnum[B-买入,S-卖出]  (default: {None})
        - effect(int):开平仓类型-->EffectEnum[0-中性,1-开仓,2-平仓]
        - quantity(int):下单量
        - amount(float):交易金额
        - tradedQuantity(int):成交量
        - orderStatus(int):订单状态-->OrderStatusEnum[0-初始化,1-运行中,2-订单拒绝,3-开仓成交,5-订单已超时,6-订单撤销中,7-交易已撤销,8-已结束,9-已提交,99-未明]
        - createTime(int):创建时间
        - inOutMarket(string):执行市场-->InOutMarketEnum[1-内部市场,2-外部市场,3-内/外部市场]
        - errorMsg(string):异常信息
        - tradedAvgPrice(float):成交均价
        - hedgeFlag(string):投机套保标识(期货专业))-->HedgeFlagEnum[1-普通,2-投机,3-套保]
        - intention(string):交易意图
        - valueDate(string):起息日
        - maturityDate(string):到期日
        - closeOrderId(str):逐笔模式平仓订单id
        - posType(string):逐笔模式-->PosTypeEnum[0-不启用(默认),1-启用]
        - warnPrice(float):预警价
        - stopPrice(float):止损价
        - ctimeStamp(long):生成数据纳秒
        - stopLossPrice(float):平仓止损价
        - takeProfitPrice(float):平仓止盈价
        - closeTradedQuantity(int):平仓成交量
        - closeAmount(int):平仓交易金额
        - closeTradedAvgPrice(float):平仓成交价格
        - bp(int): 容忍点差
        - quoteId(str): 报价id
        - otherAgencyId(str): 对手方机构21位码
        - otherAgencyName(str): 对手方机构简称
        - otherTraderId(str): 对手方交易员id
        - otherTraderName(str): 对手方交易员名称

        使用方法:
            >>> deal.get_orders()
            [{'id': '216867660628103168', 'channelCode': 'CFETS_LC', 'symbol': 'EURUSDSP', 'orderType': 2,
            'timeInForce': 5, 'expireTime': None, 'price': 1.1027695, 'side': 'S', 'effect': 0,
            'quantity': 10, 'amount': 0, 'tradedQuantity': 0, 'orderStatus': '9',
            'createTime': '20180719174900', 'inOutMarket': 2, 'errorMsg': '', 'tradedAvgPrice': 0,
            'hedgeFlag': None, 'intention': None, 'valueDate': None, 'maturityDate': None,
            'closeOrderId': None, 'posType': None, 'warnPrice': None, 'stopPrice': None,
            'ctimeStamp': 1531993740600, 'stopLossPrice':None, 'takeProfitPrice':None,
            'closeTradedQuantity':None, 'closeAmount': None, 'closeTradedAvgPrice': None, 'bp': None,
            'quoteId': None,'otherAgencyId': None, "otherAgencyName': None,'otherTraderId': None,
            'otherTraderName': None}]
    """
    return deal.get_orders(channel_code, symbol, side)


def cancel_order(orderid=None, channel_code=None, symbol=None, side=None):
    """
    描述:
        根据条件撤销委托挂单, 注意如果订单为瞬时属性的订单,则不会被撤单

    参数:
        orderid(str):订单ID (default: {None})
        channel_code(string):市场代码 (default: {None})
        symbol(string):合约名 (default: {None})
        side(string):持仓方向-->SideEnum{B:买入,S:卖出}  (default: {None})

    返回值:
        无

    示例:
        >>> deal.cancel_order() # 撤销全部委托
        >>> deal.cancel_order(216867660628103168) # 撤销指定委托

    """
    return deal.cancel_order(orderid, channel_code, symbol, side, True)


def update_tp_sl_price(order_id, tp_price=None, sl_price=None):
    """
    描述:
        存储止盈止损价格

    参数:
        - order_id(string): 订单ID
        - tp_price(float|string): 止盈价 (default: {None})
        - sl_price(float|string): 止损价 (default: {None})

    返回值:
        无

    示例:
        >>> deal.update_tp_sl_price(order_id=123456789, tp_price=1.0913, sl_price=1.0236)
    """
    return deal.update_tp_sl_price(order_id=order_id, tp_price=tp_price, sl_price=sl_price)


def get_tp_sl_price(order_id=None):
    """
    描述:
        获取止盈止损价格
    参数:
        - order_id(string): 订单ID (default: {None})
    返回值:
        list<dict>: 包含止盈止损价信息，里面包括多个对象
    示例:
        >>> deal.get_tp_sl_price(order_id=123456789) # 按照order_id查询
        [{'runId': 216867660628103168, 'order_id': '123456789', 'sl_price': 1.0236, 'tp_price': 1.0913}]

        >>> deal.get_tp_sl_price() # 按照run_id查询
        [{'runId': 216867660628103168, 'order_id': '1234567890', 'sl_price': 1.0236, 'tp_price': 1.0913},
        {'runId': 216867660628103168, 'order_id': '1234567891', 'sl_price': 1.0236, 'tp_price': 1.0913}]
    """
    return deal.get_tp_sl_price(order_id=order_id)


def to_order_exception(err_str=None, user_id=None, email_switch=True, cover_mode=1):
    """
    描述:
        跑马灯异常信息通知

    参数:
        - err_str(string): 错误信息 (default: {None})
        - user_id(string): 用户id (default: {None})
        - email_switch(bool): 邮件控制开关 True-发送, False-不发送 (default: True)
        - cover_mode(int): 跑马灯模式 1-覆盖, 0-追加 (default: 1)

    返回:
        无

    示例:
        >>> deal.to_order_exception(err_str, user_id) # 推送给客户端跑马灯方式展示

    """
    return deal.to_order_exception(err_str, user_id, email_switch, cover_mode)


def to_stop_strategy():
    """
    描述
       策略停止接口
    """
    return deal.to_stop_strategy()


def get_pm_deal_count(start_time=None, end_time=None):
    """
    描述:
       贵金属业务查询成交统计信息接口

    参数:
       start_time(String): 开始时间(default: None) 格式yyyyMMddHHmmss
       end_time(String): 结束时间(default: None) 格式yyyyMMddHHmmss
    """
    return deal.get_pm_deal_count(start_time, end_time)
