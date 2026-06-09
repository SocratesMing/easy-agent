"""策略参数接口 — 获取策略配置参数、矩阵参数、方案数量"""

param = None


def get(key: str, default=None, param_key=0):
    """获取策略配置参数值。

    Args:
        key: 参数名称
        default: 获取不到时的默认返回值
        param_key: 参数键(默认0)

    Returns:
        参数值(str)

    Example:
        >>> param.get("name", "alan")
    """
    return param.get(key, default, param_key)


def matrix(key: str) -> list:
    """获取策略矩阵参数(由Excel模板导入)。

    Args:
        key: Excel中sheet页的名称

    Returns:
        ndarray: sheet页中的数据

    Note:
        仅支持中英文大小写、下划线和中括号。
    """
    return param.matrix(key)


def get_num() -> int:
    """获取当前策略配置的自定义方案数量。

    Returns:
        int: 方案数量
    """
    return param.get_num()


def get_info_curve_param() -> list:
    """获取订阅信息中万德接入的债券收益率曲线数据信息。

    Returns:
        list[dict]: 如 [{"curve_id": 1026, "curve_type": "SPOTCURVE"}]
    """
    return param.get_info_curve_param()
