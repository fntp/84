# -*- coding: utf-8 -*-
"""跟随瞄准的算法测试（纯计算，不需要显卡、Windows、buke_km、真实鼠标）。

钉的是三件最容易写反的事：

    1. 挑目标挑的是【置信度最高】的，不是列表里第一个
    2. 位移的【方向】不能反（反了就是"越瞄越偏"，用户会以为程序在捣乱）
    3. 方向不能因为"太小"被抹成 0 —— 抹成 0 就永远差最后一格，看着像瞄不准

运行方式（在项目根目录下，不需要 pytest）：

    python test\\test_aim.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component import aim, follow                                     # noqa: E402


def _t(x, y, conf):
    return {'x': x, 'y': y, 'confidence': conf}


# ----------------------------------------------------------------------
# 一、挑目标
# ----------------------------------------------------------------------

def test_pick_best_returns_none_when_empty():
    """没检出目标是常态（bg.log 里九成都是 no_targets），必须返回 None 而不是报错。"""
    assert aim.pick_best([], 0.6) is None


def test_pick_best_takes_highest_confidence_not_first():
    """挑最高的那个，不是第一个。方向写反会一直瞄错人。"""
    ts = [_t(100, 100, 0.7), _t(900, 500, 0.95), _t(300, 300, 0.8)]
    assert aim.pick_best(ts, 0.6)['x'] == 900


def test_pick_best_respects_threshold():
    """全都低于门槛 -> 一个都不瞄。宁可漏瞄，不可瞄错。"""
    assert aim.pick_best([_t(1, 1, 0.3), _t(2, 2, 0.5)], 0.6) is None


def test_pick_best_threshold_equal_is_kept():
    """刚好等于门槛算通过（用的是 <，不是 <=）。边界写错会漏掉一批正好卡线的目标。"""
    assert aim.pick_best([_t(7, 8, 0.6)], 0.6)['x'] == 7


def test_pick_best_no_threshold_keeps_the_best():
    """门槛传 None 表示不设门槛。"""
    assert aim.pick_best([_t(1, 1, 0.01)], None)['confidence'] == 0.01


def test_pick_best_tie_keeps_the_first():
    """分数一样时取靠前的那个：同一帧反复调用结果要稳定，方便复现问题。"""
    assert aim.pick_best([_t(11, 0, 0.9), _t(22, 0, 0.9)], 0.6)['x'] == 11


# ----------------------------------------------------------------------
# 二、一步推多少
# ----------------------------------------------------------------------

def test_steps_keeps_direction_of_tiny_offset():
    """差得再少也要凑成 ±1。返回 0 的话准星永远差最后一格，看着就是"瞄不准"。"""
    assert aim._steps(1, 0.5) == 1
    assert aim._steps(-1, 0.5) == -1


def test_steps_is_zero_only_when_already_aligned():
    """真的重合了才是 0，不能顺手把"很小"当成"没有"。"""
    assert aim._steps(0, 0.5) == 0


def test_steps_scales_by_gain():
    """增益决定追得快不快：同一点，增益大的推得多。"""
    assert aim._steps(100, 0.5) == 50
    assert aim._steps(100, 1.0) == 100
    assert aim._steps(100, 2.0) == 200


# ----------------------------------------------------------------------
# 三、这一帧要不要动
# ----------------------------------------------------------------------

def test_plan_returns_none_inside_deadzone():
    """已经对准就不动 —— 不留死区的话，检测框中心那点抖动会让准星一直微颤。"""
    assert aim.plan_relative_move(_t(801, 601, 0.9), (800, 600), 0.5, 12) is None


def test_plan_moves_outside_deadzone_with_correct_sign():
    """目标在准星右下 -> 两个方向都必须是正的；左上 -> 都是负的。

    正负写反就等于推着准星往反方向跑，越推越偏。
    """
    assert aim.plan_relative_move(_t(900, 700, 0.9), (800, 600), 1.0, 12) == (100, 100)
    assert aim.plan_relative_move(_t(700, 500, 0.9), (800, 600), 1.0, 12) == (-100, -100)


def test_plan_moves_one_axis_when_the_other_is_aligned():
    """只有一个方向有偏差时，另一个方向必须是 0，不能顺手晃一下。"""
    assert aim.plan_relative_move(_t(900, 600, 0.9), (800, 600), 1.0, 12) == (100, 0)


def test_plan_deadzone_boundary_is_inclusive():
    """刚好在死区边界上（距离 == 半径）算"已对准"。用的是 <=，边界不能漏判。"""
    assert aim.plan_relative_move(_t(812, 600, 0.9), (800, 600), 1.0, 12) is None
    assert aim.plan_relative_move(_t(813, 600, 0.9), (800, 600), 1.0, 12) == (13, 0)


def test_plan_uses_screen_absolute_coordinates():
    """目标给的是【屏幕绝对坐标】（target_center 已经加过 region 偏移）。

    带 --region 时抓的是屏幕一角，但准星仍然按整屏中心算 —— 两边同一个坐标系，
    所以差值是屏幕上的真实距离，不用再补偏移。
    """
    # 抓的是屏幕左上角那块，准星还是整屏中心 (960, 540)
    assert aim.plan_relative_move(_t(1300, 700, 0.9), (960, 540), 1.0, 12) == (340, 160)


# ----------------------------------------------------------------------
# 四、Follower：把位移推给鼠标 + 报错只说一次
# ----------------------------------------------------------------------

class _Recorder:
    """假装是 move_relative，把每次收到的位移记下来。"""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, dx, dy):
        self.calls.append((dx, dy))
        if self.error:
            raise self.error


def _follower(mover, crosshair=(800, 600), conf=0.6, gain=1.0, deadzone=12,
              auto_gain=False):
    # auto_gain 默认关着：这一组测的是"固定倍率 + 报错去重"这条老路径，
    # 也就是 --no-auto-gain 走的那条，推出来的量能直接按 gain 算。
    # 自动标定那条路径（默认走的）在 test_gain.py 里单独测。
    return follow.Follower(mover, crosshair, conf, gain, deadzone,
                           auto_gain=auto_gain)


def test_follower_moves_towards_target():
    """正常一帧：算出位移并推出去，不返回任何要打印的话。"""
    m = _Recorder()
    assert _follower(m).update([_t(900, 700, 0.9)]) is None
    assert m.calls == [(100, 100)]


def test_follower_says_nothing_when_no_target():
    """没目标时不动鼠标，也不吭声 —— 这是常态，吭声会把日志刷爆。"""
    m = _Recorder()
    assert _follower(m).update([]) is None
    assert m.calls == []


def test_follower_skips_low_confidence_target():
    """低于门槛不瞄：检测漏一个没关系，瞄错人才是事故。"""
    m = _Recorder()
    assert _follower(m).update([_t(900, 700, 0.3)]) is None
    assert m.calls == []


def test_follower_reports_error_only_once():
    """同样一句报错只说一次。

    原来那份代码是每帧 print 一次，bg.log 被刷了几百行"error msg"，
    真正有用的几行全被埋了。这里是那条教训的回归测试。
    """
    m = _Recorder(error=RuntimeError('驱动没装'))
    f = _follower(m)
    first = f.update([_t(900, 700, 0.9)])
    assert first and '驱动没装' in first
    assert f.update([_t(900, 700, 0.9)]) is None
    assert f.update([_t(900, 700, 0.9)]) is None
    assert len(m.calls) == 3          # 没说，但一直在重试


def test_follower_reports_again_after_recovery():
    """好了之后再坏，要能重新说一次 —— 报错不能变成"只报一次然后永远哑掉"。"""
    m = _Recorder(error=RuntimeError('驱动没装'))
    f = _follower(m)
    assert f.update([_t(900, 700, 0.9)])
    m.error = None                     # 用户把问题修好了
    assert f.update([_t(900, 700, 0.9)]) is None
    m.error = RuntimeError('又断了')
    assert f.update([_t(900, 700, 0.9)])


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
