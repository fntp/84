# -*- coding: utf-8 -*-
"""右键开关测试（不需要 Windows、不需要游戏、不需要 buke_km）。

为什么这个必须测：
    开关漏掉一次点击，用户按了没反应，会以为程序坏了；
    开关多翻一次，程序会在用户不想让它动的时候抢鼠标 —— 这正是要修的问题。
    而"点一下"经常整段都夹在两帧之间（按下和抬起都在 0.1 秒内完成），
    所以最容易错的就是"快点击"和"长按"这两种极端，都钉在这里。

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


def _toggle(*states):
    return trigger.RightButtonToggle(vk=0x02, key_state=_keys(*states))


# ----------------------------------------------------------------------
# 一、基本语义：按一下开，再按一下关
# ----------------------------------------------------------------------

def test_starts_off_and_stays_off_when_never_pressed():
    """没按过就一直关着 —— 这是默认状态，也是"程序起来别乱动"的保证。"""
    t = _toggle(0)
    assert [t.poll() for _ in range(5)] == [False] * 5


def test_first_click_turns_on_second_turns_off():
    """用户要的就是这个：第一次右键识别，第二次不识别。

    注意状态序列：中间那个 0 是"松开了"。抬手之后低位被读走就清零了，
    所以紧接着的下一次点击还能被认出来。
    """
    t = _toggle(0, trigger.PRESSED, 0, trigger.PRESSED)
    assert t.poll() is False      # 一开始没按
    assert t.poll() is True       # 第一次点击 -> 开
    assert t.poll() is True       # 松手之后保持开着
    assert t.poll() is False      # 第二次点击 -> 关


def test_quick_click_between_polls_is_not_missed():
    """按下和抬起都夹在两帧之间（低位只剩 0x0001，高位已经是 0）。

    只看"此刻按着没有"的实现会整段漏掉，这就是为什么两个位都要看。
    """
    t = _toggle(0, trigger.PRESSED, 0)
    assert t.poll() is False
    assert t.poll() is True


# ----------------------------------------------------------------------
# 二、别重复翻：一次按下只算一次
# ----------------------------------------------------------------------

def test_holding_button_flips_only_once():
    """一直按着不放，只翻一次 —— 不能每帧都翻，那样会狂闪。"""
    t = _toggle(0, trigger.DOWN, trigger.DOWN, trigger.DOWN, trigger.DOWN)
    assert t.poll() is False
    assert [t.poll() for _ in range(4)] == [True] * 4


def test_both_bits_set_at_once_flips_only_once():
    """按下之后没抬起，低位还留着 —— 两个位同时置 1 也只能算一次。"""
    t = _toggle(0, trigger.DOWN | trigger.PRESSED, trigger.DOWN)
    assert t.poll() is False
    assert t.poll() is True
    assert t.poll() is True


def test_hold_release_hold_is_two_clicks():
    """按着、松开、再按着，算两次点击。

    注意第四帧：松开之后状态【不变】（还是开着）——
    开关记的是"要不要跟随"，不是"此刻按着没有"。
    """
    t = _toggle(0, trigger.DOWN, trigger.DOWN, 0, trigger.DOWN)
    assert [t.poll() for _ in range(5)] == [False, True, True, True, False]


def test_low_bit_stuck_while_held_flips_only_once():
    """按着不放期间，低位一直亮着（0x8001 每轮都这样）：也只能翻一次。

    这是真机上"按了没反应 / 按了又自己关掉"的元凶。低位是
    "上次问过之后按过"，它在不同外设和驱动下表现不一致 ——
    有的机器上按住期间会一直亮。原来那句
    `clicked = bool(state & PRESSED) or (down and not self._down)`
    在按着的分支里也去看低位，于是每轮翻一次：按一下 0.1 秒，
    按 20Hz 空转就是一个偶数，开开关关正好抵消，最后停在"关"，
    用户看到的就是"我按了，它没反应"。

    判据必须是"上一轮我们以为它没按着"，跟低位无关。
    """
    held = trigger.DOWN | trigger.PRESSED
    t = _toggle(0, held, held, held, held, held)
    assert t.poll() is False
    assert [t.poll() for _ in range(4)] == [True] * 4


def test_stuck_low_bit_then_release_then_press_still_counts():
    """低位常亮那种机器上，松开、再按一下，还是要翻。

    修的时候最容易修过头：把低位整个不看，快点击就全漏了；
    或者在按下沿那里忘了复位 _down，第二次按就再也认不出来。
    """
    held = trigger.DOWN | trigger.PRESSED
    t = _toggle(0, held, held, 0, held, held)
    assert [t.poll() for _ in range(6)] == [False, True, True, True, False, False]


# ----------------------------------------------------------------------
# 三、读键线程：点击不该跟着主循环的帧率走
# ----------------------------------------------------------------------

def _threaded(value, interval=0.001):
    """造一个带可变状态的开关，专门给线程那几条测试用。"""
    t = trigger.RightButtonToggle(
        vk=0x02, key_state=lambda _vk: value['v'], interval=interval)
    return t


def test_a_click_is_seen_even_if_nobody_polls():
    """起了线程之后，主循环一次都不问，开关也能自己翻过来。

    这是加线程的全部理由：主循环一帧要抓屏 + 推理，几百毫秒，
    而一次点击只有几十毫秒，靠主循环去问根本问不到；能不能问到
    还取决于低位有没有被游戏那边先读走，所以时灵时不灵。
    """
    value = {'v': 0}
    t = _threaded(value)
    t.start()
    try:
        time.sleep(0.05)
        assert t.on is False            # 什么都没按

        value['v'] = trigger.DOWN       # 按下
        time.sleep(0.05)
        assert t.on is True

        value['v'] = 0                  # 抬起 —— 只是抬手，不是点击
        time.sleep(0.05)
        assert t.on is True

        value['v'] = trigger.DOWN       # 再按一下
        time.sleep(0.05)
        assert t.on is False
    finally:
        t.close()


def test_poll_only_reads_the_result_while_the_thread_runs():
    """线程在跑的时候 poll() 不许再读一次键。

    两边都读的话，同一次按下会被翻两下、正好抵消 ——
    现象还是"按了没反应"，而且比原来更难查。
    """
    reads = []

    def read(_vk):
        reads.append(1)
        return trigger.DOWN

    t = trigger.RightButtonToggle(0x02, key_state=read)
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
    t.close()
    assert t._thread is None

    was = t.on
    value['v'] = 0
    time.sleep(0.02)
    assert t.on is was, '线程停了还在翻开关'


def test_gated_frames_start_flowing_after_a_click_and_stop_after_the_next():
    """接上真正的主循环：点一下帧号开始发，再点一下就不发了。

    前面测的都是开关自己，这条测的是【开关和主循环接在一起】是不是真的通。
    用户报的就是这个层面的事 —— "按了没反应"，而不是"poll() 返回值不对"。
    中间任何一环（谁去读键、读了算不算数、主循环问的是不是同一个对象）
    接错了，在这条上都会露出来。
    """
    from main import runmode

    value = {'v': 0}
    gate = trigger.RightButtonToggle(
        vk=0x02, key_state=lambda _vk: value['v'], interval=0.001)
    a = argparse.Namespace(loop=0)
    said = []

    gate.start()
    try:
        frames = runmode.gated_frames(a, gate, said.append, idle_interval=0.001)
        value['v'] = trigger.DOWN          # 第一下：开
        time.sleep(0.05)
        assert [next(frames) for _ in range(3)] == [1, 2, 3]

        value['v'] = 0
        time.sleep(0.02)
        value['v'] = trigger.DOWN          # 第二下：关
        value['v'] |= trigger.PRESSED      # 真机上低位常常还亮着，一起带上
        time.sleep(0.05)
        value['v'] = 0
        time.sleep(0.05)

        # 关掉之后一个帧号都不该再出来。用超时保护：真漏了的话
        # next() 会一直阻塞，测试挂死比失败更难查。
        box = {}
        t = threading.Thread(
            target=lambda: box.update(v=next(frames)), daemon=True)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive(), f'关了开关还在发帧号，发出了 {box.get("v")}'
    finally:
        gate.close()

    assert said == [runmode.switch_message(True), runmode.switch_message(False)]


# ----------------------------------------------------------------------
# 四、本机上（非 Windows）不能炸
# ----------------------------------------------------------------------

def test_non_windows_read_returns_zero():
    """非 Windows 上读键返回 0：永远"没按过"，不会误触发。"""
    assert trigger.get_async_key_state(0x02) == 0


def test_default_key_state_is_usable_here():
    """不塞假函数也能构造、能 poll，不会抛异常（本机是 Linux）。"""
    t = trigger.RightButtonToggle(0x02)
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
