# -*- coding: utf-8 -*-
"""跟随执行测试（不需要 Windows、不需要游戏、不需要 buke_km）。

为什么这个必须测：
    跟随不生效这件事从外面看永远是同一个现象 —— 鼠标不动。
    可原因有四种：没检出人 / 检出了但分数没过门槛 / 选中了但已经在死区里 /
    真的推了。这四种在 --follow-trace 的输出里【必须是四句不一样的话】，
    否则这个开关就白加了：用户照样看不出问题出在哪。

    另外 mover 是注入的，所以这里能真的验"该推的时候推了几个计数、
    方向对不对、不该推的时候一次都没调"。

运行方式（在项目根目录下，不需要 pytest）：

    python test\\test_follow.py
"""

import os
import sys

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component.follow import Follower                               # noqa: E402

# 准星（屏幕正中心）和参数都跟真实默认值一致，这样算出来的推量就是用户会看到的
CROSSHAIR = (960, 540)


class _Mover:
    """假的 move_relative：只记下被怎么调过，不动任何东西。"""

    def __init__(self, boom=None):
        self.calls = []
        self._boom = boom

    def __call__(self, dx, dy):
        if self._boom:
            raise self._boom
        self.calls.append((dx, dy))


def _follower(mover, said, **over):
    # auto_gain 默认关着：本文件钉的是"固定倍率"这条路径（= --no-auto-gain
    # 走的那条），推几个计数能直接按 gain 算出来，断言才有意义。
    # 默认走的自动标定那条路径在 test_gain.py 里单独测。
    kw = dict(crosshair=CROSSHAIR, min_confidence=0.6, gain=0.5, deadzone=12,
              trace=said.append, auto_gain=False)
    kw.update(over)
    return Follower(mover, **kw)


def _t(x, y, c=0.9):
    return {'x': x, 'y': y, 'confidence': c}


# ----------------------------------------------------------------------
# 一、trace 的四种结局必须分得开
# ----------------------------------------------------------------------

def test_no_target_is_said_out_loud():
    """一个目标都没有：不是"静默不动"，要明说是没检出人。"""
    mov, said = _Mover(), []
    _follower(mov, said).update([])

    assert said == ['跟随  这一帧没检出任何目标']
    assert mov.calls == []


def test_below_threshold_says_the_score_and_the_bar():
    """检出了但分数不够：必须把【最高分】和【门槛】都报出来。

    这两根数字一起看才知道该调 --follow-conf 还是该换模型：
    0.55 离 0.6 只差一点，调门槛就行；0.2 那种是根本不像，调门槛只会乱瞄。
    """
    mov, said = _Mover(), []
    _follower(mov, said).update([_t(1000, 540, 0.55), _t(900, 500, 0.4)])

    assert said == ['跟随  检出 2 个，最高的 0.55 不到门槛 0.6，不瞄']
    assert mov.calls == []


def test_picking_a_target_prints_where_and_how_much():
    """选中了并且要推：把目标位置、准星位置、这一下推多少都说清楚。"""
    mov, said = _Mover(), []
    _follower(mov, said).update([_t(1000, 540)])

    assert said == ['跟随  目标 (1000,540) 分 0.90  准星 (960,540)  推 (20,0)  第 1 次']
    assert mov.calls == [(20, 0)]


def test_already_on_target_says_deadzone_not_silence():
    """已经在死区里：要和"没检出人"分开说。

    不然用户会以为程序没在看画面 —— 其实它看得好好的，只是准星已经压上了。
    """
    mov, said = _Mover(), []
    _follower(mov, said).update([_t(965, 540)])

    assert said == ['跟随  目标 (965,540) 分 0.90  准星 (960,540)  已在死区 12 像素内，不动']
    assert mov.calls == []


# ----------------------------------------------------------------------
# 二、推的量：方向、增益、死区
# ----------------------------------------------------------------------

def test_direction_and_gain_are_applied():
    """目标在右下，就往右下推；推量 = 像素差 × 增益。

    方向反了就是"越瞄越偏"，增益没乘上就是"推得特别慢"，
    这两种在游戏里都很难一眼看出来，所以在这里钉死。
    """
    mov, said = _Mover(), []
    _follower(mov, said, gain=0.5).update([_t(1100, 600)])

    assert mov.calls == [(70, 30)]        # dx=140 -> 70, dy=60 -> 30
    assert said[0].endswith('推 (70,30)  第 1 次')


def test_one_pixel_off_still_pushes_one_count():
    """差一个像素也要推 ±1 个计数（在一格够小的时候）。

    _steps 里那个"不足一格凑成 ±1"就是为这个：游戏会把不足一格的位移丢掉，
    如果这里返回 0，准星会永远停在死区外一点点，看着就是"总也瞄不准"。
    这里 gain=0.1 -> 一格 10 像素，差 40 像素远超过半格，该推就得推。
    """
    mov, said = _Mover(), []
    _follower(mov, said, gain=0.1).update([_t(1000, 540)])

    assert mov.calls == [(4, 0)]          # 40 * 0.1 = 4


def test_no_mouse_call_at_all_when_one_count_would_overshoot():
    """一格太大、差得又不足半格：一个计数都不推，而且【一次鼠标都不碰】。

    这是"右上角那台机器开镜之后准星一直在抖"的正面解药。gain=0.001 表示
    一格 1000 像素，差 40 像素时凑一格会直接跨到目标另一侧 960 像素，
    下一帧再凑一格又跨回来 —— 每帧都在目标两侧蹦，就是用户看到的抖。
    所以这里推 0 是对的：不是"没对准"，是"推鼠标已经到极限了"。

    连 mover 都不该调：一个 (0,0) 在 buke_km 那边仍然是一次真实的鼠标调用，
    HID 模式下等于每帧发一份空报文，白费一帧，还会把 trace 的"第 N 次"
    刷得虚高 —— 用户照着这个数会以为程序一直在动。
    """
    mov, said = _Mover(), []
    _follower(mov, said, gain=0.001).update([_t(1000, 540)])

    assert mov.calls == []
    assert '不动' in said[0], said[0]
    assert '半格' in said[0], said[0]


def test_most_confident_target_wins():
    """一帧里几个人就瞄分最高的那个 —— 瞄错人比漏瞄危险得多。"""
    mov, said = _Mover(), []
    _follower(mov, said).update([_t(1000, 540, 0.7), _t(1100, 600, 0.95)])

    assert said[0].startswith('跟随  目标 (1100,600) 分 0.95')
    assert mov.calls == [(70, 30)]


def test_counter_keeps_counting_across_frames():
    """第 N 次是累计值，跨帧接着数 —— 用户看一眼就知道程序这几帧真的在动。"""
    mov, said = _Mover(), []
    f = _follower(mov, said)
    f.update([_t(1000, 540)])
    f.update([_t(1000, 540)])
    f.update([_t(1000, 540)])

    assert [s.rsplit('第 ', 1)[1] for s in said] == ['1 次', '2 次', '3 次']
    assert mov.calls == [(20, 0)] * 3


# ----------------------------------------------------------------------
# 三、不加 --follow-trace 时一个字都不多说
# ----------------------------------------------------------------------

def test_without_trace_nothing_is_printed():
    """默认（没有 --follow-trace）时和以前完全一样：不动就不出声。

    这个开关是给排查用的，不能变成"平时每帧都刷屏"。
    """
    mov = _Mover()
    f = _follower(mov, [], trace=None)

    assert f.update([]) is None
    assert f.update([_t(1000, 540, 0.2)]) is None
    assert f.update([_t(965, 540)]) is None
    assert f.update([_t(1000, 540)]) is None
    assert mov.calls == [(20, 0)]


# ----------------------------------------------------------------------
# 四、出错：说一次，别把日志刷爆
# ----------------------------------------------------------------------

def test_error_is_reported_once_then_again_only_if_it_changes():
    """同一句报错只说一次。

    每帧都报的话，out\\bg.log 几万行全是同一句话，真有信息的那几行反而被埋了。
    错误消失之后要能把"说过了"清掉，这样下次再犯还能看见。
    """
    mov = _Mover(boom=RuntimeError('驱动没装'))
    f = _follower(mov, [])

    first = f.update([_t(1000, 540)])
    assert first == '跟随出错：RuntimeError: 驱动没装'
    assert f.update([_t(1000, 540)]) is None      # 同一句，不重复说

    assert f.update([]) is None                   # 这一帧没目标，正常
    assert f.update([_t(1000, 540)]) == first     # 错误还在，重新说一次


def test_failed_move_does_not_count_as_a_move():
    """推失败不计进"第 N 次"，不然 trace 会吹牛说推了多少下。"""
    mov, said = _Mover(boom=RuntimeError('x')), []
    f = _follower(mov, said)
    f.update([_t(1000, 540)])
    f.update([_t(1000, 540)])

    assert [s.rsplit('第 ', 1)[1] for s in said] == ['1 次', '1 次']


# ----------------------------------------------------------------------
# 五、开关从关翻到开：上一轮那笔配对必须作废
# ----------------------------------------------------------------------

class _Screen:
    """假游戏，只做 x 轴：推 dx 个计数，目标相对准星就少 k*dx 个像素。

    单轴够用 —— 这一节要证的是"开镜第一帧有没有拿上一轮的数据去标定"，
    哪个轴都一样。k 是这台机器的灵敏度，测试里当未知数（实现那边不许偷看）。
    """

    def __init__(self, k, miss):
        self.k = k
        self.miss = miss        # 目标相对准星的 x 偏移，正数在右边
        self.calls = []

    def __call__(self, dx, dy):
        self.calls.append((dx, dy))
        self.miss -= self.k * dx

    def frame(self):
        return [_t(CROSSHAIR[0] + self.miss, CROSSHAIR[1])]


def test_reset_drops_the_pairing_left_over_from_the_last_engagement():
    """开镜第一帧不许拿上一轮那笔配对去标定。

    现象就是"开了右键之后鼠标先乱动几下"。_learn() 比的是"上一帧推了多少计数、
    误差因此变成多少"；开关关着的那段时间一帧都没有，_last 还停在上一次跟随的
    最后一帧上。开镜第一帧拿它跟新目标的误差去比，配的根本不是同一件事 ——
    反推出来的灵敏度可以差几十倍，倍率被压到很小，第一下几乎没推动。

    清掉之后这一帧走的是探测帧那条老路，跟刚启动时一模一样。
    """
    game = _Screen(4.0, 400)
    f = _follower(game, [], auto_gain=True)
    f.update(game.frame())
    assert game.calls == [(8, 0)]           # 探测帧：400 * 0.02 = 8

    game.miss = -600                        # 关镜那段时间一帧都没有，目标挪到左边
    f.reset()
    f.update(game.frame())
    assert game.calls[-1] == (-12, 0), '清过之后这一帧没按探测帧走'

    # 对照：不清会是什么样。这里只钉"少一大截"这个方向，不钉具体数字 ——
    # 具体数字会随探测帧的参数变，但那句"第一帧几乎没推动"的毛病不会。
    game2 = _Screen(4.0, 400)
    f2 = _follower(game2, [], auto_gain=True)
    f2.update(game2.frame())
    game2.miss = -600
    f2.update(game2.frame())
    assert abs(game2.calls[-1][0]) < 12, (
        f'对照没复现出"第一帧只推一点点"，推了 {game2.calls[-1][0]} 个计数')


def test_reset_keeps_the_calibration():
    """清配对不等于清标定：灵敏度是这台机器和这个游戏的性质，跟开关没关系。

    第二下开镜要的是"直接按标定好的倍率贴上去"，不是重新探一遍 ——
    用户的原话是"点三次四次也要一样快"。
    """
    game = _Screen(4.0, 400)
    f = _follower(game, [], auto_gain=True)
    for _ in range(4):
        f.update(game.frame())
    assert abs(game.miss) <= 12, f'四帧还没贴上，还差 {game.miss}'

    game.miss = 400                         # 换个目标，又差 400 像素
    f.reset()
    f.update(game.frame())

    # k=4 像素/计数 -> 倍率 0.7/4 = 0.175 -> 400 * 0.175 = 70 个计数。
    # 要是 reset 把标定也清了，这一帧会退回探测帧，只推 8 个。
    assert abs(game.calls[-1][0]) > 40, (
        f'标定被 reset 一起清掉了，这一帧只推了 {game.calls[-1][0]} 个计数')


# ----------------------------------------------------------------------
# 六、收敛之后必须停住 —— 不能在目标两侧一直蹦（用户报的"一直在抖"）
# ----------------------------------------------------------------------

def test_it_converges_and_then_holds_still():
    """开镜之后贴近目标，然后【停住】：剩下的那些帧一次鼠标都不该再动。

    这是用户报的那个毛病的端到端回归测试。他描述的是一条完整的链：
    "丝滑地左右移动" -> "最终稳定地落在目标坐标上" -> "然后持续跟踪"。
    中间任何一帧多推一下，在游戏里就是准星从目标左边蹦到右边再蹦回来，
    也就是他说的"鼠标乱晃乱抖"。

    这里用 k=40 像素/计数（开镜高倍镜的常见值，也是原来抖得最厉害的区间），
    死区调到 2 像素 —— 这样收敛后剩下的那 10 个像素残差【落在死区外】，
    走的一定是"不足一格，不推"那条路，而不是"已经对准"那条。
    两条路都不动鼠标，但只有这条能证明抖是被"不推"止住的，
    而不是被死区盖住的。
    """
    game = _Screen(k=40.0, miss=390.0)
    f = _follower(game, [], auto_gain=True, deadzone=2)

    for _ in range(40):
        f.update(game.frame())

    assert abs(game.miss) <= 40, f'没能收敛，还差 {game.miss}'
    moves = len(game.calls)
    assert moves <= 6, f'推了 {moves} 下，收敛得太慢或者一直在蹦：{game.calls}'

    # 关键的一半：收敛之后再跑一段，鼠标必须【一次都不动】。
    for _ in range(30):
        f.update(game.frame())

    assert len(game.calls) == moves, (
        f'收敛之后还在推：多了 {game.calls[moves:]}。'
        f'游戏里看到的就是准星一直在抖')
    assert abs(game.miss) <= 40, f'来回蹦，最后停在 {game.miss}'


def test_short_residual_is_reported_as_not_enough_to_push():
    """"还差一点点但不推"要说得跟"已在死区里"不一样。

    两句都表现为鼠标不动，但原因完全不同：一个是已经压上了，一个是推鼠标
    根本推不动这么细。不说清楚，用户会以为跟随卡死了，然后去乱调增益和死区。
    """
    game = _Screen(k=40.0, miss=390.0)
    said = []
    f = _follower(game, said, auto_gain=True, deadzone=2)

    for _ in range(40):
        f.update(game.frame())
    said.clear()
    f.update(game.frame())

    assert said and '半格' in said[0], said
    assert '死区' not in said[0], said


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
