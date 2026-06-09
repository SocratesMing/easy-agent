"""日志接口 — info/error/debug/warn 级别日志输出"""

log = None


def info(*objects):
    """打印info级别日志。

    Example:
        >>> qlog.info("策略启动")
    """
    log.info(*objects)


def info_f(nformat: str, *args):
    """打印info级别日志(占位符格式化)。

    Args:
        nformat: 格式字符串，如 "{}策略启动"
        *args: 占位符参数

    Example:
        >>> qlog.info_f("{}策略启动", "第一版")
    """
    log.info_f(nformat, *args)


def error(nformat: str):
    """打印error级别日志。

    Example:
        >>> qlog.error("策略启动失败")
    """
    log.error(nformat)


def error_f(nformat: str, *args):
    """打印error级别日志(占位符格式化)。

    Args:
        nformat: 格式字符串
        *args: 占位符参数

    Example:
        >>> qlog.error_f("{}策略启动失败", "第一版")
    """
    log.error_f(nformat, *args)


def debug(nformat: str):
    """打印debug级别日志。

    Example:
        >>> qlog.debug("调试信息")
    """
    log.debug(nformat)


def debug_f(nformat: str, *args):
    """打印debug级别日志(占位符格式化)。

    Args:
        nformat: 格式字符串
        *args: 占位符参数

    Example:
        >>> qlog.debug_f("{}调试信息", "第一版")
    """
    log.debug_f(nformat, *args)


def warn(nformat: str):
    """打印warn级别日志。

    Example:
        >>> qlog.warn("警告信息")
    """
    log.warn(nformat)


def warn_f(nformat: str, *args):
    """打印warn级别日志(占位符格式化)。

    Args:
        nformat: 格式字符串
        *args: 占位符参数

    Example:
        >>> qlog.warn_f("{}警告信息", "第一版")
    """
    log.warn_f(nformat, *args)
