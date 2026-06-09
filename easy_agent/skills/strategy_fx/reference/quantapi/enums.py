# 订单状态
ORDER_STATUS_NEW = '0'  # 订单初始化
ORDER_STATUS_REJECT = '2'  # 订单拒绝 无任何成交【拆分的订单交易均失败】
ORDER_STATUS_CAN = '6'  # 撤销中
ORDER_STATUS_CAD = '7'  # 订单取消 策略主动撤单且所有下单量均未成交的订单或者闭市撤销所有下单量
ORDER_STATUS_FIN = '8'  # 订单完成 订单的下单量全部成交已成交或者订单已终结，存在部分交易量
ORDER_STATUS_TIMEOUT = '5'  # 订单超时 超过时限并且所有下单量均未成交的订单
ORDER_STATUS_UNKNOWN = '99'  # 未明
ORDER_STATUS_PROCESSING = '1'   # 订单处理中（订单已提交）
ORDER_STATUS_SUBMITTED = '9'  # 订单已提交
ORDER_STATUS_BUILD = '3'  # 开仓成交


# 订单的时效性
TIME_IN_FORCE_GFD = 1
TIME_IN_FORCE_FOK = 4
TIME_IN_FORCE_FAK = 5
TIME_IN_FORCE_GTC = 6
TIME_IN_FORCE_GTD = 7
TIME_IN_FORCE_NULL = 0  # 表示无时效性

# 订单类型
ORDER_TYPE_MARKET = 1  # 市场单
ORDER_TYPE_LIMIT = 2  # 内部限价单(表示内部订单扫单) 仿真 要实现内部扫单 11
ORDER_TYPE_LIMIT_OUT = 28  # 外部限价单 (表示外部订单直通) 仿真 28(in_out)
ORDER_TYPE_SOR = 15  # SOR算法单
ORDER_TYPE_CLICK = 0  # CLICK点击单
ORDER_TYPE_FMUT_LIMIT = 11  # 集中限价单
ORDER_TYPE_STOP_LOSS = 13  # 止损单
ORDER_TYPE_OCO = 17  # OCO算法单
ORDER_TYPE_IF_DONE = 18  # IF_DONE算法单
ORDER_TYPE_IF_DONE_OCO = 19  # IF_DONE_OCO算法单
ORDER_TYPE_Q_LIMIT = 31  # Q_LIMIT 算法单
ORDER_TYPE_Q_STOP_LOSS = 33  # Q_STOP_LOSS 算法单
ORDER_TYPE_Q_OCO = 37  # OCO组合单
ORDER_TYPE_Q_IF_DONE = 38  # IF_DONE算法单
ORDER_TYPE_Q_IF_DONE_OCO = 39  # IF_DONE_OCO算法单
ORDER_TYPE_BEST_RFQ = 50  # 择优询价交易单
ORDER_TYPE_LOSS_LIMIT = 52  # 止损限价单
ORDER_TYPE_BEST_LIMIT = 51  # 择优限价单 SOR单
ORDER_TYPE_STOP_LIMIT = 14  # 停止限价单
ORDER_TYPE_FMUT_MKT = 30  # 手工平仓单
ORDER_TYPE_ESP = 99  # 做市ESP订单
ORDER_TYPE_RFQ = 98  # 做市RFQ订单
ORDER_TYPE_SIGNAL = 100  # 信号
ORDER_TYPE_IN_DEAL = 12  # 内部直通单
ORDER_TYPE_BL = 111  # USDCNYSP补录订单
ORDER_TYPE_Q_CLICK = 29 # 外部直通单
ORDER_TYPE_BLOOM_CLICK = 16 # 彭博订单


# 做内部区分
ORDER_TYPE_LIMIT_IN_SCAN = ORDER_TYPE_LIMIT  # 内部限价单(表示内部订单扫单) 仿真 要实现内部扫单 11
ORDER_TYPE_LIMIT_OUT_DIRECT = ORDER_TYPE_LIMIT_OUT  # 外部限价单 (表示外部订单直通) 仿真 28(in_out)
