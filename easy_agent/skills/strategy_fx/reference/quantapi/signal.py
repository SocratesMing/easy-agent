signal = None


class TypeEnum:
    '''
    信号状态
        +---------------------+---------------------+
        |       信号状态      |      枚举值         |
        |                     |                     |
        +=====================+=====================+
        |         有效        |        U            |
        +---------------------+---------------------+
        |         失效        |        F            |
        +---------------------+---------------------+
    '''
    VALID = "V"  # 有效
    INVALID = "I"  # 无效


class Signal:
    """
    信号字段实体:
        +---------------------+---------------------+---------------------+
        |       字段代码      |       字段名称      |   字段类型          |
        |                     |                     |                     |
        +=====================+=====================+=====================+
        |       signal_id     |       信号id号      |     str             |
        +---------------------+---------------------+---------------------+
        |       symbol        |       合约标的      |     str             |
        +---------------------+---------------------+---------------------+
        |       channel       |       渠道          |      str            |
        +---------------------+---------------------+---------------------+
        |       frequency     |       频率          |      str            |
        +---------------------+---------------------+---------------------+
        |       side          |       方向          |      str            |
        +---------------------+---------------------+---------------------+
        |      monitor        |      开仓监控       |      bool           |
        +---------------------+---------------------+---------------------+
        |       price        |      挂单价         |      float           |
        +---------------------+---------------------+---------------------+
        |       effect        |      开平仓类型     |      int             |
        +---------------------+---------------------+---------------------+
        |       pos_type      |      下单模式       |      int             |
        +---------------------+---------------------+---------------------+
        |   create_time       |      创建时间       |    int              |
        +---------------------+---------------------+---------------------+
        |   update_time       |      更新时间       |     int              |
        +---------------------+---------------------+---------------------+
        |    status           |      状态            |    str              |
        +---------------------+---------------------+---------------------+
        |    param1           |   自定义参数         |    str              |
        +---------------------+---------------------+---------------------+
        |    param2           |   自定义参数2         |    str             |
        +---------------------+---------------------+---------------------+
        |    bankId           |      所属机构        |    str              |
        +---------------------+---------------------+---------------------+
        |    userId           |      用户ID          |    bool             |
        +---------------------+---------------------+---------------------+
        |    is_closed        |      是否平仓        |    int              |
        +---------------------+---------------------+---------------------+
        |    runId            |      运行ID         |    str              |
        +---------------------+---------------------+---------------------+
        |    runName           |      运行名称       |    str             |
        +---------------------+---------------------+---------------------+
    """
    pass


def to_signal(signal_list) -> str:
    """
    描述
        发信号的接口

    参数
        - signal_list(List<Signal>): 信号数组对象
        信号对象包含以下字段:
        - signal_id(str): 开仓信号id
        - symbol (str): 合约标的
        - frequency (str): 时间周期, 格式例如 5N
        - channel (str): 渠道.
        - side (str): 买卖方向   B: 买, S: 卖.
        - effect (int): 开平仓类型  Defaults to 0-中性 1-开仓 2-平仓
        - price (float): 价格.
        - monitor (bool, optional): 是否监控平仓行为. Defaults to False.
        - pos_type (int, optional): 0(净值模式) 1(逐笔模式)
        - is_closed (str, optional): 开仓属性 1-止损 2-止盈
        - param1 (str): 自定义参数
        - param2 (str): 自定义参数
        - param3 (str): 自定义参数
        - param4 (str): 自定义参数
        - send_chat(bool): 是否发送邮箱

    Return:
        str: 信号唯一ID号
    注意:
       若是逐笔模式,则返回的是开仓信号id, 平仓信号id为开仓信号id + _C

    示例
        >>> id = signal.to_signal([{"symbol": "EURUSDSP", "frequency": "5N", "channel": "UBS_HO", "side": "B", "open_price": 1.2335, "pos_type": 0}])
    """
    if signal:
        return signal.to_signal(signal_list)
    return None


def cancel_signal(signal_ids: list[str], is_all: bool = False):
    """
    描述
        传入指定的信号id组,取消指定的信号

    参数
        -signal_ids: list[str]: 需要取消的策略信号数组
        -is_all (bool, optional): 是否取消所有有效信号,默认不激活. Defaults to False.

    示例
        >>> signal.cancel_signal(signal_ids=['9f03f05a-64ab-3459-a65e-8da2ea5d13c5', 'f5d6bfc0-2858-32d0-9cb4-ac7c9544c9fa'])

    """
    if signal:
        return signal.cancel_signal(signal_ids, is_all)
    return


def update_signal(update_list):
    """
    描述
        更新策略信号内容

    参数
        - update_list(List<Signal>): 信号数组对象
        信号数组对象包含以下字段:
          - signal_id (str): 指定信号id(必传),
          - symbol (str): 合约代码(必传)
          - effect (int): 开平仓类型(必传)
          - is_closed(int): 订单属性(逐笔模式传) 1-止损 2-止盈
          - pirce(float): 价格
          - monitor(bool): 监控标识
          - frequency(str): 时间框架
          - param1(str): 自定义参数
          - param2(str): 自定义参数2
          - send_chat(bool): 是否发送邮箱

    示例
        >>> signal.update_signal([{"signal_id":'9f03f05a-64ab-3459-a65e-8da2ea5d13c5', "price": 0.9875, "is_closed": 1}])
    """
    if signal:
        return signal.update_signal(update_list)
    return


def send_signal(symbol: str,
                frequency: str,
                channel: str = "X-BOND_HO",
                side: str = None,
                price: float = None,
                price_yield: float = None,
                effect: int = 0,
                pos_type: int = 0,
                param1: str = None,
                param2: str = None,
                param3: str = None,
                param4: str = None,
                send_chat: bool = False):
    """
        描述
            发本币信号的接口

        参数
            - symbol (str): 合约标的
            - frequency (str): 时间周期, 格式例如 5N
            - channel (str): 渠道.
            - side (str): 买卖方向   B: 买, S: 卖.
            - effect (int): 开平仓类型  Defaults to 0-中性 1-开仓 2-平仓
            - price (float): 价格.
            - price_yield (float): 收益率.
            - pos_type (int, optional): 0(净值模式) 1(逐笔模式)
            - param1 (str): 自定义参数
            - param2 (str): 自定义参数2
            - param3 (str): 自定义参数3
            - param4 (str): 自定义参数4
            - send_chat(bool): 是否发送邮箱

        Return:
            str: 信号唯一ID号

        示例
            >>> id = signal.send_signal(symbol="180011_T+1", frequency="5N", channel="X-BOND_HO")
    """
    if signal:
        return signal.send_signal(symbol, frequency, channel, side, price, price_yield, effect, pos_type, param1, param2, param3, param4, send_chat)
    return None


def renewal_signal(signal_id: str, symbol: str, frequency: str = None, price: float = None, price_yield: float = None, param1: str = None, param2: str = None, param3: str = None, param4: str = None, send_chat: bool = None):
    """
        描述
            更新策略本币信号内容

        参数
              - signal_id (str): 指定信号id(必传),
              - symbol (str): 债券代码(必传)
              - pirce(float): 价格
              - frequency(str): 时间框架
              - param1(str): 自定义参数
              - param2(str): 自定义参数2
              - param3(str): 自定义参数3
              - param4(str): 自定义参数4
              - send_chat(bool): 是否发送邮箱

        示例
            >>> signal.renewal_signal(signal_id="123456789", symbol="180011_T+1", frequency="10N")
    """
    if signal:
        return signal.renewal_signal(signal_id, symbol, frequency, price, price_yield, param1, param2, param3, param4, send_chat)
    return


def revoke_signal(signal_ids: list, is_all: bool = False):
    """
    描述
        传入指定的信号id,取消指定的信号

    参数
        -signal_id: 需要取消的策略信号组
        -is_all (bool, optional): 是否取消所有有效信号,默认不激活. Defaults to False.

    示例
        >>> signal.revoke_signal(signal_ids=['9f03f05a-64ab-3459-a65e-8da2ea5d13c5'])

    """
    if signal:
        return signal.revoke_signal(signal_ids, is_all)
    return
