date = None


def get_sys_time(time_format="%Y%m%d%H%M%S") -> str:
    """
    描述:
        获取当前的系统时间

    返回 str:
        time: 返回系统时间 如: 20190128121103

    示例:
        >>> date.get_sys_time()
        "20190128121103"
    """
    return date.get_sys_time(time_format)


def get_bus_day(symbol=None):
    """
    描述:
        获取某个合约的业务时间

    参数:
        symbol(string): 业务类型，输入合约编码

    返回 int:
        - 日期(int): 返回业务日期 如: 20190121

    示例：
        >>> date.get_bus_day("EURUSDSP")
    """
    return date.get_bus_day(symbol)


def day_offset(holidayCode, nums, ndate, period, days):
    """获取往后按工作日往后延多个工作日

    Keyword Arguments:
        holidayCode([String]):假日Code，根据SDS配置Code（例:["CNY"]）
        nums(Number):偏移周期数，支持任何整数
        ndate(Number):参考时间，日期格式只支持YYYYMMDD
        period(String):偏移周期单位，枚举:日:D,月:M,年:Y
        days(String):日期类型，交易日:TRADING,自然日:NATURAL

    返回:
        日期(int): 返回业务日期 如: 20190228
    使用方法：
        >>> date.day_offset(['CNY'], 5, 20190223, 'D', 'TRADING')
    """
    return date.day_offset(holidayCode, nums, ndate, period, days)


def day_check(holidayCode, days, ndate):
    """校验日期是否为CNY假日配置内的工作日

    Keyword Arguments:
        holidayCode([String]):假日Code，根据SDS配置Code（例:["CNY"]）
        days(String):日期类型，交易日:TRADING,节假日:HOLIDAY,自然日:NATURAL
        ndate(Number):参考时间，日期格式只支持YYYYMMDD

    返回:
        bool：是否为对应的工作类型，是则返回True，否则返回False
    使用方法：
        >>> date.day_check(['CNY'], 'TRADING', 20190223)
    """
    return date.day_check(holidayCode, days, ndate)


def check_symbol_isdeal(symbol, trading_period):
    """校验当前时间是否为策略规定的时间段内

    Keyword Arguments:
        symbol(String):合约
        trading_period(String):时间段

    返回:
        bool：是否为在工作时间段内，是则返回True，否则返回False
    使用方法：
        >>> date.check_symbol_isdeal('au2412', '093000-113000,130000-153000,210000-240000,000000-020000')
    """
    return date.check_symbol_isdeal(symbol, trading_period)


def get_bus_time(channel=None):
    """
    描述:
        获取某个渠道的交易时段信息

    参数:
        channel(string): 交易渠道

    返回 list:
        - code(string):交易渠道
        - type(string):类型 [DAY:全天 AM:上午 PM:下午 VESP:夜间]
        - name(string):英文名称
        - localName(string):本地名称
        - startTime(string):开始时间 (格式 09:00:00)
        - endTime(string):结束时间 (格式 11:30:00)
        - adjustType(string):调整类型 [A:添加 D:排除]
        - timeStamp(string):修改时间
        - comments(string):描述
        - marketStatus(string):市场状态 [OC:开始/闭市 OR:开始/休市 S:停市 PR:暂停/恢复交易]
        - tradePeriodGroup(string):日期类型 [C:通用 S:特殊]
        - tradePeriodTime(string):适用时间 (1,2,3,4,5 以数字表示周几多个以,区分)

    示例：
        >>> date.get_bus_time("UBS_HO")
    """
    return date.get_bus_time(channel)


def get_open_close_market(symbol, market, query_time: int = None):
    """
    描述:
        获取查询时间所在的开闭市时间段

    参数:
        - symbol(string):合约编码，输入合约编码
        - market(string):交易市场，输入交易市场编码，例：UBS
        - query_time(int):查询时间，输入要查询的时间戳，毫秒级，不传则默认用当前行情时间

    返回:
        开市时间，闭市时间，返回数组

    示例：
        >>> date.get_open_close_market("EURUSDSP", "UBS")
        >>> date.get_open_close_market("EURUSDSP", "UBS", 20221201)
    """
    return date.get_open_close_market(symbol, market, query_time)


def get_timestamp() -> int:
    """
    描述:
        返回当前策略时间的毫秒级时间戳

    返回 int:
        返回当前策略时间的毫秒级时间戳

    示例：
        >>> date.get_timestamp()
            1705632663000
    """
    return date.get_timestamp()
