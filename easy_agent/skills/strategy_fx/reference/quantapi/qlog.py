log = None


# def init():
#     global log
#     log = app.ctx.get('QLOG')


def info(*objects):
    """
    描述
        打印info级别日志,提供占位符的方式，以参数化的方式打印日志
    
    示例
        >>> qlog.info("策略启动")
        策略启动
    """
    log.info(*objects)


def info_f(nformat, *args):
    """
    描述
        打印info级别日志,提供占位符的方式，以参数化的方式打印日志

    参数
        日志内容

    示例
        >>> version = "第一版"
        >>> qlog.info_f("{}策略启动", version )
        第一版策略启动

    """
    log.info_f(nformat, *args)


def error(nformat):
    """
    描述
        打印error级别日志,提供占位符的方式，以参数化的方式打印日志

    示例
        >>> qlog.error("策略启动失败")
        策略启动失败
    """
    log.error(nformat)


def error_f(nformat, *args):
    """
    描述
        打印error级别日志,提供占位符的方式，以参数化的方式打印日志

    参数
        日志内容
    
    示例
        >>> version = "第一版"
            qlog.error_f("{}策略启动失败", version)
        第一版策略启动失败
    """
    log.error_f(nformat, *args)


def debug(nformat):
    """
    描述
        打印debug级别日志,提供占位符的方式，以参数化的方式打印日志

    示例
        >>> qlog.debug("策略启动失败")
        策略启动失败
    """
    log.debug(nformat)


def debug_f(nformat, *args):
    """
    描述
        打印debug级别日志,提供占位符的方式，以参数化的方式打印日志

    参数
        日志内容

    示例
        >>> version = "第一版"
            qlog.error_f("{}策略启动失败", version)
        第一版策略启动失败
    """
    log.debug_f(nformat, *args)


def warn(nformat):
    """
    描述
        打印warn级别日志,提供占位符的方式，以参数化的方式打印日志

    示例
        >>> qlog.warn("策略启动失败")
        策略启动失败
    """
    log.warn(nformat)


def warn_f(nformat, *args):
    """
    描述
        打印warn级别日志,提供占位符的方式，以参数化的方式打印日志

    参数
        日志内容

    示例
        >>> version = "第一版"
            qlog.warn_f("{}策略启动失败", version)
        第一版策略启动失败
    """
    log.warn_f(nformat, *args)
