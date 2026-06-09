base = None


def get_contract(code: str):
    """
        描述
            获取合约信息

        参数
            - code (string): 合约唯一代码，合约名

        返回 dict
            - code(string): 合约代码
            - contractType(string): 合约类型 ContractTypeEnum {B:基础合约, D:基差合约,T:期差合约,S:连续合约,M:月份合约,N:非标准合约}
            - sites(string): 交易主体
            - productBroad(string): 产品大类
            - products(string): 产品小类
            - contractMultiplier(float): 合约乘数
            - tenor(string): 期限
            - tenorGroup(string): 组合期限
            - lastDate(string): 最后交易日
            - dealTypeGroup(string): 组合交易品种
            - quoteUnitCodifiersGrpCode
            - dealType(string): 交易品种
            - valueDateRule(string): 起息日规则
            - market(string): 交易市场
            - quoteCurrency(string): 报价货币
            - timeStamp
            - localName(string): 本地名称
            - name(string): 英文名称
            - quoteUnit(string): 报价单位
            - startDate(string): 开始交易日
            - noDecimal(int): 报价有效位数
            - status(string): 合约状态

        示例
            >>> base.get_contract("EURUSDSP")
            {'code': 'EURUSDSP', 'contractType': 'S', 'sites': 'BOC', 'productBroad': 'FX', 'products': 'FXSPOT',
            'contractMultiplier': 100000.0, 'tenor': 'SPOT', 'id': 0, 'tenorGroup': None, 'lastDate': 20260701,
            'comments': None, 'dealTypeGroup': None, 'globalId': None, 'quoteUnitCodifiersGrpCode': None, 'dealType': 'EURUSD',
            'valueDateRule': 'T+2', 'market': '360T_GTX_QDM,360T_SST_QDM,CFETS_LC_ODM,CFETS_LC_QDM,FXALL_QDM,JPMC,UBS',
            'quoteCurrency': 'USD', 'timeStamp': '2021-07-15 10:08:31.0', 'localName': 'EURUSDSP', 'name': 'EURUSDSP', 'quoteUnit': '0',
            'startDate': 20170701, 'noDecimal': 6, 'status': 'V'}
    """
    return base.get_contract(code)


# def get_bull_bear_flag(name=None):
#     """
#     获取牛熊标识
#
#     Keyword Arguments:
#             name (string): 牛熊参数名称 (仅订阅一个牛熊指标时可不传)
#
#     返回: 牛熊标识,0 - 未知; 1 - 熊; 2 - 牛; 3 - 已达可能熊; 4 - 已达可能牛
#
#     使用方法:
#         >>> bull_bear = base.get_bull_bear_flag('牛熊参数')
#     """
#     return base.get_bull_bear_flag(name)


def get_bond_cash_flow(bond_code):
    """
       描述
            获取债券现金流信息

       参数:
            bond_code (string): 债券编码

       返回: list
            - key(string): 债券现金流主键 
            - bondsId(int): 债券编码id 
            - cashflowNo(int): 债券编码现金流编码 
            - currency(string): 货币 
            - cashflowType(string): 现金流类型P-Pemium, R-Principal, I-Interest, F-Fee
            - cashflowStatus(string): 现金流状态 I-Init，F-Fixing
            - dealType(string):  收付方向 P-Pay R-Receive
            - cashflowDate(int): 债券现金流日期
            - cashflowTime(int): 债券现金流时间
            - amount(float): 金额
            - notional(float): 本金 
            - paymentDate(int): 付款日期 
            - exDivideneDate(int): 除权日期 
            - adjustDate(string): 是否调整日期 N-No Y-Yes 
            - startDate(int): 开始时间 
            - endDate(int): 结束时间 
            - startDateAdj(int): 报价单位 
            - endDateAdj(int): 开始交易日 
            - period(int): 周期 
            - phase(string): 阶段 B-期初 M-期中 E-期末 
            - basis(string): 计息基础 
            - auditTimeStamp(string): 时间戳 
            - resets(string): 重置信息（None）
            - indexation(string): 付息方式（None）
             

        示例
            >>> base.get_bond_cash_flow('160017')
            [{'key': '1600171', 'bondsId': 3700037, 'cashflowNo': 1, 'currency': 'CNY', 'cashflowType': 'R',
            'cashflowStatus': 'F', 'dealType': 'P', 'cashflowDate': 20230830, 'cashflowTime': 175338,
            'amount': -100.0, 'notional': 100.0, 'paymentDate': 20160804, 'exDivideneDate': 0, 'adjustDate': 'N',
            'startDate': 20160804, 'endDate': 20160804, 'startDateAdj': 20160804, 'endDateAdj': 20160804, 'period': 1,
            'phase': 'B', 'basis': '', 'auditTimeStamp': '2023-08-30 17:53:38.0', 'resets': None, 'indexation': None},
            {'key': '16001710', 'bondsId': 3700037, 'cashflowNo': 10, 'currency': 'CNY', 'cashflowType': 'I',
            'cashflowStatus': 'F', 'dealType': 'R', 'cashflowDate': 20230830, 'cashflowTime': 175338, 'amount': 1.37,
            'notional': 100.0, 'paymentDate': 20210204, 'exDivideneDate': 20210204, 'adjustDate': 'N',
            'startDate': 20200804, 'endDate': 20210204, 'startDateAdj': 20200804, 'endDateAdj': 20210204, 'period': 10,
            'phase': 'M', 'basis': 'F', 'auditTimeStamp': '2023-08-30 17:53:38.0', 'resets': None, 'indexation': None}]

        """
    return base.get_bond_cash_flow(bond_code)


def get_bond_info(symbol: str):
    """
    描述:
        债券基本信息查询

    参数:
        symbol(str):债券编码

    返回: dict
            - bondCode(str): 债券编码
            - couponFrequency(str): 付息频率 支持枚举 [1D-每日, 1W-每周, 2W-每两周, 1M-每月, 3M-每季度, 6M-每半年, 1Y-每年. MT-利随本清, N-无]
            - fixingRate(float): 票面利率
            - maturityDate(int): 到期日
            - valueDate(int): 起息日

    示例:
        >>> base.get_bond_info('160017_T+1')
    """
    if symbol is not None:
        return base.get_bond_info(symbol)
    else:
        raise Exception("symbol cannot be None")
