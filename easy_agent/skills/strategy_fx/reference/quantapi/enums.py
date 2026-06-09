"""枚举常量定义 — 订单状态、时效性、订单类型"""

# ── 订单状态 ──────────────────────────────────────────────────────────
ORDER_STATUS_NEW = '0'          # 初始化
ORDER_STATUS_PROCESSING = '1'   # 处理中(已提交)
ORDER_STATUS_REJECT = '2'       # 拒绝
ORDER_STATUS_BUILD = '3'        # 开仓成交
ORDER_STATUS_TIMEOUT = '5'      # 超时
ORDER_STATUS_CAN = '6'          # 撤销中
ORDER_STATUS_CAD = '7'          # 已撤销
ORDER_STATUS_FIN = '8'          # 已结束
ORDER_STATUS_SUBMITTED = '9'    # 已提交
ORDER_STATUS_UNKNOWN = '99'     # 未明

# ── 订单时效性 ────────────────────────────────────────────────────────
TIME_IN_FORCE_NULL = 0   # 无时效性
TIME_IN_FORCE_GFD = 1    # 当日有效
TIME_IN_FORCE_FOK = 4    # 全部成交或取消
TIME_IN_FORCE_FAK = 5    # 部分成交或取消
TIME_IN_FORCE_GTC = 6    # 撤销前有效
TIME_IN_FORCE_GTD = 7    # 到期日前有效

# ── 订单类型 ──────────────────────────────────────────────────────────
ORDER_TYPE_CLICK = 0           # CLICK点击单
ORDER_TYPE_MARKET = 1          # 市场单
ORDER_TYPE_LIMIT = 2           # 内部限价单(内部扫单)
ORDER_TYPE_IN_DEAL = 12        # 内部直通单
ORDER_TYPE_STOP_LIMIT = 14     # 停止限价单
ORDER_TYPE_SOR = 15            # SOR算法单
ORDER_TYPE_BLOOM_CLICK = 16    # 彭博订单
ORDER_TYPE_OCO = 17            # OCO算法单
ORDER_TYPE_IF_DONE = 18        # IF_DONE算法单
ORDER_TYPE_IF_DONE_OCO = 19    # IF_DONE_OCO算法单
ORDER_TYPE_Q_CLICK = 29        # 外部直通单
ORDER_TYPE_FMUT_MKT = 30       # 手工平仓单
ORDER_TYPE_Q_LIMIT = 31        # Q_LIMIT算法单
ORDER_TYPE_Q_STOP_LOSS = 33    # Q_STOP_LOSS算法单
ORDER_TYPE_Q_OCO = 37          # Q_OCO组合单
ORDER_TYPE_Q_IF_DONE = 38      # Q_IF_DONE算法单
ORDER_TYPE_Q_IF_DONE_OCO = 39  # Q_IF_DONE_OCO算法单
ORDER_TYPE_BEST_LIMIT = 51     # 择优限价单(SOR)
ORDER_TYPE_LOSS_LIMIT = 52     # 止损限价单
ORDER_TYPE_BEST_RFQ = 50       # 择优询价交易单
ORDER_TYPE_LIMIT_OUT = 28      # 外部限价单(直通)
ORDER_TYPE_FMUT_LIMIT = 11     # 集中限价单
ORDER_TYPE_STOP_LOSS = 13      # 止损单
ORDER_TYPE_ESP = 99            # 做市ESP订单
ORDER_TYPE_RFQ = 98            # 做市RFQ订单
ORDER_TYPE_SIGNAL = 100        # 信号
ORDER_TYPE_BL = 111            # USDCNYSP补录订单

# ── 内部区分 ──────────────────────────────────────────────────────────
ORDER_TYPE_LIMIT_IN_SCAN = ORDER_TYPE_LIMIT       # 内部限价单(扫单)
ORDER_TYPE_LIMIT_OUT_DIRECT = ORDER_TYPE_LIMIT_OUT  # 外部限价单(直通)
