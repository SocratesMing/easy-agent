
funds_info = None

def get_funds():
    """
    描述:
        查询当前资金账户详情
        
    返回:dict 资金账户对象
        - funds: 资金信息
            - net: 净值
            - freeze_amt: 冻结金额
            - commission: 佣金
            - ccy: 结算货币
            - realizedPL: 实际损益
            - init_money: 初始化资金
        
        - net_pre_freeze:[] 净值模式的预占
        - oc_pre_freeze:[] 开平仓模式的预占
        - net_freeze:[] 净值模式的实占
        - oc_freeze:[] 开平仓模式的实占

    示例:
        >>>  funds.get_funds()
        {'funds': {'net': 10000000, 'freeze_amt': 0, 'commission': 0, 'ccy': 'USD', 'realizedPL': 0, 'init_money': 10000000},
        'net_pre_freeze': [], 'oc_pre_freeze': [], 'net_freeze': [], 'oc_freeze': []}
    """
    return funds_info.get_funds()


def get_market_info(channel_code, symbol) -> dict:
    """
    描述:
        获取市场信息

    参数:
        channel_code (string): 渠道
        symbol (string): 合约唯一代码

    Returns:
        dict: 行情对象
    """
    return funds_info.get_market_info(channel_code, symbol)
