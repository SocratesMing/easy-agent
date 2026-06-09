"""定时任务接口 — 每日定时、秒级定时"""

event_loop_processor = None
strategy_context = None


def run_daily(name: str, trigger: str):
    """每日定时任务。只能在 init 内使用。

    Args:
        name: 定时器名称(触发onTime时name一致)
        trigger: 时间表达式，如 "160000" 表示16:00:00

    Example:
        >>> scheduler.run_daily("run_daily", '160000')
    """
    trigger = trigger.replace(':', '')
    if strategy_context.mode == "run":
        import datetime
        import time
        day_ts = 86400.0
        st = time.mktime(datetime.date.today().timetuple())
        timeStamp = 0
        if len(trigger) == 6:
            timeStamp = int(trigger[0:2]) * 3600 + int(trigger[2:4]) * 60 + int(trigger[4:6])
        elif len(trigger) == 4:
            timeStamp = int(trigger[0:2]) * 3600 + int(trigger[2:4]) * 60
        st = st + timeStamp
        if st < strategy_context.get_time():
            st = st + day_ts
        event_loop_processor.todo_time_event(name, st, day_ts)
    else:
        event_loop_processor.todo_schedule_day_task(name, trigger)


def run_second(name: str, trigger: int):
    """秒级定时任务。只能在 init 内使用。

    Args:
        name: 定时器名称(触发onTime时name一致)
        trigger: 间隔秒数，如 5

    Example:
        >>> scheduler.run_second("timing", 5)
    """
    from quant_base.core.exception.quantException import QuantException

    interval = 0
    if type(trigger) == int:
        interval = trigger
    else:
        try:
            interval = int(trigger)
        except:
            raise QuantException(msg='trigger must can be to number')
    if strategy_context.mode == "run":
        event_loop_processor.todo_time_event(name, strategy_context.get_time() + interval, interval)
    else:
        event_loop_processor.todo_interval_task(name, trigger)
