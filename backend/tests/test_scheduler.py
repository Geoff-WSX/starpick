"""调度器语义测试：笔记本休眠/唤醒后的补跑行为。

背景：macOS 睡眠时 time.monotonic() 冻结，APScheduler 的等待线程据此计时，
因此休眠期间错过的轮次既不会运行、next_run_time 也不推进；
且过小的 misfire_grace_time 会把迟到超过容忍窗口的任务静默丢弃。
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.main import _is_sleep_gap, _run_sleep_watchdog


def test_sleep_gap_detection():
    assert _is_sleep_gap(3600.0, 0.0) is True    # 睡 1h：挂钟走、单调钟没走
    assert _is_sleep_gap(30.0, 29.9) is False    # 正常推进（毫秒级抖动）
    assert _is_sleep_gap(60.0, 30.0) is False    # 进程被抢占 30s 但未休眠，两钟同步推进
    assert _is_sleep_gap(0.0, 5.0) is False      # 防御性：单调钟更快时不算休眠


class _SpyScheduler:
    """只记录 wakeup 调用次数的调度器替身。"""

    def __init__(self):
        self.woken = 0

    def wakeup(self):
        self.woken += 1


def _clock_of(seq):
    """按序返回 (挂钟, 单调钟)，用完后固定返回最后一组。"""
    state = {"i": 0}

    def read():
        value = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        return value

    return read


def _run_watchdog_until(spy, clock, seconds=0.15):
    """用测试用短周期跑看门狗，跑满 seconds 后停线程并返回。"""
    stop = threading.Event()
    thread = threading.Thread(
        target=_run_sleep_watchdog,
        args=(spy, stop),
        kwargs={"clock": clock, "interval_seconds": 0.01, "settle_seconds": 0.01},
    )
    thread.start()
    time.sleep(seconds)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive(), "看门狗未响应 stop_event"


def test_watchdog_wakes_scheduler_after_sleep():
    """挂钟跳 2h、单调钟只走 1s（模拟休眠）→ 看门狗等网络就绪后唤醒调度器一次。"""
    spy = _SpyScheduler()
    _run_watchdog_until(spy, _clock_of([(0.0, 0.0), (7200.0, 1.0)]))
    assert spy.woken == 1      # 只补一次，不因持续处于"睡过"状态而反复唤醒


def test_watchdog_ignores_normal_ticks():
    """两钟同步推进（普通卡顿/GC 停顿）→ 不误判为休眠。"""
    spy = _SpyScheduler()
    _run_watchdog_until(spy, _clock_of([(0.0, 0.0), (0.02, 0.02), (0.05, 0.04)]))
    assert spy.woken == 0


def _late_job_ran(misfire_grace_time):
    """注册一个"错过 2 小时"的采集式任务，返回它在 5s 内是否补跑。"""
    ran = threading.Event()
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        ran.set,
        IntervalTrigger(hours=6),
        id="late",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=misfire_grace_time,
        next_run_time=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    sched.start()
    try:
        return ran.wait(5)
    finally:
        sched.shutdown(wait=False)


def test_late_job_runs_when_grace_unlimited():
    """采集/更新任务配 misfire_grace_time=None：休眠唤醒后错过的轮次补跑一次。"""
    assert _late_job_ran(None) is True


def test_late_job_dropped_with_bounded_grace():
    """有界容忍窗口会静默丢弃休眠期间错过的轮次——修复前的实际行为，勿回退成因。"""
    assert _late_job_ran(3600) is False
