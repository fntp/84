# -*- coding: utf-8 -*-
"""右键开关测试（不需要 Windows、不需要游戏、不需要 buke_km）。

为什么这个必须测：
    开关判错，用户看到的是"我开镜了它不动"（漏了）或者"我没按它自己动"
    （多了），然后会以为是跟随坏了、驱动坏了、模型坏了，四处乱调。
    它又是全流程唯一的入口，所以这里钉的是【语义】：按着就是开，松开就是关。

    最容易错的不是"能不能读到键"，而是把它做成"按一下翻一次"那种开关 ——
    在按住开镜的游戏里，跟随状态会跟着按键次数的奇偶走，表现成
    "关镜的时候跟着、开镜的时候不动"。第一节最后一条就是这个的回归测试。

运行方式（在项目根目录下，不需要 pytest）：

    python test\\test_trigger.py
"""

import argparse
import os
import sys
import threading
import time

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component import trigger                                       # noqa: E402

# 低位。GetAsyncKeyState 的"上次问过之后按过"，读一次清一次，
# 还会被游戏自己读走 —— 所以 trigger 里【故意不用它】。
# 这里留个名字，是为了明确地测"只靠它不能把开关打开"。
PRESSED = 0x0001


def _keys(*states):
    """造一个假的读键函数：按顺序吐出这些状态，吐到最后一个就一直吐它。

    真实世界里 poll() 每次拿到的都是"当前状态"，
    所以这里也模拟成一串状态按时间排开，每次读一格。
    """
    seq = list(states)

    def read(_vk):
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    return read


def _hold(*states):
    return trigger.RightButtonHold(vk=0x02, key_state=_keys(*states))


# ----------------------------------------------------------------------
# 一、语义：按着就是开，松开就是关
# ----------------------------------------------------------------------

def test_starts_off_and_stays_off_when_never_pressed():
    """没按过就一直关着 —— 这是默认状态，也是"程序起来别乱动"的保证。"""
    t = _hold(0)
    assert [t.poll() for _ in range(5)] == [False] * 5
    assert t.presses == 0


def test_level_follows_the_button_instead_of_counting_clicks():
    """按着开、松开停、再按着又开 —— 这是用户要的，也是修掉的那个 bug。

    做成"按一次翻一次"的话，第二次按住正好把开关翻回去：
    用户看到的就是"我关镜的时候它跟着、我开镜的时候它不动"。
    """
    t = _hold(0, trigger.DOWN, trigger.DOWN, 0, 0, trigger.DOWN)
    assert [t.poll() for _ in range(6)] == [False, True, True, False, False, True]


def test_holding_it_longer_does_not_change_anything():
    """一直按着不放：一路都是 True，不会按时间或按轮数自己变。"""
    t = _hold(0, trigger.DOWN, trigger.DOWN, trigger.DOWN, trigger.DOWN)
    assert t.poll() is False
    assert [t.poll() for _ in range(4)] == [True] * 4


def test_the_low_bit_alone_never_turns_it_on():
    """低位亮着、高位没亮 = 此刻没按着。哪怕它是刚按下的痕迹也不行。

    低位是"上次问过之后按过"，读一次就清，而且游戏自己也在读鼠标，
    经常先被它读走。拿它当"按着"就会时灵时不灵 —— 这条钉住的是
    "开关只认高位"这个决定。
    """
    t = _hold(0, PRESSED, PRESSED, 0)
    assert [t.poll() for _ in range(4)] == [False] * 4
    assert t.presses == 0


# ----------------------------------------------------------------------
# 二、按下次数：只给 --check 用，数的是"从没按着变成按着"
# ----------------------------------------------------------------------

def _holds(n, press_state):
    """读键流：0 起头，然后 n 组"按下 + 松开"。"""
    seq = [0]
    for _ in range(n):
        seq += [press_state, 0]
    return seq


def test_every_hold_counts_exactly_one_press():
    """点 N 下就该数出 N 次按下（N = 1..12），不管按住期间低位亮不亮。

    用户的原话是"我右键可能点无数次"，所以"一两下有效、点到第三第四下就失灵"
    这种事不能有。--check 就靠这个数报"认到几次按下"：数漏了，用户会以为
    是权限问题白折腾半天。
    """
    for press_state in (trigger.DOWN, trigger.DOWN | PRESSED):
        for n in range(1, 13):
            t = _hold(*_holds(n, press_state))
            # 第一下是"还没按"，之后 n 组是 按下 / 松开
            assert t.poll() is False, (n, press_state)
            assert [t.poll() for _ in range(2 * n)] == [True, False] * n, \
                (n, press_state)
            assert t.presses == n, (n, press_state)


def test_low_bit_stuck_while_held_is_still_one_press():
    """按住期间低位每轮都亮（0x8001 那种外设）：只算一次按下。

    数多的话 --check 会报出"认到 20 次按下"这种没人看得懂的结果。
    """
    held = trigger.DOWN | PRESSED
    t = _hold(0, held, held, held, held, held)
    assert t.poll() is False
    assert [t.poll() for _ in range(4)] == [True] * 4
    assert t.presses == 1


def test_opening_and_closing_the_scope_many_times_keeps_tracking_the_level():
    """开镜/关镜来回 12 遍：每一次开镜都在跟，每一次关镜都停手。

    这一条就是用户报的那个 bug 的回归测试：以前开关跟的是按键次数的奇偶，
    第 1、3、5 次开镜跟得上，第 2、4、6 次就反了。
    """
    seq = [0]
    for _ in range(12):
        seq += [trigger.DOWN, trigger.DOWN, 0, 0]
    t = _hold(*seq)

    assert t.poll() is False
    for k in range(12):
        assert [t.poll() for _ in range(4)] == [True, True, False, False], k
    assert t.presses == 12


# ----------------------------------------------------------------------
# 三、读键线程：开关不能跟着主循环的帧率走
# ----------------------------------------------------------------------

def _threaded(value, interval=0.001):
    """造一个带可变状态的开关，专门给线程那几条测试用。"""
    return trigger.RightButtonHold(
        vk=0x02, key_state=lambda _vk: value['v'], interval=interval)


def test_the_thread_keeps_the_level_current_without_anyone_polling():
    """起了线程之后，主循环一次都不问，开关也能跟着右键变。

    这是加线程的全部理由：主循环一帧要抓屏 + 推理，几十到几百毫秒，
    用户松手可能整段都夹在两次询问之间 —— 那就多跟了一帧。
    线程 5 毫秒读一次，开镜就贴、松手就停。
    """
    value = {'v': 0}
    t = _threaded(value)
    t.start()
    try:
        time.sleep(0.05)
        assert t.on is False            # 什么都没按

        value['v'] = trigger.DOWN       # 按住 = 开镜
        time.sleep(0.05)
        assert t.on is True

        value['v'] = 0                  # 松开 = 收镜，立刻停手
        time.sleep(0.05)
        assert t.on is False

        value['v'] = trigger.DOWN       # 再按一下，还是开
        time.sleep(0.05)
        assert t.on is True
        assert t.presses == 2
    finally:
        t.close()


def test_poll_only_reads_the_result_while_the_thread_runs():
    """线程在跑的时候 poll() 不许再读一次键。

    两边都读的话，同一次按下会被数两遍（--check 报出来的次数翻倍），
    而且主循环和线程会各读到一个不同时刻的状态 —— 白添一层难查的抖动。
    """
    reads = []

    def read(_vk):
        reads.append(1)
        return trigger.DOWN

    t = trigger.RightButtonHold(0x02, key_state=read)
    t._thread = object()            # 假装线程已经起了，不真起一个
    t.on = True

    assert [t.poll() for _ in range(3)] == [True, True, True]
    assert reads == [], '线程在跑的时候 poll() 又去读键了'


def test_close_stops_the_thread_and_without_start_is_a_noop():
    """close() 要真的把线程停掉；没起过线程时调它也不能炸。

    没起过就炸的话，报错会出现在退出的 finally 里 ——
    那时候用户看到的是一次莫名其妙的崩溃，而不是"程序正常结束"。
    """
    value = {'v': trigger.DOWN}
    t = _threaded(value)
    t.close()                       # 没起过，空操作
    assert t._thread is None

    t.start()
    time.sleep(0.02)
    t.close()
    assert t._thread is None

    was = t.on
    value['v'] = 0
    time.sleep(0.02)
    assert t.on is was, '线程停了还在改开关'


# ----------------------------------------------------------------------
# 四、接上真正的主循环
# ----------------------------------------------------------------------

def test_gated_frames_flow_while_held_and_stop_on_release():
    """接上主循环：按住右键才发帧号，松开就一个都不发。

    前面测的都是开关自己，这条测的是【开关和主循环接在一起】通不通。
    用户报的现象就是这个层面的："开镜了没反应"和"没按它自己动"。
    中间任何一环（谁去读键、主循环问的是不是同一个对象、关着的时候
    是不是真的什么都没干）接错了，在这条上都会露出来。
    """
    from main import runmode

    value = {'v': 0}
    gate = trigger.RightButtonHold(
        vk=0x02, key_state=lambda _vk: value['v'], interval=0.001)
    a = argparse.Namespace(loop=0)
    said = []
    opens = []

    gate.start()
    try:
        frames = runmode.gated_frames(a, gate, said.append, idle_interval=0.001,
                                      on_open=lambda: opens.append(1))

        # 没按的时候：连第一次 next() 都不该返回。超时保护是必须的 ——
        # 真漏了的话 next() 会一直阻塞，测试挂死比失败更难查。
        box = {}
        t = threading.Thread(
            target=lambda: box.update(v=next(frames)), daemon=True)
        t.start()
        time.sleep(0.05)
        assert box == {}, f'没按右键就发了帧号 {box.get("v")}'
        assert opens == []

        value['v'] = trigger.DOWN          # 按住：开镜
        t.join(timeout=0.5)
        assert box.get('v') == 1
        assert [next(frames) for _ in range(2)] == [2, 3]
        assert opens == [1]

        value['v'] = 0                     # 松开：收镜，一个帧号都不该再发
        time.sleep(0.05)
        box2 = {}
        t2 = threading.Thread(
            target=lambda: box2.update(v=next(frames)), daemon=True)
        t2.start()
        t2.join(timeout=0.3)
        assert t2.is_alive(), f'松开右键还在发帧号，发出了 {box2.get("v")}'
        assert box2 == {}
        assert opens == [1]

        # 同一个还堵着的线程接着读：再开一次镜，它应该立刻拿到第 4 帧。
        # 【不能】另外用主线程去 next() —— 生成器一次只能有一个线程在里面，
        # 两边同时读会撞成 "generator already executing"，而且谁先拿到这一帧
        # 是随机的，测试会时好时坏。
        value['v'] = trigger.DOWN
        t2.join(timeout=0.5)
        assert box2.get('v') == 4, '再开镜之后帧号没接着往下走'
        assert opens == [1, 1], '每次开镜都该清一次跟随器的配对'
    finally:
        gate.close()

    assert said == [runmode.switch_message(False),
                    runmode.switch_message(True),
                    runmode.switch_message(False),
                    runmode.switch_message(True)]


# ----------------------------------------------------------------------
# 五、本机上（非 Windows）不能炸
# ----------------------------------------------------------------------

def test_non_windows_read_returns_zero():
    """非 Windows 上读键返回 0：永远"没按着"，不会误触发。"""
    assert trigger.get_async_key_state(0x02) == 0


def test_default_key_state_is_usable_here():
    """不塞假函数也能构造、能 poll，不会抛异常（本机是 Linux）。"""
    t = trigger.RightButtonHold(0x02)
    assert t.poll() is False


# ----------------------------------------------------------------------
# 迷你测试运行器（没有 pytest 时的退路）
# ----------------------------------------------------------------------

def _run_all():
    """把本文件里所有 test_ 开头的函数跑一遍，打印结果。"""
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
