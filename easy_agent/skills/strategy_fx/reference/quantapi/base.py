"""基础接口 — 合约信息查询"""

base = None


def get_contract(code: str) -> dict:
    """获取合约信息。

    Args:
        code: 合约唯一代码（合约名），如 "EURUSDSP"

    Returns:
        dict: 合约信息，主要字段:
            - code(str): 合约代码
            - contractType(str): 合约类型 B-基础 D-基差 T-期差 S-连续 M-月份 N-非标准
            - sites(str): 交易主体
            - productBroad(str): 产品大类
            - products(str): 产品小类
            - contractMultiplier(float): 合约乘数
            - tenor(str): 期限
            - tenorGroup(str): 组合期限
            - lastDate(str): 最后交易日
            - dealType(str): 交易品种
            - valueDateRule(str): 起息日规则
            - market(str): 交易市场
            - quoteCurrency(str): 报价货币
            - localName(str): 本地名称
            - name(str): 英文名称
            - quoteUnit(str): 报价单位
            - startDate(str): 开始交易日
            - noDecimal(int): 报价有效位数
            - status(str): 合约状态

    Example:
        >>> base.get_contract("EURUSDSP")
    """
    return base.get_contract(code)


def get_bond_cash_flow(bond_code: str) -> list:
    """获取债券现金流信息。

    Args:
        bond_code: 债券编码

    Returns:
        list[dict]: 现金流列表，每条主要字段:
            - cashflowNo(int): 现金流编码
            - currency(str): 货币
            - cashflowType(str): 现金流类型 P-Premium R-Principal I-Interest F-Fee
            - cashflowStatus(str): 现金流状态 I-Init F-Fixing
            - dealType(str): 收付方向 P-Pay R-Receive
            - cashflowDate(int): 现金流日期(YYYYMMDD)
            - amount(float): 金额
            - notional(float): 本金
            - paymentDate(int): 付款日期(YYYYMMDD)
            - exDivideneDate(int): 除权日期(YYYYMMDD)
            - startDate(int): 开始时间(YYYYMMDD)
            - endDate(int): 结束时间(YYYYMMDD)
            - period(int): 周期
            - phase(str): 阶段 B-期初 M-期中 E-期末
            - basis(str): 计息基础

    Example:
        >>> base.get_bond_cash_flow('160017')
    """
    return base.get_bond_cash_flow(bond_code)


def get_bond_info(symbol: str) -> dict:
    """查询债券基本信息。

    Args:
        symbol: 债券编码，如 "160017_T+1"

    Returns:
        dict: 债券信息，主要字段:
            - bondCode(str): 债券编码
            - couponFrequency(str): 付息频率 1D/1W/2W/1M/3M/6M/1Y/MT(利随本清)/N(无)
            - fixingRate(float): 票面利率
            - maturityDate(int): 到期日(YYYYMMDD)
            - valueDate(int): 起息日(YYYYMMDD)

    Example:
        >>> base.get_bond_info('160017_T+1')
    """
    if symbol is not None:
        return base.get_bond_info(symbol)
    else:
        raise Exception("symbol cannot be None")
