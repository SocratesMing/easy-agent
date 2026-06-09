md = None


def get_price(symbol, type_=None, source=None, fields=None) -> list:
    """
    描述:
        获取最新行情

    必传参数:
        symbol(str):合约唯一代码

    参数:
        type    str     数据类型 (default: {None})
        source  str     行情来源 (default: {None})
        fields  list    指定返回对象字段 (default: {None})

    返回 list:
        - status(str):价格状态-->QuoteStatusEnum[1-正常,2-异常]
        - source(str):数据渠道
        - type(str):数据类型
        - symbol(str):合约代码
        - time(int):时间戳
        - bestBid(float):最优买价
        - bestBidAmt(float):最优买价数量
        - bestAsk(float):最优卖价
        - bestAskAmt(float):最优卖价数量
        - asks(list):卖出报盘价格，asks[0]代表盘口卖一档报盘价
        - ask_vols(list):卖出报盘数量，ask_vols[0]代表盘口卖一档报盘数量
        - bids(list):买入报盘价格，bids[0]代表盘口买一档报盘价
        - bid_vols(list):买入报盘数量，bid_vols[0]代表盘口买一档报盘数量
        - limitUp(float):涨停价
        - limitDown(float):跌停价

    示例:
        >>> md.get_price("EURUSDSP")
        [{'status': '1', 'source': 'CFETS_LC', 'type': 'ODM_DEPTH', 'symbol': 'EURUSDSP',
        'time': 1598922000100, 'bestBid': 1.19907, 'bestBidAmt': 94, 'bestAsk': 1.19919,
        'bestAskAmt': 94, 'asks': [1.19919, 1.19925, 1.19931, 1.19937, 1.19943],
        'ask_vols': [94, 94, 94, 94, 94], 'bids': [1.19907, 1.19901, 1.19895, 1.19889, 1.19883],
        'bid_vols': [94, 94, 94, 94, 94], 'limitUp': '', 'limitDown': ''}]
    """
    return md.get_price(symbol, type_, source, fields)


def query_bars(symbol, type_, source, count, fields=None, df=False):
    """
    描述:
        获取一段时间的N根bar数据

    必传参数:
        - symbol(str)  合约唯一代码
        - type  (str)  数据类型
        - source(str)  行情来源
        - count (int)  bar数量

    参数:
        - fields  list     指定返回对象字段 (default: {None})
        - df      bool     是否返回 dataframe格式 (default: {False})

    返回:
        Bar对象 list
        - source(str):数据渠道
        - type(str):数据类型
        - frequency(str):频率-->[1N,5N,15N,30N,1H,1D,1W,1M]
        - symbol(str):合约代码
        - time(int):时间戳
        - tradeDate(int):交易日期-->yyyyMMddHHmmss
        - open(float):开盘价
        - close(float):收盘价
        - high(float):最高价
        - low(float):最低价
        - trade_volume(float):成交量
        - trade_amt(float):成交额
        - strat(int):bar的开始时间
        - end(int):bar的结束时间

    示例:
        >>> md.query_bars("EURUSDSP", type_="5N_BAR_ODM_DEPTH", source="CFETS_LC", count=1, fields=["open", "close"])
           [{'source': 'CFETS_LC', 'type': '5N_BAR_ODM_DEPTH', 'frequency': '5N', 'symbol': 'EURUSDSP',
           'time': 1532017800200, 'start': 1532018100000.0, 'end': 1532018400000.0,
           'tradeDate': 20180720003000, 'open': 1.1648800000000001, 'close': 1.16487,
           'high': 1.1648800000000001, 'low': 1.1648649999999998, 'trade_volume': None, 'trade_amt': None}]
    """
    return md.query_bars(symbol, type_, source, count, fields, df)


def query_bars_pro(symbol, type_, source, count, fields=None, data_type=1) -> list:
    """
    描述
        获取一段时间获取N根bar数据

    参数
        - symbol(str): 合约唯一代码 【必填】
        - type(str): 数据类型 【必填】
        - source(str): 行情来源 【必填】
        - count(int): bar数量 【必填】
        - fields(list): 指定返回对象字段 (default: {None})
        - data_type(int): 指定返回数据对象类型(0-pandas、1-numpy、2-dict  default:1)

    返回
        list<dict>:  Bar对象
    
    示例
        >>> md.query_bars("EURUSDSP", type_="5N_BAR_ODM_DEPTH", source="CFETS_LC", count=1, fields=["open", "close"])
           [{'source': 'CFETS_LC', 'type': '5N_BAR_ODM_DEPTH', 'frequency': '5N', 'symbol': 'EURUSDSP',
           'time': 1532017800200, 'start': 1532018100000.0, 'end': 1532018400000.0,
           'tradeDate': 20180720003000, 'open': 1.1648800000000001, 'close': 1.16487,
           'high': 1.1648800000000001, 'low': 1.1648649999999998, 'tradeVolume': None, 'tradeAmt': None}]
    """
    return md.query_bars_pro(symbol, type_, source, count, fields, data_type)


def sub_scribe(channel_code, symbol, type):
    """
    描述
        渠道行情订阅从缓存调整为下发

    参数
        - symbol(str): 合约唯一代码
        - channel_code(str): 渠道代码
        - type(str): 行情类型

    返回
        None
    """
    return md.sub_scribe(channel_code, symbol, type)


def un_sub_scribe(channel_code, symbol, type):
    """
    描述
        渠道行情订阅从下发调整为缓存

    参数
        - symbol(str): 合约唯一代码
        - channel_code(str): 渠道代码
        - type(str): 行情类型

    返回
        None
    """
    return md.un_sub_scribe(channel_code, symbol, type)


def in_active_bond_pricing(symbol):
    """
    描述:
        非活跃券定价
    参数:
        symbol(str):合约编码

    返回:
        - netPrice(float):净价
        - dirtyPrice(float):全价
        - yieldToMaturity(float):收益率

    示例:
        >>> netPrice, dirtyPrice, yieldToMaturity = md.in_active_bond_pricing('160017_T+1')
    """
    return md.in_active_bond_pricing(symbol)


def get_bond_mutual_calculation(symbol: str, netPrice=None, ytm=None):
    """
    描述:
        互算接口: 输入净价或者到期收益率计算返回净价、全价、到期收益率

    参数:
        symbol(str):债券编码 必输
        netprice(float):净价 非必输,与到期收益率二选一输入即可
        ytm(float):到期收益率 非必输,与净价二选一输入即可

    返回:
        - netprice(float):净价
        - fullprice(float):全价
        - ytm(float):到期收益率

    示例:
        >>> discount_factor = md.get_bond_mutual_calculation(symbol, netPrice, ytm)
    """
    return md.get_bond_mutual_calculation(symbol, netPrice, ytm)


def get_irs_df(code: str, date_list: list):
    """
    描述:
        贴现因子查询

    参数:
        - code(str):合约
        - date_list(list):日期列表 日期格式YYYYMMDD 日期在五年以内

    返回:
        无

    示例:
        >>> discount_factor = md.get_irs_df('FR007', [20220608, 20220701])
    """
    return md.get_irs_df(code, date_list)


def get_xswap_discount_curve(code):
    """
    描述:
        利率互换贴现因子查询

    参数:
        - code(str):合约编码

    返回 dict:
        - curveTenor(list(str)):关键期限点
        - discountCurve(list(float)):贴现因子曲线

    使用方法:
        >>> curve = md.get_xswap_discount_curve(symbol='FR007')
    """
    return md.get_xswap_discount_curve(code=code)


def get_irs_fixing_curve(code: str, start_date: int, end_date=None):
    """
    描述:
        定盘利率查询

    参数:
        - symbol(str):合约
        - start_date(list):起始日期  日期格式YYYYMMDD
        - end_date(list):结束日期  日期格式YYYYMMDD 默认是当前时间

    返回 dict:
        - date:
           - mdType(str): 债券类型 0-Shibor，K-回购定盘
           - securityType(str): 债券品种 Shibor：ShiborCn 回购定盘：FR001、FR007、FR014
           - tenor(str): 债券期限 Shibor：O/N、1W、2W、1M、3M、6M、9M、1Y，回购定盘：上下限(例：1-5)
           - price(float): 价格
           - shiborBp(str): 涨跌幅 Shibor专有，单位BP
           - benchmarkEffectiveDate(str): 生成日期 格式yyyyMMdd
    示例:
        >>> rate = md.get_irs_fixing_curve('FR007', 20220608, 20220701)
    """
    return md.get_irs_fixing_curve(code, start_date, end_date)


def get_xswap_curve(code):
    """
    描述:
        利率互换曲线查询

    参数:
        - code(str):合约编码

    返回 dict:
        - curveTenor(list(str)):关键期限
        - rateCurve(list(float)):原始曲线
        - curveDates(list(str)):关键期限点对应日期
        - spotRateCurve(list(float)):即期利率曲线
        - positiveSpotRateCurve(list(float)):正 即期利率曲线
        - negativeSpotRateCurve(list(float)):负 即期利率曲线

    示例:
        >>> curve = md.get_xswap_curve(code='FR007_1Y')
    """
    return md.get_xswap_curve(code=code)


def get_bond_yield_curve(curve_type, bond_type, query_type=0, key_tenor=None):
    """
    描述:
        债券收益率曲线查询

    参数:
        - curve_type(int):曲线类型 1-自定义曲线 2-中债曲线
        - bond_type(str):债券类型 CDB-政策性金融债(国开行) GB-国债 EIBC-政策性金融债(进出口行) ADBC-政策性金融债(农发行)
            注意: 在选择自定义曲线的时候 有两种算法构建, 为Hermite算法,Hull-White 算法, 如果选择前者, 债券类型不加_HW, 例如 GZ;如果选择后者算法,需要在债券类型后加_HW, 例如GZ_HW \n\\
        - query_type(int):查询类型 0-全部曲线 1-即期 2-远期 3-到期
        - key_tenor(float list):关键期限点

    返回 dict:
        - spot(dict):即期曲线
            - key:关键期限点
            - value:关键期限点对应的即期收益率
        - fwd(dict):远期曲线
            - key:关键期限点
            - value:关键期限点对应的远期收益率
        - maturity(dict):到期曲线
            - key:关键期限点
            - value:关键期限点对应的远期收益率

    示例:
        >>> curve = md.get_bond_yield_curve(curve_type=1, bond_type='GK', query_type=0, key_tenor=0.09)
    """
    return md.get_bond_yield_curve_info(curve_type=curve_type, bond_type=bond_type, query_type=query_type,
                                        key_tenor=key_tenor)


def get_info_bond_curve(curve_num, curve_type, trade_dt=None, key_tenor=None, start_date=None, end_date=None):
    """
    描述:
        中债登收益率曲线查询

    参数:
        - curve_num(str):曲线编码
        - curve_type(str):曲线类型  SPOTCURVE-即期  MATCURVE-到期
        - trade_dt(str list): 交易日期 格式yyyyMMdd
        - key_tenor(float list):关键期限点
        - start_date(str): 交易开始日期 格式yyyyMMdd
        - end_date(str): 交易结束日期 格式yyyyMMdd
    示例:
       >>> curve_list = md.get_info_bond_curve(curve_num=1042, curve_type="SPOTCURVE", trade_dt=["20250725", "20250803"], key_tenor=[0, 0.5, 1, 2])
    """
    return md.get_info_bond_curve(curve_num=curve_num, curve_type=curve_type, trade_dt=trade_dt, key_tenor=key_tenor,
                                  start_date=start_date, end_date=end_date)


def cal_bond_yield_curve_indicator(code: str, start_date: int, end_date: int, key_tenor: list):
    """
    描述:
        关期限点指定时间段的曲线查询
    参数:
        - code(str):合约 
        - start_date(int):起始日期 日期格式YYYYMMDD 
        - end_date(int):结束日期 日期格式YYYYMMDD 默认是当前时间
        - key_tenor(list):关键期限点 默认查询全部 期限枚举['1D', '1W', '3M', '6M', '9M', '1Y', '2Y', '3Y', '4Y', '5Y']\n

    返回 dict:
        - date (dict):
            - tenor : rate
    示例:
        >>> rate = md.cal_bond_yield_curve_indicator('FR007', 20220608, 20220701, ['1D','1W'])
    """
    return md.cal_bond_yield_curve_indicator(code, start_date, end_date, key_tenor)


def get_bond_residual_curve(symbol, curveType, frequency='1N', num=-1):
    """
    描述:
        获取债券的残差曲线

    参数:
        - symbol(str): 债券编码
        - curveType(int): 曲线类型  0: 全部 1:'GB' 2: 'GB_HW' 3: 'GB_NS'
        - num(int): 数量, 默认可不填 
        - frequency(str): 残差曲线的周期 频率-->[1N,5N,15N,30N,1H,1D,1W,1M] 

    返回 dict:
        - curve(np.arr):
            - symbol(np.str):合约代码
            - time(np.int64):时间
            - price_residual(np.float64):全价残差
            - yield_residual(np.float64):到期收益率残差
        - price_residual_avg(float64):全价残差均值
        - price_residual_std(float64):全价残差标准差
        - price_residual_var(float64):全价残差方差
        - yield_residual_avg(float64):到期收益率残差均值
        - yield_residual_std(float64):到期收益率残差标准差
        - yield_residual_var(float64):到期收益率残差方差

    示例:
        >>> slope= md.get_residual_curve(symbol, frequency='1N')
    """
    return md.get_residual_curve(symbol, curveType, frequency, num)


def get_bond_yield_curve_slope(curve_type, bond_type, query_type, key_tenor_a, key_tenor_b):
    """
    描述:
        计算曲线斜率

    参数:
        - curve_type(int):曲线类型 1-自定义曲线 2-中债曲线
        - bond_type(str):债券类型 CDB-政策性金融债(国开行) GB-国债 EIBC-政策性金融债(进出口行) ADBC-政策性金融债(农发行)
                注意: 在选择自定义曲线的时候 有两种算法构建, 为Hermite算法,Hull-White 算法, 如果选择前者, 债券类型不加_HW, 例如 GZ;如果选择后者算法,需要在债券类型后加_HW, 例如GZ_HW 
        - query_type(int):查询类型 0-全部曲线 1-即期 2-远期 3-到期
        - key_tenor_a(float):期限点a
        - key_tenor_b(float):期限点b

    返回:
        float 类型的斜率

    示例:
        >>> slope= md.get_bond_yield_curve_slope(curve_type=1, bond_type='GK', query_type=0, key_tenor_a=0.09, key_tenor_b=0.09)
    """
    return md.get_bond_yield_curve_slope(curve_type, bond_type, query_type, key_tenor_a, key_tenor_b)


def get_active_bond(channel_code, bond_code, start_date, end_date):
    """
    描述:
        活跃券切券信息查询

    参数:
        - channel_code(str):渠道编码
        - bond_code(str):虚拟合约编码
        - start_date(int):开始时间
        - end_date(int):结束时间

    返回 list:
        - virtualCode(str): 虚拟合约编码
        - contractCode(str): 合约编码
        - date(int): 日期
        - channel(str): 渠道
        - type(str): 产品类型
        - time(int): 切券日期

    示例:
        >>> md.get_active_bond('X-BOND_HO', '100001#1M', 20221001, 20231001)
    """
    return md.get_active_bond(channel_code, bond_code, start_date, end_date)


def get_bond_three_factors_model(bond_type: str, term='10'):
    """
    描述:
        现券三因子查询

    参数:
        - bond_type(str):债券类型 CDB-政策性金融债(国开行) GB-国债 EIBC-政策性金融债(进出口行) ADBC-政策性金融债(农发行)
        - start_date(int):开始时间 yyyymmdd
        - end_date(int):结束时间 yyyymmdd
        - term(str):最长期限 (default: {10}) 可选数值['0.17', '0.25', '0.5', '0.75', '1', '2', '3', '5', '7', '10', '15', '20', '30', '40', '50']

    返回 dict:
        - level_factor(dict): 水平因子
            - factors: (list(float))  因子
            - avg: (float)            均值
            - std: (float)            标准差
            - var: (float)            方差
        
        - slope_factor(dict): 斜率因子
            - factors: (list(float)  因子  
            - avg: (float)           均值
            - std: (float)           标准差
            - var: (float)           方差

        - curvature_factor(dict): 曲率因子 
            - factors: (list(float))   因子  
            - avg: (float)             均值
            - std: (float)             标准差
            - var: (float)             方差
        
    示例:
        >>> curve = md.get_bond_three_factors_model('GK', 20230101, 20230520, term='1')
    """
    return md.get_bond_three_factors_model(bond_type, term)


def get_bonds_info(symbol):
    """
        描述:
            债券定义信息查询
        参数:
        - symbol(str): 合约代码

        返回 dict:
    """
    return md.get_bonds_info(symbol)


def get_contract_price_info(symbol):
    """
    描述:
        获取 对应 合约每手金额管理 数据
    参数:
    - symbol(str): 合约代码

    返回 dict:
        - contractCode(合约代码)
        - unitPrice(每手金额）
        - unitMaxLot(最大手数)
    """
    return md.get_contract_price_info(symbol)


def get_bonds_rating(symbol):
    """
        描述:
            债券评级信息查询
        参数:
        - symbol(str): 合约代码

        返回 dict:
    """
    return md.get_bonds_rating(symbol)


def check_issue(issue_code):
    """
        描述:
            判断发行人是否在跟随范围内
        参数:
        - issue_code(str): 发行人代码

        返回 True/False:
    """
    return md.check_issue(issue_code)


def get_trade_bonds_info(symbol):
    """
        描述:
            可成交债券信息查询
        参数:
        - symbol(str): 合约代码

        返回 dict:
    """
    return md.get_trade_bonds_info(symbol)


def get_market_info(channel_code, symbol):
    """
        描述:
            交易市场规则查询
        参数:
        - channel_code(str): 渠道
        - symbol: 合约

        返回 dict:
    """
    return md.get_market_info(channel_code, symbol)


def get_bond_basis_price(bond_code, start_date=None, end_date=None):
    """
        描述:
            中债估值
            若
        参数:
        - bond_code(str): 债券代码
        - start_date(str): 开始时间 格式yyyyMMdd
        - end_date(str) : 结束时间  格式yyyyMMdd

        备注:
        - 当前接口仅支持2025年4月26号之后的数据, 开始时间与结束时间不能跨年份查询

        返回 list:
    """
    return md.get_bond_basis_price(bond_code, start_date, end_date)


def get_issuer_liquidity_analysis(issuer, start_date=None, end_date=None):
    """
            描述:
                主体流动性评分数据查询
            参数:
            - issuer(str): 主体名称
            - start_date(str): 开始时间 格式YYYY-MM-DD
            - end_date(str) : 结束时间  格式YYYY-MM-DD

            返回 list:
        """
    return md.get_issuer_liquidity_analysis(issuer, start_date, end_date)