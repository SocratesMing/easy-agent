maker = None


def to_quote(symbol, maker_quote_depths, floor_code=None, market_type=None, quote_status=None,
             quote_msg=None, expire_time=None) -> None:
    """
    描述:
        LP目前仅支持以分层带量的方式提供市场行情数据,分层带量的报价形式分快照更新和增量更新两种
        在分层带量报价簿中，每层市场数据的量和价格满足一定的关系，例如:VWAP
        一笔交易仅可和一个分层带量报价的一层提交。LC可以和不同的LP的分层带量数据成交

    外汇做市
        参数:
            - symbol(string):合约唯一代码
            - maker_quote_depths(list[Dict]):报价字段列表
            >>>
                [{'level': 1, 'bid': 1.1958813630450311, 'bidAmt': 100, 'ask': 1.1972186369549689, 'askAmt': 100},
                {'level': 2, 'bid': 1.1958813630450311, 'bidAmt': 200, 'ask': 1.1972186369549689, 'askAmt': 200},
                {'level': 3, 'bid': 1.1958813630450311, 'bidAmt': 300, 'ask': 1.1972186369549689, 'askAmt': 300}]

            - floor_code(string):交易分组 (default: {None} 当策略只有一个floor_code，且其在策略参数配置唯一配置，则无需输入)
            - expire_time(int):过期时间 (default: {None} 以分钟为单位，仅本币做市有效, 20:00:00.000)
            - market_type(string): 市场类型 (default: {None} 传值: ODM/QDM  若为None则默认QDM)
            
        Returns:
            无

    债券做市
        参数:
            - symbol(string):合约唯一代码
            - maker_quote_depths(list[Dict]):报价字段列表
                - bid(float):买净价
                - bidAmt(float):买量
                - ask(float):卖净价
                - askAmt(float):卖量
                - bidYield(float):买收益率
                - askYield(float):卖收益率
                - bidStrikeYield(float):行权 买收益率
                - askStrikeYield(float):行权 卖收益率

            - floor_code枚举  格式${渠道}-${报价类型}
                - 本币:
                    CFETS-IND:交易中心,银行间,指示性报价
                    BC-IND:债券通指示性报价
                    ALL-IND: 银行间,债券通指示性报价
                - 外汇:
                    QT: 策略报价渠道
                    QT1: 策略报价渠道
                    QT2: 策略报价渠道

    示例:
        >>> maker_quote_depths = [{'level': 1, 'bid': 1.1958813630450311, 'bidAmt': 100, 'ask': 1.1972186369549689, 'askAmt': 100},
                                  {'level': 2, 'bid': 1.1958813630450311, 'bidAmt': 200, 'ask': 1.1972186369549689, 'askAmt': 200},
                                  {'level': 3, 'bid': 1.1958813630450311, 'bidAmt': 300, 'ask': 1.1972186369549689, 'askAmt': 300}]
        >>> maker.to_quote("EURUSDSP", maker_quote_depths, "LP_ESP1", expire_time=None)


    """
    if maker:
        return maker.to_quote(symbol, maker_quote_depths, floor_code, market_type, quote_status, quote_msg, expire_time)
    return None


def to_quote_cancel(symbol, floor_code=None) -> None:
    """
    描述
        撤销一个报价并不终止做市方的报价流。

    参数
        - symbol(string): 合约唯一代码
        - floor_code(string): 交易分组 (default: {None}当策略只有一个floor_code，且其在策略参数配置唯一配置，则无需输入)

    返回
        无
    
    示例
        >>> maker.to_quote_cancel("EURUSDSP", "LP_ESP1")

    """
    if maker:
        return maker.to_quote_cancel(symbol, floor_code)
    return


def to_rfq_quote_rej(quote_req_id, reject_reason, reject_text=None) -> None:
    """
    描述
        对于做市方不希望参与的RFQ,可发送to_rfq_quote_rej 函数来拒绝CFETS的报价请求。

    参数
        - quote_req_id(string):订单唯一标识
        - reject_reason(string):拒绝原因
            - 1 - 无效的产品要素
            - 2 - 该产品当前报价无效
            - 3 - 报价请求数量超过了限制
            - 4 -  LP已结束交易时间
            - 5 - 无效的价格
            - 6 - 该请求未得到Maker的授权
            - 7 - 无匹配项（No Match For Inquiry）
            - 8 -  RFQ不支持这个产品
            - 9 - 流动性不足（No Inventory）
            - 10 -  Pass由于市场条件Maker不愿参与RFQ
            - 11 - 代理方授信额度不足以完成交易
            - 99 - 其他
        - reject_text(string):描述具体的拒绝原因 (default: {None} 当reject_reason为99时, 需说明决绝原因)

    返回
        无
    
    示例
        >>> maker.to_rfq_quote_rej(216867660628103168, "1")
    """
    if maker:
        return maker.to_rfq_quote_rej(quote_req_id, reject_reason, reject_text)
    return


def to_rfq_quote(quote_req_id, price=None, yield_rate=None, strike_yield=None, order_qty=None, valid_time=None) -> None:
    """
    描述:
        做市方使用to_rfq_quote报文响应taker的onRfq请求。 rfqQuote报文用 QuoteReqID 与 onRfq 报文关联对应
        如果存在quote_req_id的回复请求,会判断是否可以修改, 如果可以修改则进行报价更新
        报价是有有效时间的，到了该时刻报价将失效。报价过期之后没有必要发送 to_rfq_quote_cancel 函数进行撤销。
        做更新操作时需要确认是否收到了询价请求已回复的回执,如果收到了才能做更新操作

    - 债券做市:
        - quote_req_id(string):询价报价请求Id
        - price(float): 净价 default: {None}
        - yield_rate(float): 收益率 default: {None}
        - strike_yield(float): 行权收益率 default: {None}
        - order_qty(float): 请求报价回复量 default: {None}
        - valid_time(string): 报价有效时间yyyyMMdd-HH:mm:ss.SSS default: {None} 默认取询价请求中的有效时间

    返回:
        无

    示例
        >>> maker.to_rfq_quote(216867660628103168, price=7.1, order_qty=1000000)
    """
    if maker:
        return maker.to_rfq_quote(quote_req_id, price, yield_rate, strike_yield, order_qty, valid_time)
    return


def to_rfq_quote_cancel(quote_req_id) -> None:
    """
    描述
        做市方通过执行to_rfq_quote_cancel函数来撤销报价。
        撤销一个报价并不终止做市方的报价。做市方执行 to_rfq_quote_cancel函数后仍然可以重新发送报价， 只要询价请求没有过期。
        做撤销操作时需要确认是否收到了询价请求已回复的回执,如果收到了才能做更新操作

    参数
        - quote_req_id(string):询价报价请求Id

    返回
        无
    
    示例
        >>> maker.to_rfq_quote_cancel(216867660628103168)
    """
    if maker:
        return maker.to_rfq_quote_cancel(quote_req_id)
    return


def get_rfq_req(quote_req_id=None) -> list:
    """
    描述
        做市方通过执行get_rfq_req函数来查询当前策略维度下接收的询价请求(从本地缓存中查询)。
        如果询价请求已完结会移除

    参数
        - quote_req_id(string):询价报价请求Id

    返回
        询价报文rfqReq
    """
    if maker:
        return maker.get_rfq_req(quote_req_id)
    return []


def get_rfq_quote(quote_req_id=None) -> list:
    """
        描述
            做市方通过执行get_rfq_quote函数来查询当前策略维度下回复请求报文(从本地缓存中查询)。
            如果回复请求已完结会移除

        参数
            - quote_req_id(string):询价报价请求Id

        返回
            询价报文rfqQuote
        """
    if maker:
        return maker.get_rfq_quote(quote_req_id)
    return []


def get_rfq_quote_on_way(bond_code, counterparty_id=None, settl_type=None) -> list:
    """
     描述
        做市方通过执行get_rfq_quote_on_way函数来查询当前维度下手工已回复信息(从远程中查询)。

    参数
        - bond_code(string): 债券代码
        - counterparty_id(string): 对手方交易机构代码
        - settl_type(string): 清算速度

    返回
        询价报文rfqQuote
    """
    if maker:
        return maker.get_rfq_quote_on_way(bond_code, counterparty_id, settl_type)
    return []
