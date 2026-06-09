"""资金账户接口 — 查询资金详情、市场信息"""

funds_info = None


def get_funds() -> dict:
    """查询当前资金账户详情。

    Returns:
        dict:
            - funds(dict): 资金信息
                - net(float): 净值
                - freeze_amt(float): 冻结金额
                - commission(float): 佣金
                - ccy(str): 结算货币
                - realizedPL(float): 实际损益
                - init_money(float): 初始化资金
            - net_pre_freeze(list): 净值模式预占
            - oc_pre_freeze(list): 开平仓模式预占
            - net_freeze(list): 净值模式实占
            - oc_freeze(list): 开平仓模式实占

    Example:
        >>> funds.get_funds()
    """
    return funds_info.get_funds()


def get_market_info(channel_code: str, symbol: str) -> dict:
    """获取市场信息。

    Args:
        channel_code: 渠道
        symbol: 合约唯一代码

    Returns:
        dict: 行情对象
    """
    return funds_info.get_market_info(channel_code, symbol)
