"""日期工具接口 — 系统时间、业务日期、工作日偏移、交易时段"""

date = None


def get_sys_time(time_format: str = "%Y%m%d%H%M%S") -> str:
    """获取当前系统时间。

    Args:
        time_format: 时间格式，默认 "%Y%m%d%H%M%S"

    Returns:
        str: 系统时间，如 "20190128121103"

    Example:
        >>> date.get_sys_time()
    """
    return date.get_sys_time(time_format)


def get_bus_day(symbol=None) -> int:
    """获取某个合约的业务日期。

    Args:
        symbol: 合约编码

    Returns:
        int: 业务日期，如 20190121

    Example:
        >>> date.get_bus_day("EURUSDSP")
    """
    return date.get_bus_day(symbol)


def day_offset(holiday_code: list, nums: int, ndate: int, period: str, days: str) -> int:
    """按工作日偏移计算日期。

    Args:
        holiday_code: 假日Code列表，如 ["CNY"]
        nums: 偏移周期数(支持负数)
        ndate: 参考日期(YYYYMMDD)
        period: 偏移周期单位 D-日 M-月 Y-年
        days: 日期类型 TRADING-交易日 NATURAL-自然日

    Returns:
        int: 计算后日期，如 20190228

    Example:
        >>> date.day_offset(['CNY'], 5, 20190223, 'D', 'TRADING')
    """
    return date.day_offset(holiday_code, nums, ndate, period, days)


def day_check(holiday_code: list, days: str, ndate: int) -> bool:
    """校验日期是否为指定类型的工作日。

    Args:
        holiday_code: 假日Code列表，如 ["CNY"]
        days: 日期类型 TRADING-交易日 HOLIDAY-节假日 NATURAL-自然日
        ndate: 参考日期(YYYYMMDD)

    Returns:
        bool: 是否为对应类型

    Example:
        >>> date.day_check(['CNY'], 'TRADING', 20190223)
    """
    return date.day_check(holiday_code, days, ndate)


def check_symbol_isdeal(symbol: str, trading_period: str) -> bool:
    """校验当前时间是否在策略规定的时间段内。

    Args:
        symbol: 合约
        trading_period: 时间段，如 "093000-113000,130000-153000"

    Returns:
        bool: 是否在工作时间段内

    Example:
        >>> date.check_symbol_isdeal('au2412', '093000-113000,130000-153000')
    """
    return date.check_symbol_isdeal(symbol, trading_period)


def get_bus_time(channel=None) -> list:
    """获取渠道的交易时段信息。

    Args:
        channel: 交易渠道

    Returns:
        list[dict]: 交易时段列表，每条含:
            - code(str): 交易渠道
            - type(str): 类型 DAY-全天 AM-上午 PM-下午 VESP-夜间
            - name(str): 英文名称
            - localName(str): 本地名称
            - startTime(str): 开始时间，如 "09:00:00"
            - endTime(str): 结束时间，如 "11:30:00"
            - adjustType(str): 调整类型 A-添加 D-排除
            - marketStatus(str): 市场状态 OC-闭市 OR-休市 S-停市 PR-暂停
            - tradePeriodGroup(str): 日期类型 C-通用 S-特殊
            - tradePeriodTime(str): 适用时间(1-5表示周一到周五)

    Example:
        >>> date.get_bus_time("UBS_HO")
    """
    return date.get_bus_time(channel)


def get_open_close_market(symbol: str, market: str, query_time: int = None):
    """获取查询时间所在的开闭市时间段。

    Args:
        symbol: 合约编码
        market: 交易市场编码，如 "UBS"
        query_time: 查询时间戳(毫秒)，不传则用当前行情时间

    Returns:
        开市时间、闭市时间数组

    Example:
        >>> date.get_open_close_market("EURUSDSP", "UBS")
    """
    return date.get_open_close_market(symbol, market, query_time)


def get_timestamp() -> int:
    """获取当前策略时间的毫秒级时间戳。

    Returns:
        int: 毫秒级时间戳

    Example:
        >>> date.get_timestamp()
    """
    return date.get_timestamp()
