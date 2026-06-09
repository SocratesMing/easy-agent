param = None


# def init():
#     global param
#     param = app.ctx.get('PARAM')

def get(key, default=None, param_key=0):
    """
    描述
        策略配置的参数获取函数,在策略维度使用客户端配置的启动参数

    参数
        - key (string): 策略配置的参数名称
        - default: 配置的参数名称获取不到时默认的返回值

    返回
        string: 策略配置的参数值

    示例
        【name    名字     will】
        【age     年龄     18  】
        >>> qlog.info(param.get("name", "alan"))
        Will
        >>> qlog.info(param.get("名字"))
        Will
    """

    return param.get(key, default, param_key)


def matrix(key) -> list:
    """
    描述:
        策略配置的参数获取函数,在策略维度使用客户端配置的矩阵参数,由固定Excel模板导入数据.仅支持中英文大小写,下划线和中括号

    参数:
        - key (string): Excel中sheet页的名称

    返回:
        ndarray: excel里sheet页里面 ndarray结构数据
    """

    return param.matrix(key)


def get_num() -> int:
    """
    描述:
       当前平台适配配置多方案, 此方法用于获取当前策略配置的自定义方案数量

    参数:
       - 无

    返回 int:
       方案数量
    """
    return param.get_num()


def get_info_curve_param() -> list:
    """
    描述:
       此方法用于获取订阅信息中万德接入的债券收益率曲线数据信息

    参数:
       - 无

    返回 list:
        [{"curve_id": 1026, "curve_type": "SPOTCURVE"}]
    """
    return param.get_info_curve_param()
