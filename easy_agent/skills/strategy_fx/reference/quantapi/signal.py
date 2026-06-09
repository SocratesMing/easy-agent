"""信号接口 — 外汇信号、本币信号的发送/更新/撤销"""

signal = None


class TypeEnum:
    """信号状态枚举。"""
    VALID = "V"   # 有效
    INVALID = "I"  # 无效


class Signal:
    """信号对象字段:
        - signal_id(str): 信号ID
        - symbol(str): 合约标的
        - channel(str): 渠道
        - frequency(str): 频率，如 "5N"
        - side(str): 买卖方向 B-买 S-卖
        - monitor(bool): 是否监控平仓行为
        - price(float): 挂单价
        - effect(int): 开平仓类型 0-中性 1-开仓 2-平仓
        - pos_type(int): 下单模式 0-净值模式 1-逐笔模式
        - create_time(int): 创建时间
        - update_time(int): 更新时间
        - status(str): 状态 V-有效 I-无效
        - param1(str): 自定义参数
        - param2(str): 自定义参数2
        - bankId(str): 所属机构
        - userId(str): 用户ID
        - is_closed(int): 是否平仓 1-止损 2-止盈
        - runId(str): 运行ID
        - runName(str): 运行名称
    """
    pass


def to_signal(signal_list: list) -> str:
    """发送外汇信号。

    Args:
        signal_list: 信号字典列表，每条含:
            - symbol(str): 合约标的(必填)
            - frequency(str): 时间周期，如 "5N"(必填)
            - channel(str): 渠道(必填)
            - side(str): 买卖方向 B-买 S-卖
            - price(float): 价格
            - effect(int): 开平仓类型 0-中性 1-开仓 2-平仓
            - monitor(bool): 是否监控平仓行为
            - pos_type(int): 0-净值模式 1-逐笔模式
            - is_closed(str): 开仓属性 1-止损 2-止盈
            - param1/param2(str): 自定义参数
            - send_chat(bool): 是否发送邮箱

    Returns:
        str: 信号唯一ID号。逐笔模式下返回开仓信号ID，平仓信号ID为开仓ID + "_C"

    Example:
        >>> signal.to_signal([{"symbol": "EURUSDSP", "frequency": "5N", "channel": "UBS_HO", "side": "B", "price": 1.2335, "pos_type": 0}])
    """
    if signal:
        return signal.to_signal(signal_list)
    return None


def cancel_signal(signal_ids: list[str], is_all: bool = False):
    """撤销指定的外汇信号。

    Args:
        signal_ids: 需要取消的信号ID列表
        is_all: 是否取消所有有效信号，默认False

    Example:
        >>> signal.cancel_signal(signal_ids=['9f03f05a-64ab-3459-a65e-8da2ea5d13c5'])
    """
    if signal:
        return signal.cancel_signal(signal_ids, is_all)
    return


def update_signal(update_list: list):
    """更新外汇信号内容。

    Args:
        update_list: 信号更新字典列表，每条含:
            - signal_id(str): 指定信号ID(必填)
            - symbol(str): 合约代码(必填)
            - effect(int): 开平仓类型(必填)
            - is_closed(int): 订单属性(逐笔模式) 1-止损 2-止盈
            - price(float): 价格
            - monitor(bool): 监控标识
            - frequency(str): 时间框架
            - param1/param2(str): 自定义参数
            - send_chat(bool): 是否发送邮箱

    Example:
        >>> signal.update_signal([{"signal_id": "9f03f05a-64ab-3459-a65e-8da2ea5d13c5", "price": 0.9875, "is_closed": 1}])
    """
    if signal:
        return signal.update_signal(update_list)
    return


def send_signal(symbol: str, frequency: str, channel: str = "X-BOND_HO",
                side: str = None, price: float = None, price_yield: float = None,
                effect: int = 0, pos_type: int = 0,
                param1: str = None, param2: str = None,
                param3: str = None, param4: str = None,
                send_chat: bool = False) -> str:
    """发送本币信号。

    Args:
        symbol: 合约标的(必填)
        frequency: 时间周期，如 "5N"(必填)
        channel: 渠道，默认 "X-BOND_HO"
        side: 买卖方向 B-买 S-卖
        price: 价格
        price_yield: 收益率
        effect: 开平仓类型 0-中性 1-开仓 2-平仓
        pos_type: 0-净值模式 1-逐笔模式
        param1/param2/param3/param4: 自定义参数
        send_chat: 是否发送邮箱

    Returns:
        str: 信号唯一ID号

    Example:
        >>> signal.send_signal(symbol="180011_T+1", frequency="5N", channel="X-BOND_HO")
    """
    if signal:
        return signal.send_signal(symbol, frequency, channel, side, price, price_yield,
                                  effect, pos_type, param1, param2, param3, param4, send_chat)
    return None


def renewal_signal(signal_id: str, symbol: str, frequency: str = None,
                   price: float = None, price_yield: float = None,
                   param1: str = None, param2: str = None,
                   param3: str = None, param4: str = None,
                   send_chat: bool = None):
    """更新本币信号内容。

    Args:
        signal_id: 指定信号ID(必填)
        symbol: 债券代码(必填)
        frequency: 时间框架
        price: 价格
        price_yield: 收益率
        param1/param2/param3/param4: 自定义参数
        send_chat: 是否发送邮箱

    Example:
        >>> signal.renewal_signal(signal_id="123456789", symbol="180011_T+1", frequency="10N")
    """
    if signal:
        return signal.renewal_signal(signal_id, symbol, frequency, price, price_yield,
                                     param1, param2, param3, param4, send_chat)
    return


def revoke_signal(signal_ids: list, is_all: bool = False):
    """撤销指定的本币信号。

    Args:
        signal_ids: 需要取消的信号ID列表
        is_all: 是否取消所有有效信号，默认False

    Example:
        >>> signal.revoke_signal(signal_ids=['9f03f05a-64ab-3459-a65e-8da2ea5d13c5'])
    """
    if signal:
        return signal.revoke_signal(signal_ids, is_all)
    return
