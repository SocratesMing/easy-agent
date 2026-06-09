"""做市接口 — 报价、撤报价、询价回复"""

maker = None


def to_quote(symbol: str, maker_quote_depths: list, floor_code=None,
             market_type=None, quote_status=None, quote_msg=None,
             expire_time=None) -> None:
    """分层带量报价(外汇/债券做市)。

    Args:
        symbol: 合约唯一代码
        maker_quote_depths: 报价字段列表，每层含:
            外汇做市:
                - level(int): 层级
                - bid(float): 买价
                - bidAmt(float): 买量
                - ask(float): 卖价
                - askAmt(float): 卖量
            债券做市:
                - bid(float): 买净价
                - bidAmt(float): 买量
                - ask(float): 卖净价
                - askAmt(float): 卖量
                - bidYield(float): 买收益率
                - askYield(float): 卖收益率
                - bidStrikeYield(float): 行权买收益率
                - askStrikeYield(float): 行权卖收益率
        floor_code: 交易分组(策略只有一个时可不传)
            外汇: 如 "LP_ESP1"
            债券: 格式${渠道}-${报价类型}
                本币: CFETS-IND / BC-IND / ALL-IND
                外汇: QT / QT1 / QT2
        market_type: 市场类型 ODM/QDM(默认QDM)
        expire_time: 过期时间(分钟，仅本币做市有效)
        quote_status: 报价状态
        quote_msg: 报价消息

    Example:
        >>> depths = [
        ...     {'level': 1, 'bid': 1.1958, 'bidAmt': 100, 'ask': 1.1972, 'askAmt': 100},
        ...     {'level': 2, 'bid': 1.1958, 'bidAmt': 200, 'ask': 1.1972, 'askAmt': 200},
        ... ]
        >>> maker.to_quote("EURUSDSP", depths, "LP_ESP1")
    """
    if maker:
        return maker.to_quote(symbol, maker_quote_depths, floor_code, market_type,
                              quote_status, quote_msg, expire_time)
    return None


def to_quote_cancel(symbol: str, floor_code=None) -> None:
    """撤销报价(不终止做市方的报价流)。

    Args:
        symbol: 合约唯一代码
        floor_code: 交易分组(策略只有一个时可不传)

    Example:
        >>> maker.to_quote_cancel("EURUSDSP", "LP_ESP1")
    """
    if maker:
        return maker.to_quote_cancel(symbol, floor_code)
    return


def to_rfq_quote_rej(quote_req_id: str, reject_reason: str,
                      reject_text: str = None) -> None:
    """拒绝CFETS的报价请求。

    Args:
        quote_req_id: 订单唯一标识
        reject_reason: 拒绝原因代码:
            1-无效的产品要素, 2-报价无效, 3-数量超限,
            4-LP已结束交易, 5-无效价格, 6-未授权,
            7-无匹配项, 8-RFK不支持, 9-流动性不足,
            10-Pass, 11-授信额度不足, 99-其他
        reject_text: 具体拒绝原因(reject_reason为99时必填)

    Example:
        >>> maker.to_rfq_quote_rej("216867660628103168", "1")
    """
    if maker:
        return maker.to_rfq_quote_rej(quote_req_id, reject_reason, reject_text)
    return


def to_rfq_quote(quote_req_id: str, price: float = None, yield_rate: float = None,
                 strike_yield: float = None, order_qty: float = None,
                 valid_time: str = None) -> None:
    """回复询价报价(债券做市)。

    Args:
        quote_req_id: 询价报价请求ID
        price: 净价
        yield_rate: 收益率
        strike_yield: 行权收益率
        order_qty: 回价量
        valid_time: 报价有效时间(yyyyMMdd-HH:mm:ss.SSS)

    Note:
        报价有有效时间，过期后自动失效无需撤销。
        更新操作需确认已收到询价请求已回复的回执。

    Example:
        >>> maker.to_rfq_quote("216867660628103168", price=7.1, order_qty=1000000)
    """
    if maker:
        return maker.to_rfq_quote(quote_req_id, price, yield_rate, strike_yield,
                                  order_qty, valid_time)
    return


def to_rfq_quote_cancel(quote_req_id: str) -> None:
    """撤销询价报价(不终止做市方的报价流)。

    Args:
        quote_req_id: 询价报价请求ID

    Note:
        撤销后仍可重新报价，只要询价请求未过期。
        撤销操作需确认已收到询价请求已回复的回执。

    Example:
        >>> maker.to_rfq_quote_cancel("216867660628103168")
    """
    if maker:
        return maker.to_rfq_quote_cancel(quote_req_id)
    return


def get_rfq_req(quote_req_id=None) -> list:
    """查询当前策略维度下接收的询价请求(本地缓存)。

    Args:
        quote_req_id: 询价报价请求ID(不传则查询全部)

    Returns:
        list: 询价请求列表(已完结的会移除)
    """
    if maker:
        return maker.get_rfq_req(quote_req_id)
    return []


def get_rfq_quote(quote_req_id=None) -> list:
    """查询当前策略维度下回复请求报文(本地缓存)。

    Args:
        quote_req_id: 询价报价请求ID(不传则查询全部)

    Returns:
        list: 回复报价列表(已完结的会移除)
    """
    if maker:
        return maker.get_rfq_quote(quote_req_id)
    return []


def get_rfq_quote_on_way(bond_code: str, counterparty_id=None,
                         settl_type=None) -> list:
    """查询当前维度下手工已回复信息(远程查询)。

    Args:
        bond_code: 债券代码
        counterparty_id: 对手方交易机构代码
        settl_type: 清算速度

    Returns:
        list: 询价报文列表
    """
    if maker:
        return maker.get_rfq_quote_on_way(bond_code, counterparty_id, settl_type)
    return []
