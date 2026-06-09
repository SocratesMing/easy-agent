"""市场数据对象说明 — 行情数据源与渠道对照表"""


class MarketDataList:
    """Tick深度行情数据源:
        - UBS深度行情: 交易所=UBS, 渠道=UBS_HO, 结构=DepthQuote, 类型=FXSPOT
        - JPMC深度行情: 交易所=JPMC, 渠道=JPMC_HO, 结构=DepthQuote, 类型=FXSPOT
        - 外汇交易中心ODM: 交易所=CFETS, 渠道=CFETS-ODM_HO, 结构=DepthQuote, 类型=FXSPOT
        - 外汇交易中心QDM(全量): 交易所=CFETS, 渠道=CFETS-QDM_FULL_HO, 结构=DepthQuote, 类型=FXSPOT
        - 外汇交易中心QDM(扫单): 交易所=CFETS, 渠道=CFETS-QDM_SWEEP_HO, 结构=DepthQuote, 类型=FXSPOT
    """
    pass


class BarDataList:
    """Bar数据源(频率替换?号):
        - UBS深度行情: 交易所=UBS, 渠道=UBS_HO, 类型=?_BAR_DEPTH, 频率=1N/5N/15N/30N/1H/1D/1W/1M
        - JPMC深度行情: 交易所=JPMC, 渠道=JPMC_HO, 类型=?_BAR_DEPTH, 频率=1N/5N/15N/30N/1H/1D/1W/1M
        - 外汇交易中心ODM: 交易所=CFETS, 渠道=CFETS-ODM_HO, 类型=?_BAR_DEPTH, 频率=1N/5N/15N/30N/1H/1D/1W/1M
        - 外汇交易中心QDM(全量): 交易所=CFETS, 渠道=CFETS-QDM_FULL_HO, 类型=?_BAR_DEPTH, 频率=1N/5N/15N/30N/1H/1D/1W/1M
        - 外汇交易中心QDM(扫单): 交易所=CFETS, 渠道=CFETS-QDM_SWEEP_HO, 类型=?_BAR_DEPTH, 频率=1N/5N/15N/30N/1H/1D/1W/1M
    """
    pass
