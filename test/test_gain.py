# -*- coding: utf-8 -*-
"""增益自标定的收敛测试（不需要 Windows、不需要游戏、不需要 buke_km）。

为什么这个必须测：
    用户的原话是"你还慢慢的慢慢的滑过去，我还不如自己瞄"。
    老的实现整场只用 --gain 那一个固定倍率 —— 可游戏灵敏度是个未知数：
    推一个计数镜头转几像素，只有游戏自己知道，同一台机器换个游戏就变。
    倍率乘上灵敏度太小，准星就一格一格慢慢磨，正是用户骂的那个现象。

    所以这里造一个【假的游戏】：镜头按"推出去的计数 × 一个我们故意藏起来的
    灵敏度 k"转，然后真的跑几帧，看准星几帧能贴上目标。断言的是帧数，
    不是某个中间值 —— 用户在意的是快慢，测的就得是快慢。

运行方式（在项目根目录下，不需要 pytest）：

    python test\\test_gain.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component import aim, follow                                   # noqa: E402

CROSSHAIR = (960, 540)
DEADZONE = 12


class _Game:
    """假的游戏：镜头按 k 像素/计数 转，视角一转目标在屏幕上就往反方向挪。

    目标固定在准星右边 400 像素的地方（就是"开镜看见人了，还差半个屏幕"）。
    k 是这台"机器"的灵敏度，测试里当未知数用 —— 实现那边不许偷看它。
    """

    def __init__(self, sensitivity, miss=400):
        self.k = sensitivity
        self.target = [CROSSHAIR[0] + miss, CROSSHAIR[1]]
        self.moves = []

    def move_relative(self, dx, dy):
        self.moves.append((dx, dy))
        self.target[0] -= self.k * dx
        self.target[1] -= self.k * dy

    def frame(self):
        return [{'x': self.target[0], 'y': self.target[1], 'confidence': 0.9}]

    def error(self):
        return (self.target[0] - CROSSHAIR[0], self.target[1] - CROSSHAIR[1])

    def off(self):
        ex, ey = self.error()
        return (ex * ex + ey * ey) ** 0.5


def _follower(game, gain=0.5, auto_gain=True):
    return follow.Follower(game.move_relative, CROSSHAIR, 0.6, gain, DEADZONE,
                           auto_gain=auto_gain)


def _frames_to_lock(follower, game, limit=20):
    """真的跑几帧，返回第几帧贴上（误差进死区）；跑满还没贴上返回 None。"""
    for i in range(1, limit + 1):
        follower.update(game.frame())
        if game.off() <= DEADZONE:
            return i
    return None


# ----------------------------------------------------------------------
# 一、开镜就贴上：不管游戏灵敏度是多少
# ----------------------------------------------------------------------

def test_cold_start_locks_within_five_frames():
    """从没标定过的第一帧起算，400 像素的差距五帧内贴上。

    k 跨了三个数量级（一个计数转 0.05 像素到 12 像素）—— 现实里不同游戏、
    不同设置就在这个范围里。老实现用固定倍率，低灵敏度那头要三十几帧，
    用户看到的就是"慢慢滑过去"。
    """
    for k in (0.05, 0.2, 1.0, 2.0, 4.0, 8.0, 12.0):
        game = _Game(k)
        got = _frames_to_lock(_follower(game), game, limit=5)
        assert got is not None, f'k={k} 五帧还没贴上，实际误差 {game.off():.1f}'


def test_a_calibrated_follower_locks_a_new_target_in_three():
    """已经标定过之后，目标换个位置再开镜，三帧就该贴上。

    这才是实战里最常见的一下：程序早就在跑了，用户按右键、开镜、贴上。
    标定结果一直留着（帧数），所以第二下比第一下更快。
    """
    for k in (0.2, 1.0, 4.0, 8.0):
        game = _Game(k)
        f = _follower(game)
        assert _frames_to_lock(f, game, limit=5) is not None, k

        game.target = [CROSSHAIR[0] + 400, CROSSHAIR[1]]     # 又差 400 像素
        got = _frames_to_lock(f, game, limit=3)
        assert got is not None, f'k={k} 标定过之后还要 {got} 帧'


def test_it_never_swings_past_the_target():
    """准星一步都不许越过目标 —— 那比慢更糟，手感是"给我拽到另一边"。

    探测帧是唯一没有标定数据的帧，靠"推出去的计数封顶"保证它不甩过头；
    标定出来之后每帧只消七成误差，更不会越过去。
    """
    for miss in (400, 2000):
        for k in (0.05, 0.2, 1.0, 4.0, 8.0, 16.0, 20.0):
            if miss == 400 and k > 20:
                continue
            game = _Game(k, miss=miss)
            f = _follower(game)
            last = game.off()
            for _ in range(8):
                f.update(game.frame())
                now = game.off()
                assert now <= last + 1e-6, (
                    f'k={k} miss={miss}: 误差从 {last:.1f} 涨到 {now:.1f}')
                last = now


def test_a_diagonal_target_converges_too():
    """斜着的目标也收敛：两个轴共用一个倍率，投影反推出来的灵敏度仍然对。"""
    game = _Game(3.0)
    game.target = [CROSSHAIR[0] + 300, CROSSHAIR[1] + 200]
    got = _frames_to_lock(_follower(game), game, limit=5)
    assert got is not None, f'斜着要 {got} 帧，误差 {game.off():.1f}'


# ----------------------------------------------------------------------
# 二、反推灵敏度这件事本身
# ----------------------------------------------------------------------

def test_it_learns_the_sensitivity_from_one_frame():
    """推 50 个计数、误差从 400 缩到 150 -> 一个计数合 5 像素 -> 倍率 0.7/5。"""
    t = aim.GainTuner()
    t.observe((50, 0), (400, 0), (150, 0))
    assert abs(t.gain_for((100, 0)) - aim.GAIN_FRACTION / 5.0) < 1e-9


def test_it_learns_nothing_from_a_target_that_ran_away():
    """误差没缩反而涨：那是目标自己在动，不是推得没效果。

    拿这种帧去改标定等于把目标的速度学进灵敏度里，越学越偏。
    宁可这一帧不学 —— 下一帧还能再来。
    """
    t = aim.GainTuner()
    t.observe((20, 0), (400, 0), (440, 0))
    assert t.gain_for((100, 0)) == 8 / 100.0


def test_it_learns_nothing_from_a_two_count_nudge():
    """只推了 2 个计数就不学：游戏那边的丢步和取整比信号本身还大。

    学进去的就是噪声。判据是【推了多少计数】，不是推了没有。
    """
    t = aim.GainTuner()
    t.observe((2, 0), (400, 0), (300, 0))
    assert t.gain_for((100, 0)) == 8 / 100.0


def test_probe_is_big_enough_to_be_measurable():
    """第一帧（还没标定）推出去的计数必须够大，否则永远学不到东西。

    这是"慢慢滑过去"那个毛病的根：误差小的时候按一个小倍率算出来只有两三个
    计数，不达 MIN_TRUST_COUNTS，测量作废，标定永远做不出来 ——
    于是一直用探测倍率，一帧挪一点点，看着就是慢慢滑。
    """
    t = aim.GainTuner()
    for err in (20, 60, 200, 400, 1280, 4000):
        # 用 gain_for 算出探测帧的倍率，再看 aim 实际会推出去多少计数
        gain = t.gain_for((err, 0))
        counts = aim._steps(err, gain)
        assert abs(counts) >= aim.MIN_TRUST_COUNTS, (
            f'误差 {err} 只推出 {counts} 个计数，标定学不到')
        assert abs(counts) <= aim.PROBE_COUNTS, (
            f'误差 {err} 推了 {counts} 个计数，超过探测帧的上限')


# ----------------------------------------------------------------------
# 三、--no-auto-gain 要能把老行为原样退回来
# ----------------------------------------------------------------------

def test_without_auto_gain_the_configured_gain_is_used_verbatim():
    """关掉自标定就是老行为：整个屏幕差多少就按 --gain 算多少，不打折。"""
    game = _Game(1.0)
    f = _follower(game, gain=0.5, auto_gain=False)
    f.update(game.frame())
    assert game.moves == [(200, 0)]      # 400 * 0.5


def test_auto_gain_is_what_makes_it_fast():
    """同一台机器、同一个起点，只差自标定，快慢差一个量级。

    这就是用户抱怨的量化版本：低灵敏度（k=0.2）的游戏里，固定倍率 0.5
    每帧只消掉 10% 误差，十帧都还在外面；自标定几帧就贴上了。
    """
    slow = _Game(0.2)
    f = _follower(slow, gain=0.5, auto_gain=False)
    assert _frames_to_lock(f, slow, limit=10) is None, '固定倍率居然十帧内贴上了'

    fast = _Game(0.2)
    assert _frames_to_lock(_follower(fast, gain=0.5), fast, limit=10) is not None


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
