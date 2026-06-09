"""持仓接口 — 持仓查询、在途单、损益、金融指标"""

pos = None


# ── 持仓公共字段说明 ──────────────────────────────────────────────────
# get_position / get_ord_position / roll_pos 返回值中通用:
#   symbol(str): 合约代码
#   frozenQuantity(float): 冻结量
#   quantity(float): 总持仓量
#   quantityTd(float): 今日持仓量(仅逐笔)
#   posSide(int): 头寸方向 0-中性 1-多方向 2-空方向
#   profit(float): 损益
#   value(float): 估值
#   costPrice(float): 敞口价格
#   unRealizedPL(float): 未交割浮动损益
#   realizedPL(float): 已交割损益
#   washAmount(float): 持仓成本
#   time(int): 头寸时间(毫秒)
#   avgPrice(float): 均价(仅逐笔)
#   id(int): 唯一编号(仅逐笔)
# ─────────────────────────────────────────────────────────────────────


def get_position(symbol: str, pos_side: int = 0) -> dict:
    """获取合约持仓信息。

    Args:
        symbol: 合约代码
        pos_side: 头寸方向 0-中性(默认) 1-多方向 2-空方向

    Returns:
        dict: 持仓信息(字段见模块顶部公共字段说明)

    Example:
        >>> pos.get_position("EURUSDSP", 0)
    """
    return pos.get_position(symbol, pos_side)


def get_position_onroad(symbol: str, effect: int = 0, pos_side: int = 0) -> dict:
    """获取合约持仓和在途单量。

    Args:
        symbol: 合约代码
        effect: 开平仓类型 0-中性(默认) 1-开仓 2-平仓
        pos_side: 持仓方向 0-中性(默认) 1-多方向 2-空方向

    Returns:
        dict:
            - onroad_b(float): 买方向在途单量
            - onroad_s(float): 卖方向在途单量
            - quantity(float): 总持仓

    Example:
        >>> pos.get_position_onroad("EURUSDSP", 0, 0)
    """
    return pos.get_position_onroad(symbol, effect, pos_side)


def get_position_quantity_onroad(symbol: str) -> dict:
    """获取合约持仓和在途单量(多空方向合并)。

    Args:
        symbol: 合约代码

    Returns:
        dict:
            - onroad_b(float): 买方向在途单量
            - onroad_s(float): 卖方向在途单量
            - quantity(float): 总持仓(多方向为正，空方向为负)

    Example:
        >>> pos.get_position_quantity_onroad("au2412")
    """
    return pos.get_position_quantity_onroad(symbol)


def get_order_onroad_amt(symbol: str, side: str) -> float:
    """获取合约指定买卖方向的在途单量。

    Args:
        symbol: 合约代码
        side: 买卖方向 B-买 S-卖

    Returns:
        float: 在途单数量

    Example:
        >>> pos.get_order_onroad_amt("au2412", "B")
    """
    return pos.get_order_onroad_amt(symbol, side)


def get_today_quantity_by_symbol(symbol: str) -> float:
    """查询合约当日成交量。

    Args:
        symbol: 合约代码

    Returns:
        float: 当日成交量
    """
    return pos.get_today_quantity_by_symbol(symbol)


def get_ord_position(order_id=None, symbol=None) -> list:
    """获取逐笔持仓信息。不传参数则返回全部未平仓持仓。

    Args:
        order_id: 开仓订单ID
        symbol: 合约代码

    Returns:
        list[dict]: 逐笔持仓列表(字段见模块顶部公共字段说明)

    Example:
        >>> pos.get_ord_position(order_id=216868121676222464)
    """
    return pos.get_ord_position(order_id, symbol)


def roll_pos(symbol: str) -> list:
    """对合约进行展期操作(移到下一个交易日)。

    Args:
        symbol: 合约代码

    Returns:
        list[dict]: 展期后持仓列表(字段见模块顶部公共字段说明)

    Example:
        >>> pos.roll_pos("EURUSDSP")
    """
    return pos.roll_pos(symbol)


def get_indicators(symbols=None) -> dict:
    """查询金融指标(久期、凸性、DV01等)。

    Args:
        symbols: 合约编码(str或list)。不传则查询策略维度。

    Returns:
        dict: 指标内容:
            单合约-固息债/贴现/利随本清:
                - duration(float): 久期
                - mod_duration(float): 修正久期
                - convexity(float): 凸性
                - dv01(float): DV01
            单合约-浮息债:
                - spread_duration(float): 利差久期
                - spread_convexity(float): 利差凸性
                - ir_duration(float): 利率久期
                - ir_convexity(float): 利率凸性
                - dv01(float): DV01
            投组维度:
                - duration(float): 久期(浮息债为利差久期)
                - convexity(float): 凸性(浮息债为利差凸性)
                - dv01(float): DV01

    Example:
        >>> pos.get_indicators('160016_T+1')
    """
    return pos.get_indicators(symbols)


def get_bond_loss_profit(dimension: int, symbol=None) -> dict:
    """查询现券损益。

    Args:
        dimension: 查询维度 1-合约 2-策略
        symbol: 合约代码(维度为2时不需要)

    Returns:
        dict:
            - unRealizedPL(float): 未实现损益
            - realizedPL(float): 已实现损益
            - profit(float): 总损益

    Example:
        >>> pos.get_bond_loss_profit(dimension=1, symbol='160017_T+1')
    """
    return pos.get_bond_loss_profit(dimension=dimension, symbol=symbol)


def get_xswap_loss_profit(dimension: int, symbol=None) -> dict:
    """查询利率互换损益。

    Args:
        dimension: 查询维度 1-利率指标 2-策略
        symbol: 利率指标代码(如 FR007, Shibor3M)，维度为2时不需要

    Returns:
        dict:
            - unRealizedPL(float): 未实现损益
            - realizedPL(float): 已实现损益
            - profit(float): 总损益

    Example:
        >>> pos.get_xswap_loss_profit(dimension=1, symbol='FR007')
    """
    return pos.get_xswap_loss_profit(dimension=dimension, symbol=symbol)


def get_dv01(dimension: int, symbol=None) -> dict:
    """查询DV01(利率互换基准指标)。

    Args:
        dimension: 合约类型 0-基准指标 1-策略
        symbol: 基准指标代码(如 FR007)

    Returns:
        dict: 按维度和symbol组合返回:
            - unit_dv01(dict): {期限点(str): dv01(float)}
            - tactics_sublevel_dv01_and_all_dv01 或 index_{symbol}_sublevel_dv01_and_all_dv01(dict):
                {期限点(str): dv01(float), 'ALL': 总值}

    Example:
        >>> pos.get_dv01(dimension=0, symbol='FR007')
    """
    return pos.get_irs_dv01(dimension, symbol)


def get_folder_position(folder=None, pair=None) -> list:
    """根据账户、货币对获取头寸信息。

    Args:
        folder: 账户
        pair: 货币对

    Returns:
        list[dict]: 头寸列表
    """
    return pos.get_folder_position(folder, pair)


def get_folder_trade(folder=None, tradeDateStart=None, tradeDateEnd=None) -> list:
    """根据账户、日期范围获取成交信息。

    Args:
        folder: 账户
        tradeDateStart: 开始日期(YYYYMMDD)
        tradeDateEnd: 结束日期(YYYYMMDD)

    Returns:
        list[dict]: 成交列表
    """
    return pos.get_folder_trade(folder, tradeDateStart, tradeDateEnd)


def get_day_pnl() -> float:
    """获取当日损益(贵金属接口)。

    Returns:
        float: 当日损益
    """
    return pos.get_day_pnl()


def get_fx_profit() -> dict:
    """查询当前外汇策略的已实现与浮动损益。

    Returns:
        dict:
            - unRealizedPL(float): 未实现损益
            - realizedPL(float): 已实现损益
    """
    return pos.get_fx_profit()


def get_rpm_position_quantity(book=None, folder=None) -> list:
    """查询当前组合账户的积存金。

    Args:
        book: 组合
        folder: 账户

    Returns:
        list: 积存金列表
    """
    return pos.get_rpm_position_quantity(book, folder)


def get_pm_profit() -> dict:
    """查询当前贵金属策略已实现损益。

    Returns:
        dict:
            - unRealizedPL(float): 未实现损益
            - realizedPL(float): 已实现损益
    """
    return pos.get_pm_profit()
