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

import os
import sys

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


# ----------------------------------------------------------------------
# 三、本机上（非 Windows）不能炸
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
