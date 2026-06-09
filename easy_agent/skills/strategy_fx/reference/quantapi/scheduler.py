event_loop_processor = None
strategy_context = None

def run_daily(name, trigger):
    """
    描述:
        定时任务: 每日运行一次指定的函数，只能在 init 内使用

    参数:
        - name (str): 定时器名称,触发onTime的时候保存一致
        - trigger (str): 时间表达式 例子: 160000

    示例:
        >>> scheduler.run_daily("run_daily", '160000')
    """
    trigger = trigger.replace(':', '')
    if strategy_context.mode == "run":
        # 仿真实盘按python的时间精度，实际比较的是time.time()
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


def run_second(name, trigger):
    """
    描述:
        定时任务: 每隔几秒指定的函数，只能在 init 内使用

    参数:
        name (str): 定时器名称,触发onTime的时候保存一致
        trigger (int): 时间表达式 例子: 5

    示例:
        >>> scheduler.run_second("timing",5)
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
        # 仿真实盘按python的时间精度，实际比较的是time.time()
        event_loop_processor.todo_time_event(name, strategy_context.get_time() + interval, interval)
    else:
        event_loop_processor.todo_interval_task(name, trigger)
