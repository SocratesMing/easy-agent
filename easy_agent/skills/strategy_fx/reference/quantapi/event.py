def init(context):
    '''
    描述
        init 中初始化一些必要的参数,订阅数据。

        在回测和实时模拟交易只会在启动的时候触发一次。

        你的算法会使用这个方法来设置你需要的各种初始化配置。

        context 对象将会在你的算法的所有其他的方法之间进行传递以方便你可以拿取到。
    
    参数
        - context(class): 连接上线文的环境对象, 该对象将会在你的算法策略任何方法之间传递
          用户可以通过context定义多种自己需要的属性,也可以查看context固有属性。
          此次上下文中新增五个固有属性:
            - subscribe(list): 策略订阅信息
            - 举例: [
		        {
		        	"symbol": "EURUSDSP",
		        	"sub_type": "1",
		        	"kind": "tick",
		        	"source": "UBS_HO",
		        	"type": "FXSPOT"
		        },
			    {
			    	"symbol": "EURUSDSP",
			    	"sub_type": "2",
			    	"kind": "bar",
			    	"source": "UBS_HO",
			    	"type": "1N_BAR_DEPTH"
			    }
		    ]
            - kind tick代表是深度行情, bar代表的是bar数据
            - type 代表是行情类型
    '''
    pass


def onData(context, data):
    '''
    描述
        已订阅(subscribe)合约tick ,每次数据的更新会自动触发该方法的调用。
        策略具体逻辑可在该方法内实现,包括交易信号的产生、订单的创建、风险管理等

    参数
        - context(class): 连接上下文的环境对象,该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性,也可以查看context固有属性。
        - data(list): [{'status': '1','source': 'CFETS_LC','type': 'ODM_DEPTH','symbol': 'EURUSDSP',
          'time': 1532000820300,'best_bid': 1.16478,'best_bid_amt': 57,'best_ask': 1.1649,
          'best_ask_amt': 57,'asks': [1.1649,1.16496,1.16502,1.16507,1.16513],
          'ask_vols': [57,57,57,57,57],'bids': [1.16478,1.16472,1.16466,1.16461,1.16455],
          'bid_vols': [57,57,57,57,57],'limitUp': None,'limitDown': None}]

    返回值
        无

    示例
        >>> def onData(context, data):
                tick = data[0]
                bid = tick.best_bid
                ask = tick.best_ask
                pass
    '''
    pass


def onOrder(context, order):
    '''
    描述
        order状态变化时,触发该函数
        订单状态如下

            - 0-初始化
            - 1-运行中
            - 2-订单拒绝
            - 5-订单超时
            - 6-订单撤销中
            - 7-交易已撤销(取消）
            - 8-已结束
            - 9-已提交
            - 99-未明。

    参数
        - context(class): 连接上下文的环境对象,该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性,也可以查看context固有属性。
        - order: {'id': 216870154712780800, 'channelCode': 'CFETS_LC', 'symbol': 'EURUSDSP',
          'orderType': 2, 'timeInForce': 5, 'expireTime': None, 'price': 1.224048,
          'side': 'B', 'effect': 0, 'quantity': 10, 'amount': 1165759.9999999998,
          'tradedQuantity': 10, 'orderStatus': '8', 'createTime': '20180720073000',
          'inOutMarket': 2, 'errorMsg': '', 'tradedAvgPrice': 1.1657599999999997,
          'hedgeFlag': None, 'intention': None, 'valueDate': None, 'maturityDate': None,
          'closeOrderId': None, 'posType': None, 'warnPrice': None, 'stopPrice': None, 'currency': None,
          'ctimeStamp': 1532043000100}

    返回
        无

    示例
        >>> def onOrder(context, order):
                order_id = order.id
                pass
    '''
    pass


def onTrade(context, trade):
    '''
    描述
        产生成交后触发事件驱动。

    参数
        - context(class): 连接上下文的环境对象,该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性,也可以查看context固有属性。
        - trade(dict): {'id': 216870196848627712, 'orderId': 216870196848627712, 'channelCode': 'CFETS_LC',
          'symbol': 'EURUSDSP', 'valueDate': '20180722', 'maturityDate': '20180722',
          'side': 'B', 'effect': 0, 'price': 1.1657599999999997, 'quantity': 10,
          'amount': 1165759.9999999998, 'tradeTime': '20180720073000', 'closeOrderId': None, 'inOutMarket': None,
          'ctimeStamp': 1532043000101, 'hedgeFlag': None, 'orderType': 2}

    返回值
        无

    示例
        >>> def onTrade(context, trade):
                trade_id = trade.id
                pass
    '''
    pass


'''定时任务'''


def onTime(context, time, name):
    '''
    描述
        根据条件撤销委托挂单

    参数
        - context(class): 连接上下文的环境对象,该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性,也可以查看context固有属性。
        - time(str): 定时器触发时间。
        - name(str): 定时器名称 跟scheduler注册的定时器名称一样。
    
    返回值
        无

    示例
        >>> scheduler.run_daily("my_job","160000")  # 需要在init方法中先使用scheduler设置好定时器
            def onTime(context, time, name):
                if name =='my_job'
                   #做一些事情,如平仓,查询敞口等
            pass
    '''
    pass


def onBusinessDate(context, data):
    """
    描述
        产生切日事件触发事件驱动

    """
    pass


def onMonitor(context, data):
    '''
    描述
        接口链路启停触发事件驱动
    '''
    pass


def onSignal(context, data, operate_type):
    '''
    描述
        信号根据是否监控开仓,止盈,止损等相关操作之后产生变化后触发事件驱动。
        当信号对象中signal_type是止盈,止损或开仓类型时,对象中market_target字段值为触发该操作的深度行情对象。
        行情对象有以下属性：
        
            - best_bid: 最优买价
            - best_bid_amt: 最优买价量
            - best_ask: 最优卖价
            - best_ask_amt: 最优卖价量
            - asks: 档位卖价
            - ask_vols: 档位行情卖量
            - bids: 档位买价
            - bid_vols: 档位行情买量

    参数
        - context(class): 连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        - data(dict): 信号对象
        - operate_type(string): 信号操作类型

    示例
        >>> def onSignal(context, data, operate_type):
                signal_id = data.signal_id
    '''
    pass


def onSignalPrice(context, signal):
    '''
    描述
        推送最新市价事件驱动。
        当前事件不是策略必须实现的事件，程序会自动判断策略中是否含有当前事件方法:
        
            1. 若有则将需要推送市价的信号对象传入该事件方法，由策略去根据业务场景决定推送的市价。
            2. 若没有则程序根据信号方向自动推送最新行情最优卖价或最优买价。

    参数
        - context(class): 连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        - signal(dict): 信号对象

    示例
        >>> def onSignalPrice(context, data):
                signal_id = data.signal_id   
    '''
    pass


def onFolderTrade(context, trade):
    '''
    描述
        推送账户维度成交信息。
        当前事件不是策略必须实现的事件，程序会自动判断策略中是否含有当前事件方法:
        若有则将需要推送账户成交信息传入该事件方法。

    参数
        - context(class): 连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        - trade(dict): 成交对象

    示例
        >>> def onFolderTrade(context, trade):
                runId = data.runId   
    '''
    pass


def onFolderPosition(context, position):
    '''
    描述
        推送账户维度头寸信息。
        当前事件不是策略必须实现的事件，程序会自动判断策略中是否含有当前事件方法:
        若有则将需要推送账户成交信息传入该事件方法。

    参数
        - context(class): 连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
          用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        - position(dict): 头寸对象

    示例
        >>> def onFolderPosition(context, trade):
                positionId = data.positionId
    '''
    pass


def onQuote(context, quote):
    """
    描述:
        做市商对外的报价如果被风控拒绝或者被交易中心拒绝，将触发onQuote事件

    参数:
        context (class):
            连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
            用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        quote (dict):
            报价对象
                外汇做市
                    { \n
                        'quoteId': 'b51ed597-9f7f-3fb4-b8c9-fd77e1aab906',# 报价id \n
                        'status': '2',# 状态 \n
                        'symbol': 'EURUSDSP',# 合约 \n
                        'time': 1705043184689,# 时间戳(毫秒) \n
                        'floorCode': 'CFETS-ESP', #, floorCode \n
                        'quoteTypeStr': 'ESP', # 报价类型 \n
                        'quoteDateTime': 20240112150624000, # 报价时间YYYYMMDDHHmmss \n
                        'makerDepths': [ # 报价信息,即to_qutoe的报价信息 \n
                            { \n
                            'positionNo': 1, \n
                            'bidAmt': 1000000.0, \n
                            'askAmt': 1000000.0, \n
                            'bid': 21.1951, \n
                            'ask': 21.1959, \n
                            'level': 1 \n
                            } \n
                        ], \n
                        'errorText': '未找到对应的渠道数据，channelCode = CFETS-ESP' # 失败原因 \n
                        }
                债券做市
                    { \n
                        'quoteId': '883d7924-8ef0-315a-93d7-7c736d46c15d', # 报价id \n
                        'makerDepths': [ # 报价信息,即to_qutoe的报价信息 \n
                            { \n
                            'bid': 119.9371, \n
                            'bidAmt': 10000000.0, \n
                            'ask': 119.9805, \n
                            'askAmt': 10000000.0 \n
                            } \n
                        ], \n
                        'quoteDateTime': '20240112145715', # 报价时间YYYYMMDDHHmmss \n
                        'symbol': '160017_T+1', # 合约 \n
                        'floorCode': None, # floorCode \n
                        'errorText': '报价失效：策略未绑定报价', # 失败原因 \n
                        'status': 1 # 报价状态 1-失败 \n
                    } \n

    使用方法:
        >>> def onQuote(context,quote):
                maker.to_quote_order_confirm(id, "F")
    """
    pass


def onQuoteOrder(context, quoteOrder):
    """
    描述:
        做市商对外的报价如果被交易对手点击成交请求，将触发onQuoteOrder事件,
        做市商需要通过to_quote_order_confirm来接收或者拒绝该成交请求,
        本币做市不会触发该事件

    参数:
        context (class):
            连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
            用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        quoteOrder (dict):
            报价交易对象
                { \n
                    "floorCode": "LP_ESP1", \n
                    "id": 223483495955697664, \n
                    "orderStatus": 2, \n
                    "symbol": "EURUSDSP", \n
                    "products": "FXSPOT", \n
                    "side": "B", \n
                    "orderType": 1, \n
                    "quantity": 196000000.0, \n
                    "tradeQuantity": 0, \n
                    "price": 21.18294, \n
                    "farPrice": None, \n
                    "spotRate": None, \n
                    "points": None, \n
                    "farPoints": None, \n
                    "createTime": 1625198940000, \n
                    "partyId": "yt", \n
                    "partyRoleId": "yt" \n
                } \n

    使用方法:
        >>> def onQuoteOrder(context,quoteOrder):
                maker.to_rfq_quote("EURUSDSP", 7.1, 7.2)
    """
    pass


def onRfqReq(context, rfqReq):
    """
    描述:
        对手方报价发起询价后,触发onRfqReq事件

    参数:
        - context (class):
            连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
            用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        - rfqReq (dict):
          - 债券询价:
            - channelCode(String): 渠道 CFETS-银行间 BC-债券通
            - quoteReqId(String): 询价请求编号
            - clOrdId(String): 客户参考编号
            - quoteId(String): 报价回复编号
            - symbol(String): 合约
            - quoteWay(String): 报价方式  1-手工 2-策略
            - side(String): 交易方向 B-买入 S-卖出
            - orderQty(float): 询价量
            - price(float): 净价
            - yieldRate(float): 收益率
            - strikeYield(float): 行权收益率
            - accruedInterestAmt(float): 应计利息
            - clearingMethod(String): 清算类型  1-净额清算 2-全额清算 默认2(全额清算)
            - settlType(String): 清算速度
            - deliveryType(String): 结算方式
            - settlDate(String): 结算日
            - principal(float): 每百元金额
            - counterPartyId(String): 对手方交易员账户
            - traderId(String): 对手方交易员ID
            - worker(String): 系统柜员
            - book(String): 组合
            - folder(String): 账户
            - validTime(String): 有效时间 
            - status(String): 2-待回复 3-已回复 4-已撤销 5-已过期 6-已拒绝

    使用方法:
        >>> def onRfqReq(context, rfqReq):
    """
    pass


def onRfqQuote(context, rfqQuote):
    """
    描述:
        做市商对外的回复报价如果状态发生变化，将触发onRfqQuote事件

    参数:
        - context (class):
            连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
            用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        - rfqQuote (dict):
          - 债券做市:
            - channelCode(String): 渠道 CFETS-银行间 BC-债券通
            - quoteReqId(String): 询价请求编号
            - clOrdId(String): 客户参考编号
            - fmqtQuoteId(String): 策略报价编号(运行服务生成)
            - quoteId(String): 报价回复编号(报价服务生成)
            - symbol(String): 合约
            - side(String): 交易方向 B-买入 S-卖出
            - orderQty(float): 回价量
            - price(float): 净价
            - yieldRate(float): 收益率
            - strikeYield(float): 行权收益率
            - accruedInterestAmt(float): 应计利息
            - clearingMethod(String): 清算类型 1-净额清算 2-全额清算 默认2(全额清算)
            - settlType(String): 清算速度
            - settlDate(String): 结算日
            - counterPartyId(String): 对手方交易员账户
            - traderId(String): 对手方交易员ID
            - book(String): 组合
            - folder(String): 账户
            - validTime(String): 有效时间
            - errorCode(String): 异常代码  1- 回复报价失败  2- 回复报价修改失败  3- 回复报价撤销失败 4- 回复报价超时
            - errorMsg(String): 异常信息
            - status(String): 1-执行中 2-已成交 5-已过期 4-已撤销 8-已拒绝


    使用方法:
        >>> def onRfqQuote(context, rfqQuote):
                pass
    """
    pass


def onRfqQuoteOrder(context, rfqQuoteOrder):
    """
    描述:
        做市商对外的报价如果被交易对手点击成交请求，将触发onRfqQuoteOrder事件,做市商需要通过to_rfq_quote_order_confirm来接收或者拒绝该成交请求
        本币做市不会触发该事件

    参数:
        context (class):
            连接上下文的环境对象，该对象将会在你的算法策略的任何方法之间做传递。
            用户可以通过context定义多种自己需要的属性，也可以查看context固有属性
        rfqQuoteOrder(dict):
            询价成交对象
                { \n
                    订单编号: id=216870196848627712  \n
                    交易分组: floorCode='LP_IND1' \n
                    策略合约: symbol='USDCNHSP' \n
                    订单类型: orderType [2:限价单(默认) 6:询价交易 50:择优询价交易单 51:择优限价单 52:停止限价单 53:冰山订单 54:止损单 (default)] \n
                    买卖方向: side='B' \n
                    订单状态: orderStatus=2 \n
                    开平仓类型: effect=0 [0-中性 1-开仓 2-平仓] \n
                    交易量: quantity=3 \n
                    价格: price=6.98749 \n
                    远期价格: farPrice=6.98749 \n
                    即期价格: spotRate=6.98749 \n
                    点: points=0 \n
                    远期点: farPoints=0 \n
                    创建时间: createTime=1532043000101 \n
                    本方交易员: partyId='yt' \n
                    对手方交易员: partyRoleId='yt' \n
                } \n

    使用方法:
        >>> def onRfqQuoteOrder(context,rfqQuoteOrder):
                trade_id = data.id
    """
    pass
