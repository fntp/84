# -*- coding: utf-8 -*-
"""自检测试（不需要 Windows、不需要游戏、不需要 buke_km）。

为什么这个必须测：
    --check 是用户手上唯一一把能区分"环境坏了"和"代码坏了"的尺子。
    它要是自己数错了 —— 比如按了两下右键却报"认到 1 次点击" ——
    用户会照着这个错结论去乱调环境，比没有这把尺子还糟。
    所以这里盯的是【计数】和【结论】这两件事，不是"能不能跑"。

真正去动鼠标、去读屏幕尺寸那几项没法在这里验（Linux 上没有），
验的是：计数准不准、三个标记对不对、非 Windows 上不炸也不误报失败。

运行方式（在项目根目录下，不需要 pytest）：

    python test\\test_selfcheck.py
"""

import os
import sys
import threading
import time

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import selfcheck                                              # noqa: E402
from main.component import trigger                                      # noqa: E402


# ----------------------------------------------------------------------
# 工具：假的时间轴 / 假的读键
# ----------------------------------------------------------------------

class _Clock:
    """假时钟和假 sleep 共用一个时间轴，这样 watch_toggle 不用真的等。"""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _keys(*states):
    """假的读键函数：按顺序吐状态，吐到最后一个就一直吐它。"""
    seq = list(states)

    def read(_vk):
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    return read


def _gate(*states):
    return trigger.RightButtonToggle(vk=0x02, key_state=_keys(*states))


def _watch(gate, seconds=0.1):
    c = _Clock()
    return selfcheck.watch_toggle(gate, seconds, clock=c.now, sleep=c.sleep)


# ----------------------------------------------------------------------
# 一、计数：报出来的次数必须就是用户按的次数
# ----------------------------------------------------------------------

def test_two_clicks_are_reported_as_two():
    """让用户"按两下"，就得报 2。

    这是最容易数错的地方：第二次点击是把开关【关掉】，如果只数"打开的瞬间"，
    这里会得出 1 —— 用户明明按了两下却被告知 1 次，只会以为自己按漏了，
    然后反复重试，把一个好环境当成坏的。所以数的是状态变化次数。
    """
    assert _watch(_gate(0, trigger.PRESSED, 0, trigger.PRESSED, 0)) == 2


def test_no_click_is_zero_not_an_error():
    """一次没按就是 0，交给 run() 去判成失败并给出提示。"""
    assert _watch(_gate(0)) == 0


def test_holding_the_button_counts_once():
    """按住不放只算一次点击，不能按帧数往上累。

    按住 0.1 秒就是好几个 poll，按帧数算的话用户轻轻一按会被报成 5 次，
    然后他会以为开关在乱翻。
    """
    down = trigger.DOWN
    assert _watch(_gate(0, down, down, down, down, down)) == 1


def test_initial_position_is_not_counted_as_a_click():
    """第一次 poll 只用来记初始状态，不比较、也不计数。

    否则开关本来就按着（或者读键那一下正好落在按下中间）会凭空多报一次，
    用户按两下却看到 3 —— 数字对不上他就会一直重试。
    """
    c = _Clock()
    gate = _gate(trigger.DOWN)          # 一上来就按着，之后一直是这个状态
    assert selfcheck.watch_toggle(gate, 0.02, clock=c.now, sleep=c.sleep) == 0


def test_a_click_shorter_than_the_watch_interval_is_still_counted():
    """整段点击都夹在 watch_toggle 两次 poll 之间，也必须数到。

    run() 里是 gate.start() 之后再 watch_toggle，理由就在这里：
    靠 watch_toggle 自己那个 0.02 秒的轮询去读，按下和抬起都在两次
    轮询之间的话，整整一下点击就是看不见的 —— 用户按得动，
    尺子说按不动，他会跑去改权限和驱动，全白费。

    这条特意把轮询间隔拉到 0.25 秒（远大于那次 20 毫秒的按下），
    让"只靠轮询"必漏，只有读键线程数得到。用的是真时钟，因为
    要验的正是"两次 poll 之间发生了什么"这件事本身。
    """
    value = {'v': 0}
    gate = trigger.RightButtonToggle(
        vk=0x02, key_state=lambda _vk: value['v'], interval=0.001)

    def click():
        time.sleep(0.05)
        value['v'] = trigger.DOWN
        time.sleep(0.02)
        value['v'] = 0

    gate.start()
    clicker = threading.Thread(target=click, daemon=True)
    clicker.start()
    try:
        clicks = selfcheck.watch_toggle(gate, 0.6, interval=0.25)
    finally:
        clicker.join()
        gate.close()

    assert clicks == 1, f'夹在两次轮询中间的点击没被数到（数到 {clicks} 次）'


# ----------------------------------------------------------------------
# 二、输出格式：三种结局必须一眼分得开
# ----------------------------------------------------------------------

def test_line_marks_ok_bad_and_unsupported():
    """OK / !! / -- 三种标记各管一档，不能混。

    "验不了"（非 Windows 上那几项）绝不能被画成"没过" ——
    画成没过的话，用户在 Linux 上跑一次就会看到一堆红叉，白挨一顿折腾。
    """
    assert selfcheck._line(True, '管理员权限', '是') == '[OK] 管理员权限  是'
    assert selfcheck._line(False, 'HID 驱动', '没装') == '[!!] HID 驱动  没装'
    assert selfcheck._line(None, '当前前台窗口') == '[--] 当前前台窗口'


def test_countdown_counts_down():
    """倒计时从 N 数到 1，而且每一声之间真的等一秒。"""
    said = []
    slept = []
    selfcheck._countdown(said.append, 3, slept.append)

    assert said == ['  3 ...', '  2 ...', '  1 ...']
    assert slept == [1, 1, 1]


# ----------------------------------------------------------------------
# 三、本机（Linux）上：能跑完、不误报失败
# ----------------------------------------------------------------------

def test_run_finishes_and_returns_a_count():
    """run() 任何平台上都要能跑完并返回"没过几项"这个整数。

    不返回、或者抛异常的话，用户敲 --check 看到的是一个回溯，
    那比不加这个功能还吓人 —— 他会以为整个程序都坏了。
    """
    said = []
    bad = selfcheck.run(said.append, wait=0, sleep=lambda s: None, move=False)

    assert isinstance(bad, int)
    assert said[0] == '=== 跟随自检 ==='

    if not sys.platform.startswith('win'):
        # 非 Windows：明确说"这里验不了"，而且【不算失败】
        assert bad == 0
        assert any(line.startswith('[--]') for line in said)


# ----------------------------------------------------------------------
# 迷你测试运行器（没有 pytest 时的退路）
# ----------------------------------------------------------------------

def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failed.append(name)
            print(f'  FAIL  {name}\n        {type(e).__name__}: {e}')
        else:
            print(f'  ok    {name}')

    print(f'\n{len(tests) - len(failed)}/{len(tests)} 通过')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run_all())
