"""quantapi — 策略开发API接口包

模块概览:
    base      - 合约信息查询 (get_contract, get_bond_cash_flow, get_bond_info)
    md        - 行情数据 (get_price, query_bars, query_bars_pro, 债券/IRS曲线)
    deal      - 交易接口 (to_order, cancel_order, get_order, get_orders)
    pos       - 持仓查询 (get_position, get_ord_position, get_indicators, 损益)
    signal    - 信号接口 (to_signal, send_signal, cancel_signal, update_signal)
    param     - 策略参数 (get, matrix, get_num)
    qlog      - 日志 (info, error, debug, warn 及 _f 格式化版本)
    scheduler - 定时任务 (run_daily, run_second)
    funds     - 资金账户 (get_funds, get_market_info)
    event     - 事件回调 (init, onData, onOrder, onTrade, onTime, onSignal 等)
    date      - 日期工具 (get_sys_time, get_bus_day, day_offset, day_check)
    maker     - 做市接口 (to_quote, to_rfq_quote, to_rfq_quote_rej)
    object    - 市场数据对象说明 (MarketDataList, BarDataList)
    enums     - 枚举常量 (ORDER_STATUS_*, TIME_IN_FORCE_*, ORDER_TYPE_*)
"""


class Context:
    """策略上下文抽象类。"""

    def ctime(self) -> int:
        """获取当前的策略时间。"""
        pass
