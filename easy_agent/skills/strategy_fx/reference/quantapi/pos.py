pos = None


def get_position(symbol, pos_side=0) -> dict:
    """
    描述:
        根据合约获取持仓信息

    必选参数:
        symbol(string):合约代码

    可选参数:
        pos_side(int):头寸方向
            - 0-中性
            - 1-多方向
            - 2-空方向(default: {0})

    返回 dict:
        - symbol(string): 合约代码
        - frozenQuantity(float): 冻结量
        - quantity(float): 总持仓量
        - posSide(int): 头寸方向-->PosSideEnum[0-中性,1-多方向,2-空方向](default: {0})
        - profit(float): 损益
        - value(float): 估值
        - costPrice(float): 敞口价格
        - unRealizedPL(float): 未交割浮动损益
        - realizedPL(float): 已交割损益
        - washAmount(float): 持仓成本
        - time(long): 头寸时间

    示例:
        >>> pos.get_position("EURUSDSP", 0)
        {'symbol': 'EURUSDSP', 'frozenQuantity': 0, 'quantity': 0, 'posSide': 0,
        'profit': -2145.0000000000637, 'value': 0.0, 'costPrice': 1.16064, 'unRealizedPL': 0.0,
        'realizedPL': -2145.0000000000637, 'washAmount': 0.0, 'time': 1530524400101}
    """
    return pos.get_position(symbol, pos_side)


def get_position_onroad(symbol, effect=0, pos_side=0) -> dict:
    """
    描述
        根据合约获取持仓信息和在途单量
    参数
        - symbol(string):合约代码  【必填】
        - effect(int):开平仓类型
            - 0 - 中性 【默认】
            - 1 - 开仓
            - 2 - 平仓
        - pos_side(int):持仓方向
            - 0 - 中性 【默认】
            - 1 - 多方向
            - 2 - 空方向

    返回 dict: 各方向在途单数量及总持仓
            - onroad_b(float): 买方向汇总 
            - onroad_s(float): 买方向汇总 
            - quantity(float): 总持仓 

    示例
        >>> pos.get_position_onroad("EURUSDSP", 0, 0)
        {"onroad_b",60.0,"onroad_s",50.0,"quantity":1000}
    """
    return pos.get_position_onroad(symbol, effect, pos_side)


def get_position_quantity_onroad(symbol) -> dict:
    """
    描述
        根据合约获取持仓信息和在途单量
    参数
        - symbol(string):合约代码  【必填】

    返回 dict: 各方向在途单数量及总持仓
            - onroad_b(float): 买方向汇总
            - onroad_s(float): 买方向汇总
            - quantity(float): 总持仓(多方向为正，空方向为负)

    示例
        >>> pos.get_position_quantity_onroad("au2412")
        {"onroad_b",60.0,"onroad_s",50.0,"quantity":1000}
    """
    return pos.get_position_quantity_onroad(symbol)


def get_order_onroad_amt(symbol, side) -> dict:
    """
    描述
        根据合约和买卖方向获取在途单量
    参数
        - symbol(string):合约代码  【必填】
        - side(int):买卖方向    【必填】
            - B - 买
            - S - 卖

    返回 float: 买卖方向在途单数量

    示例
        >>> pos.get_order_onroad_amt("au2412", "B")
        60.0
    """
    return pos.get_order_onroad_amt(symbol, side)


def get_today_quantity_by_symbol(symbol) -> dict:
    """
    描述
        根据合约查询当日成交量
    参数
        - symbol(string):合约代码  【必填】

    返回 float: 当日成交量

    示例
        >>> pos.get_today_quantity_by_symbol("au2412")
        60.0
    """
    return pos.get_today_quantity_by_symbol(symbol)


def get_ord_position(order_id=None, symbol=None) -> list:
    """
    描述
        根据合约或者开仓订单ID获取逐笔持仓信息,如果不传获取全部未平仓持仓

    参数
        - order_id(string): 开仓订单Id (default: {None})
        - symbol(string): 合约代码 (default: {None})

    返回 list<dict>: Position信息
        - id(int): 唯一编号
        - symbol(string): 合约代码
        - frozenQuantity(float): 冻结量
        - quantity(float): 总持仓量
        - quantityTd(float): 今日持仓量
        - posSide(int): 头寸方向-->PosSideEnum[0-中性,1-多方向,2-空方向](default: {0})
        - profit(float): 损益
        - value(float): 估值
        - costPrice(float): 敞口价格
        - unRealizedPL(float): 未交割浮动损益
        - realizedPL(float): 已交割损益
        - washAmount(float): 持仓成本
        - time(long): 头寸时间
    
    示例
        >>> pos.get_ord_position(216868121676222464)
        [{'id': 216868121676222464, 'symbol': 'EURUSDSP', 'frozenQuantity': 0, 'quantity': 0, 'quantityTd': 0, 'posSide': 0,
        'profit': -2145.0000000000637, 'value': 0.0, 'costPrice': 1.16064, 'unRealizedPL': 0.0,
        'realizedPL': -2145.0000000000637, 'washAmount': 0.0, 'time': 1530524400101, 'avgPrice': 0}]
    """
    return pos.get_ord_position(order_id, symbol)


def roll_pos(symbol) -> list:
    """
    对所选合约进行展期操作，移到下一个交易日
        必选参数:
            symbol(string):合约代码 (default: {None})

        返回:
            list dict: Position信息 \n
            Returns:list
            [{ \n
                id'(int): 唯一编号 \n
                symbol(string): 合约代码 \n
                frozenQuantity(float): 冻结量 \n
                quantity(float): 总持仓量 \n
                quantityTd(float): 今日持仓量 \n
                posSide(int): 头寸方向-->PosSideEnum[0-中性,1-多方向,2-空方向](default: {0}) \n
                profit(float): 损益 \n
                value(float): 估值 \n
                costPrice(float): 敞口价格 \n
                unRealizedPL(float): 未交割浮动损益 \n
                realizedPL(float): 已交割损益 \n
                washAmount(float): 持仓成本 \n
                time(long): 头寸时间 \n
                avgPrice(float): 均价 \n
            }] \n
        使用方法
            >>> pos.roll_pos("EURUSDSP")
            [{'id': 216868121676222464, 'symbol': 'EURUSDSP', 'frozenQuantity': 0, 'quantity': 0, 'quantityTd': 0, 'posSide': 0,
            'profit': -2145.0000000000637, 'value': 0.0, 'costPrice': 1.16064, 'unRealizedPL': 0.0,
            'realizedPL': -2145.0000000000637, 'washAmount': 0.0, 'time': 1530524400101, 'avgPrice': 0}]
    """
    return pos.roll_pos(symbol)


def get_indicators(symbols=None):
    """
    描述:
        金融指标查询

    可选参数：
        symbols(list or str):合约编码

    返回 dict:
        - 单合约:
                - 固息债|贴现|利随本清:
                    - duration(float): 久期
                    - mod_duration(float): 修正久期
                    - convexity(float): 凸性
                    - dv01(float): dv01(未产生敞口时返回单位值)
                - 浮息债:
                    - spread_duration(float): 利差久期
                    - spread_convexity(float): 利差凸性
                    - ir_duration(float): 利率久期
                    - ir_convexity(float): 利率凸性
                    - dv01(float): dv01(未产生敞口时返回单位值)
        - 投组:
            投组维度如果未输入合约查询策略维度的指标，当未产生敞口时，返回空。
            投组维度如果输入合约，查询指定合约维度，当未产品敞口时，返回单位指标
                -duration(float): 久期(浮息债为利差久期)
                -convexity(float): 凸性(浮息债为利差凸性)
                -dv01(float): dv01
            
    示例:
        >>> pos.get_indicators('160016_T+1')
        {"duration": 1.355, "mod_duration": 1.6933, "convexity": 2.355, "dv01": 1.35443}
    """
    return pos.get_indicators(symbols)


def get_bond_loss_profit(dimension, symbol=None):
    """
    描述:
        现券损益查询

    参数:
        - dimension(int): 查询维度 1-合约 2-策略
        - symbol(str): 合约代码, 当查询维度为2(策略)时,不需要输入
        
    返回 dict:
        - unRealizedPL(float): 未实现损益
        - realizedPL(int): 已实现损益
        - profit(float): 总损益

    示例:
        >>> value = pos.get_bond_loss_profit(dimension=0, symbol='160017_T+1')
    """
    return pos.get_bond_loss_profit(dimension=dimension, symbol=symbol)


def get_xswap_loss_profit(dimension, symbol=None):
    """
    描述:
        利率互换损益查询

    参数:
        - dimension(int): 查询维度 1-利率指标 2-策略
        - symbol(str): 利率指标代码(如 FR007,Shibor3M), 当查询维度为2(策略)时,不需要输入

    返回 dict:
        unRealizedPL(float): 未实现损益
        realizedPL(float): 已实现损益
        profit(float): 总损益

    示例:
        >>> value = pos.get_xswap_loss_profit(dimension=1, symbol='FR007')
    """
    return pos.get_xswap_loss_profit(dimension=dimension, symbol=symbol)


def get_dv01(dimension, symbol=None):
    """
    描述:
        dv01查询(基准指标)利率互换
    参数:
        - dimension(int): 合约类型 0-基准指标 1-策略
        - symbol(str): 基准指标

    返回 dict:
        - 合约类型传入0:
            - 基准指标传入None时:
               - unit_dv01(dict): {key -- 期限点(str) : value -- dv01(float)}, key -- 期限点(str) : value -- dv01(float)}
               - tactics_sublevel_dv01_and_all_dv01: {key -- 期限点(str) : value -- dv01(float), 'ALL': totalValue}
            - 基准指标传入值时:
               - unit_dv01(dict): {key -- 期限点(str) : value -- dv01(float)}, key -- 期限点(str) : value -- dv01(float)}
               - 'index_'+symbol+'_sublevel_dv01_and_all_dv01': {key -- 期限点(str) : value -- dv01(float) }
        - 合约类型传入1:
            - 基准指标传入None时:
                - tactics_sublevel_dv01_and_all_dv01: {key -- 期限点(str) : value -- dv01(float), 'ALL': totalValue}
            - 基准指标传入值时:
                - 'index_'+symbol+'_sublevel_dv01_and_all_dv01': {key -- 期限点(str) : value -- dv01(float) }

    示例:
        >>> value = pos.get_dv01(dimension=0, symbol='FR007')
            dict: {
                    'unit_dv01':
                        {'1W': 0.000128515849878681, '1M': 0.000511175662487819, '3M': 0.001504189594754379, '6M': 0.003150001287815192,
                        '9M': 0.004549469498652091, '1Y': 0.006196887571089486, '2Y': 0.011579257279061596, '3Y': 0.017673877414448803,
                        '4Y': 0.02495314119810738, '5Y': 0.030532689279085506},
                    'index_'+symbol+'_sublevel_dv01_and_all_dv01':
                            {'1W': 0.0, '1M': 0.0, '3M': 0.0, '6M': 0.0, '9M': 0.0, '1Y': 0.0, '2Y': 0.0, '3Y': 0.0, '4Y': 0.0, '5Y': 0.0, 'ALL': -944.4172199859313}}
    """
    return pos.get_irs_dv01(dimension, symbol)


def get_folder_position(folder=None, pair=None) -> list:
    """
    描述
        根据账户、货币对获取头寸信息

    参数
        - folder(string): 账户 (default: {None})
        - pair(string): 货币对 (default: {None})

    返回
        list<dict>: Position信息

    示例
        >>> pos.get_folder_position("TTFX_LHHH","EURUSDSP")

    """
    return pos.get_folder_position(folder, pair)


def get_folder_trade(folder=None, tradeDateStart=None, tradeDateEnd=None) -> list:
    """
    描述
        根据账户、开始日期、结束日期获取成交信息

    参数
        - folder(string): 账户 (default: {None})
        - tradeDateStart(string): 开始日期 (default: {None})
        - tradeDateEnd(string): 结束日期 (default: {None})

    返回
        list<dict>: Trade信息

    示例
        >>> pos.get_folder_trade("TTFX_LHHH","20231008","20231009")
        [{'tradeId': '410736318340268032', 'orderId': 'Order410736317166264320', 'channel': 'UBS', 'origTradeId': None,
         'groupId': '410736318340268032', 'sourceChannel': 'TRTN', 'instrument': 'FXSPOT', 'instrumentPrefix': 'FX',
         'instrumentSuffix': 'SPOT', 'tradeType': 1, 'subTradeType': 0, 'innerBankTradeId': 'Fmut410736317166264320',
         'outsideBankTradeId': 'POD217RD102PK9', 'relatedId': 'QOD1ZYRD0ZNN54', 'execId': 'QOD1ZYRD0ZNN54', 'tradeWay': 'L',
         'tradeStatus': '2', 'pair': 'USDSGD', 'quoteId': '2827318967899841582_b0', 'ccy1': 'USD', 'ccy2': 'SGD',
         'branchCode': '00001', 'takerTraderId': 'fnn', 'cpty': 'UBS', 'makerTraderId': None, 'outAccountId': 'CITICTEST',
         'tradeDate': 20231009, 'tradeTime': 95943, 'comments': None, 'dealtCcy': 'USD', 'farInitRate': 0.0,
         'farSpotRate': 0.0, 'farPoint': 0.0, 'farAmount1': 0.0, 'farAmount2': 0.0, 'farTenor': None, 'farDeliveryDate': 0,
         'farBuySell': None, 'nearRate': 1.37126, 'nearInitRate': 1.37126, 'nearSpotRate': 1.37126, 'nearPoint': 0.0,
         'nearAmount1': -1000000.0, 'nearAmount2': 1371260.0, 'nearTenor': None, 'nearDeliveryDate': 20231011,
         'nearBuySell': 'S', 'swapPoint': None, 'isSplit': 1, 'splitRate': 0.0, 'splitPair': None, 'book': 'TTFX',
         'folder': 'TTFX_LHHH', 'entity': 'HO', 'entityId': '701100', 'orderType': '0', 'tradeModel': '',
         'isSendPosition': 1, 'positionCountStatus': 3, 'positionCountDateTime': '200000000000087071', 'reversalStatus': 1,
         'fmmsSendStatus': 1, 'entryType': 1, 'bookkeepingSource': 1, 'version': 0, 'bigCategories': 'FX',
         'subCategories': 'FXSPOT', 'cellCategories': None, 'origTradeReportId': None, 'productCode': None,
         'productName': None, 'cancelType': None, 'serialNo': None, 'algoOrderId': None, 'algoTacticsId': None,
         'contractCode': None, 'queryTradeType': 0, 'ourParty': None, 'tradePattern': None, 'mmOrderType': None,
         'mmBuySell': None, 'strategyPositionStatus': 0, 'strategyPositionNo': None, 'strategyCashflowStatus': 0,
         'strategyPositionComments': None}]
    """
    return pos.get_folder_trade(folder, tradeDateStart, tradeDateEnd)


def get_day_pnl():
    """
        描述
            获取当日损益(贵金属接口)
        参数
            无
        返回
            float
        示例
            >>> pos.get_day_pnl()
    """
    return pos.get_day_pnl()


def get_fx_profit():
    """
    描述:
        查询当前策略的已实现与浮动损益

    参数:
        无

    返回 dict:
        - unRealizedPL(float): 未实现损益
        - realizedPL(float): 已实现损益

    示例:
        >>> value = pos.get_fx_profit()
    """
    return pos.get_fx_profit()


def get_rpm_position_quantity(book=None, folder=None):
    """
    描述:
       查询当前组合账户的积存金

    参数:
       book(str) : 组合
       folder(str) : 账户

    返回 list:

    示例:
       rpm_position_list = pos.get_rpm_position_quantity(book="TGLD")
    """
    return pos.get_rpm_position_quantity(book, folder)


def get_pm_profit():
    """
    描述:
        查询当前贵金属策略已实现损益

        参数:
            无

        返回 dict:
            - unRealizedPL(float): 未实现损益
            - realizedPL(float): 已实现损益

        示例:
            >>> value = pos.get_pm_profit()
    """
    return pos.get_pm_profit()

