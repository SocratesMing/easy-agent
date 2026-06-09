"""行情数据接口 — 实时行情、Bar数据、债券曲线、贴现因子等"""

md = None


# ── 实时行情 ──────────────────────────────────────────────────────────


def get_price(symbol: str, type_=None, source=None, fields=None) -> list:
    """获取最新行情快照。

    Args:
        symbol: 合约唯一代码，如 "EURUSDSP"
        type_: 数据类型，如 "ODM_DEPTH"
        source: 行情来源/渠道，如 "CFETS_LC"、"UBS_HO"
        fields: 指定返回字段列表，如 ["bestBid", "bestAsk"]

    Returns:
        list[dict]: 行情快照列表，主要字段:
            - status(str): 价格状态 1-正常 2-异常
            - source(str): 数据渠道
            - type(str): 数据类型
            - symbol(str): 合约代码
            - time(int): 时间戳(毫秒)
            - bestBid(float): 最优买价
            - bestBidAmt(float): 最优买价量
            - bestAsk(float): 最优卖价
            - bestAskAmt(float): 最优卖价量
            - asks(list[float]): 卖盘价格(asks[0]=卖一)
            - ask_vols(list[float]): 卖盘量
            - bids(list[float]): 买盘价格(bids[0]=买一)
            - bid_vols(list[float]): 买盘量
            - limitUp(float): 涨停价
            - limitDown(float): 跌停价

    Example:
        >>> md.get_price("EURUSDSP")
        >>> md.get_price("EURUSDSP", source="CFETS_LC")
    """
    return md.get_price(symbol, type_, source, fields)


# ── Bar 数据 ──────────────────────────────────────────────────────────


def query_bars(symbol: str, type_: str, source: str, count: int,
               fields=None, df=False) -> list:
    """获取N根Bar(K线)数据。

    Args:
        symbol: 合约唯一代码
        type_: 数据类型，格式为 "{频率}_BAR_{行情类型}"，如 "5N_BAR_ODM_DEPTH"
        source: 行情来源/渠道
        count: Bar数量
        fields: 指定返回字段列表
        df: 是否返回DataFrame格式，默认False返回list

    Returns:
        list[dict]: Bar数据列表，主要字段:
            - source(str): 数据渠道
            - type(str): 数据类型
            - frequency(str): 频率 1N/5N/15N/30N/1H/1D/1W/1M
            - symbol(str): 合约代码
            - time(int): 时间戳(毫秒)
            - tradeDate(int): 交易日期(YYYYMMDDHHmmss)
            - open(float): 开盘价
            - close(float): 收盘价
            - high(float): 最高价
            - low(float): 最低价
            - trade_volume(float): 成交量
            - trade_amt(float): 成交额
            - start(int): Bar开始时间
            - end(int): Bar结束时间

    Example:
        >>> md.query_bars("EURUSDSP", type_="5N_BAR_ODM_DEPTH", source="CFETS_LC", count=10)
    """
    return md.query_bars(symbol, type_, source, count, fields, df)


def query_bars_pro(symbol: str, type_: str, source: str, count: int,
                   fields=None, data_type: int = 1) -> list:
    """获取N根Bar数据(增强版，支持多种返回格式)。

    Args:
        symbol: 合约唯一代码
        type_: 数据类型，如 "5N_BAR_ODM_DEPTH"
        source: 行情来源/渠道
        count: Bar数量
        fields: 指定返回字段列表
        data_type: 返回格式 0-pandas 1-numpy 2-dict

    Returns:
        list[dict]: 同 query_bars

    Example:
        >>> md.query_bars_pro("EURUSDSP", type_="5N_BAR_ODM_DEPTH", source="CFETS_LC", count=10, data_type=2)
    """
    return md.query_bars_pro(symbol, type_, source, count, fields, data_type)


# ── 行情订阅 ──────────────────────────────────────────────────────────


def sub_scribe(channel_code: str, symbol: str, type: str):
    """订阅渠道行情(从缓存切换为实时下发)。

    Args:
        channel_code: 渠道代码，如 "UBS_HO"
        symbol: 合约唯一代码
        type: 行情类型
    """
    return md.sub_scribe(channel_code, symbol, type)


def un_sub_scribe(channel_code: str, symbol: str, type: str):
    """取消订阅渠道行情(从实时下发切换为缓存)。

    Args:
        channel_code: 渠道代码
        symbol: 合约唯一代码
        type: 行情类型
    """
    return md.un_sub_scribe(channel_code, symbol, type)


# ── 债券定价 & 互算 ──────────────────────────────────────────────────


def in_active_bond_pricing(symbol: str) -> tuple:
    """非活跃券定价。

    Args:
        symbol: 债券编码，如 "160017_T+1"

    Returns:
        tuple: (netPrice, dirtyPrice, yieldToMaturity)
            - netPrice(float): 净价
            - dirtyPrice(float): 全价
            - yieldToMaturity(float): 收益率

    Example:
        >>> net, dirty, ytm = md.in_active_bond_pricing('160017_T+1')
    """
    return md.in_active_bond_pricing(symbol)


def get_bond_mutual_calculation(symbol: str, netPrice=None, ytm=None) -> dict:
    """债券净价/收益率互算。输入净价或到期收益率，返回净价、全价、到期收益率。

    Args:
        symbol: 债券编码(必填)
        netPrice: 净价(与ytm二选一)
        ytm: 到期收益率(与netPrice二选一)

    Returns:
        dict:
            - netprice(float): 净价
            - fullprice(float): 全价
            - ytm(float): 到期收益率

    Example:
        >>> md.get_bond_mutual_calculation('160017_T+1', netPrice=100.5)
    """
    return md.get_bond_mutual_calculation(symbol, netPrice, ytm)


# ── 利率互换(IRS) ────────────────────────────────────────────────────


def get_irs_df(code: str, date_list: list) -> list:
    """查询贴现因子。

    Args:
        code: 合约编码，如 "FR007"
        date_list: 日期列表(YYYYMMDD)，五年以内

    Returns:
        list: 贴现因子列表

    Example:
        >>> md.get_irs_df('FR007', [20220608, 20220701])
    """
    return md.get_irs_df(code, date_list)


def get_xswap_discount_curve(code: str) -> dict:
    """查询利率互换贴现因子曲线。

    Args:
        code: 合约编码，如 "FR007"

    Returns:
        dict:
            - curveTenor(list[str]): 关键期限点
            - discountCurve(list[float]): 贴现因子曲线

    Example:
        >>> md.get_xswap_discount_curve(code='FR007')
    """
    return md.get_xswap_discount_curve(code=code)


def get_irs_fixing_curve(code: str, start_date: int, end_date=None) -> dict:
    """查询定盘利率。

    Args:
        code: 合约编码，如 "FR007"
        start_date: 起始日期(YYYYMMDD)
        end_date: 结束日期(YYYYMMDD)，默认当前时间

    Returns:
        dict: 按日期分组，每组主要字段:
            - mdType(str): 债券类型 0-Shibor K-回购定盘
            - securityType(str): 债券品种 ShiborCn / FR001 / FR007 / FR014
            - tenor(str): 债券期限 O/N/1W/2W/1M/3M/6M/9M/1Y
            - price(float): 价格
            - shiborBp(str): 涨跌幅(Shibor专有，单位BP)
            - benchmarkEffectiveDate(str): 生成日期(yyyyMMdd)

    Example:
        >>> md.get_irs_fixing_curve('FR007', 20220608, 20220701)
    """
    return md.get_irs_fixing_curve(code, start_date, end_date)


def get_xswap_curve(code: str) -> dict:
    """查询利率互换曲线。

    Args:
        code: 合约编码，如 "FR007_1Y"

    Returns:
        dict:
            - curveTenor(list[str]): 关键期限
            - rateCurve(list[float]): 原始曲线
            - curveDates(list[str]): 关键期限点对应日期
            - spotRateCurve(list[float]): 即期利率曲线
            - positiveSpotRateCurve(list[float]): 正即期利率曲线
            - negativeSpotRateCurve(list[float]): 负即期利率曲线

    Example:
        >>> md.get_xswap_curve(code='FR007_1Y')
    """
    return md.get_xswap_curve(code=code)


# ── 债券收益率曲线 ────────────────────────────────────────────────────


def get_bond_yield_curve(curve_type: int, bond_type: str,
                         query_type: int = 0, key_tenor=None) -> dict:
    """查询债券收益率曲线。

    Args:
        curve_type: 曲线类型 1-自定义曲线 2-中债曲线
        bond_type: 债券类型 CDB-国开行 GB-国债 EIBC-进出口行 ADBC-农发行
            自定义曲线加 _HW 表示 Hull-White 算法，如 GK_HW；不加则用 Hermite 算法
        query_type: 查询类型 0-全部 1-即期 2-远期 3-到期
        key_tenor: 关键期限点(float list)

    Returns:
        dict:
            - spot(dict): 即期曲线 {期限点: 收益率}
            - fwd(dict): 远期曲线 {期限点: 收益率}
            - maturity(dict): 到期曲线 {期限点: 收益率}

    Example:
        >>> md.get_bond_yield_curve(curve_type=1, bond_type='GK', query_type=0)
    """
    return md.get_bond_yield_curve_info(curve_type=curve_type, bond_type=bond_type,
                                        query_type=query_type, key_tenor=key_tenor)


def get_info_bond_curve(curve_num, curve_type, trade_dt=None,
                        key_tenor=None, start_date=None, end_date=None):
    """查询中债登收益率曲线。

    Args:
        curve_num: 曲线编码
        curve_type: 曲线类型 SPOTCURVE-即期 MATCURVE-到期
        trade_dt: 交易日期列表(YYYYMMDD)
        key_tenor: 关键期限点(float list)
        start_date: 交易开始日期(YYYYMMDD)
        end_date: 交易结束日期(YYYYMMDD)

    Example:
        >>> md.get_info_bond_curve(curve_num=1042, curve_type="SPOTCURVE", trade_dt=["20250725"])
    """
    return md.get_info_bond_curve(curve_num=curve_num, curve_type=curve_type,
                                  trade_dt=trade_dt, key_tenor=key_tenor,
                                  start_date=start_date, end_date=end_date)


def cal_bond_yield_curve_indicator(code: str, start_date: int, end_date: int,
                                   key_tenor: list) -> dict:
    """查询关键期限点指定时间段的曲线数据。

    Args:
        code: 合约编码
        start_date: 起始日期(YYYYMMDD)
        end_date: 结束日期(YYYYMMDD)
        key_tenor: 关键期限点，如 ['1D','1W','3M','6M','9M','1Y','2Y','3Y','4Y','5Y']

    Returns:
        dict: 按日期分组，每组 {期限: 利率}

    Example:
        >>> md.cal_bond_yield_curve_indicator('FR007', 20220608, 20220701, ['1D','1W'])
    """
    return md.cal_bond_yield_curve_indicator(code, start_date, end_date, key_tenor)


def get_bond_residual_curve(symbol, curveType, frequency='1N', num=-1) -> dict:
    """获取债券残差曲线。

    Args:
        symbol: 债券编码
        curveType: 曲线类型 0-全部 1-GB 2-GB_HW 3-GB_NS
        frequency: 周期 1N/5N/15N/30N/1H/1D/1W/1M
        num: 数量，默认-1(全部)

    Returns:
        dict:
            - curve(np.array): 残差数据(symbol, time, price_residual, yield_residual)
            - price_residual_avg(float): 全价残差均值
            - price_residual_std(float): 全价残差标准差
            - price_residual_var(float): 全价残差方差
            - yield_residual_avg(float): 收益率残差均值
            - yield_residual_std(float): 收益率残差标准差
            - yield_residual_var(float): 收益率残差方差

    Example:
        >>> md.get_bond_residual_curve('160017_T+1', curveType=0, frequency='1N')
    """
    return md.get_residual_curve(symbol, curveType, frequency, num)


def get_bond_yield_curve_slope(curve_type: int, bond_type: str, query_type: int,
                               key_tenor_a: float, key_tenor_b: float) -> float:
    """计算曲线斜率。

    Args:
        curve_type: 曲线类型 1-自定义 2-中债
        bond_type: 债券类型(同 get_bond_yield_curve)
        query_type: 查询类型 0-全部 1-即期 2-远期 3-到期
        key_tenor_a: 期限点a
        key_tenor_b: 期限点b

    Returns:
        float: 斜率

    Example:
        >>> md.get_bond_yield_curve_slope(curve_type=1, bond_type='GK', query_type=0,
        ...                               key_tenor_a=0.09, key_tenor_b=1.0)
    """
    return md.get_bond_yield_curve_slope(curve_type, bond_type, query_type,
                                         key_tenor_a, key_tenor_b)


# ── 债券信息查询 ──────────────────────────────────────────────────────


def get_active_bond(channel_code: str, bond_code: str,
                    start_date: int, end_date: int) -> list:
    """查询活跃券切券信息。

    Args:
        channel_code: 渠道编码，如 "X-BOND_HO"
        bond_code: 虚拟合约编码，如 "100001#1M"
        start_date: 开始时间(YYYYMMDD)
        end_date: 结束时间(YYYYMMDD)

    Returns:
        list[dict]: 主要字段:
            - virtualCode(str): 虚拟合约编码
            - contractCode(str): 合约编码
            - date(int): 日期
            - channel(str): 渠道
            - type(str): 产品类型
            - time(int): 切券日期

    Example:
        >>> md.get_active_bond('X-BOND_HO', '100001#1M', 20221001, 20231001)
    """
    return md.get_active_bond(channel_code, bond_code, start_date, end_date)


def get_bond_three_factors_model(bond_type: str, term: str = '10') -> dict:
    """查询现券三因子(水平/斜率/曲率)。

    Args:
        bond_type: 债券类型 CDB/GB/EIBC/ADBC
        term: 最长期限，可选 '0.17'/'0.25'/'0.5'/'0.75'/'1'/'2'/'3'/'5'/'7'/'10'/'15'/'20'/'30'/'40'/'50'

    Returns:
        dict: 三个因子各有 factors(list[float])/avg/std/var:
            - level_factor(dict): 水平因子
            - slope_factor(dict): 斜率因子
            - curvature_factor(dict): 曲率因子

    Example:
        >>> md.get_bond_three_factors_model('GK', term='1')
    """
    return md.get_bond_three_factors_model(bond_type, term)


def get_bonds_info(symbol: str) -> dict:
    """查询债券定义信息。

    Args:
        symbol: 合约代码
    """
    return md.get_bonds_info(symbol)


def get_contract_price_info(symbol: str) -> dict:
    """获取合约每手金额管理数据。

    Args:
        symbol: 合约代码

    Returns:
        dict:
            - contractCode(str): 合约代码
            - unitPrice(float): 每手金额
            - unitMaxLot(float): 最大手数
    """
    return md.get_contract_price_info(symbol)


def get_bonds_rating(symbol: str) -> dict:
    """查询债券评级信息。

    Args:
        symbol: 合约代码
    """
    return md.get_bonds_rating(symbol)


def check_issue(issue_code: str) -> bool:
    """判断发行人是否在跟随范围内。

    Args:
        issue_code: 发行人代码

    Returns:
        bool: 是否在范围内
    """
    return md.check_issue(issue_code)


def get_trade_bonds_info(symbol: str) -> dict:
    """查询可成交债券信息。

    Args:
        symbol: 合约代码
    """
    return md.get_trade_bonds_info(symbol)


def get_market_info(channel_code: str, symbol: str) -> dict:
    """查询交易市场规则。

    Args:
        channel_code: 渠道
        symbol: 合约
    """
    return md.get_market_info(channel_code, symbol)


def get_bond_basis_price(bond_code: str, start_date=None, end_date=None) -> list:
    """查询中债估值。

    Args:
        bond_code: 债券代码
        start_date: 开始时间(YYYYMMDD)
        end_date: 结束时间(YYYYMMDD)

    Note:
        仅支持2025年4月26日之后的数据，开始与结束时间不能跨年份。
    """
    return md.get_bond_basis_price(bond_code, start_date, end_date)


def get_issuer_liquidity_analysis(issuer: str, start_date=None, end_date=None) -> list:
    """查询主体流动性评分数据。

    Args:
        issuer: 主体名称
        start_date: 开始时间(YYYY-MM-DD)
        end_date: 结束时间(YYYY-MM-DD)
    """
    return md.get_issuer_liquidity_analysis(issuer, start_date, end_date)
