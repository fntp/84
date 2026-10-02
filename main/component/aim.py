# -*- coding: utf-8 -*-
"""跟随瞄准：算出这一帧该把鼠标推多少，纯计算，不碰任何 DLL。

为什么要"相对移动"而不是"移到那个坐标"：
    用户玩的这类游戏是锁定视角的（第一/第三人称），鼠标不是屏幕上的一个点，
    而是"转视角的摇杆"。游戏每帧去读鼠标的**位移量**，用位移量转镜头。
    这时候如果还调 MouseMove(绝对坐标)，系统会先把鼠标瞬移到目标点，
    游戏读到的就是一个巨大的位移 —— 镜头一下子甩过去，手感就是"我给你拽回正了"。

    正确做法是 MouseMoveR(相对位移)：从屏幕中心（准星位置，游戏锁视角时鼠标
    就夹在那里）往目标方向推一点点，下一帧重新看图、重新算、再推一点点。
    这样是一点点逼近的闭环，不管增益设多少，瞄歪了下一帧自己就纠回来，
    不会像绝对坐标那样一帧甩过头。

为什么必须闭环（每帧重新算）：
    我们不知道游戏的鼠标灵敏度，也没法问。但闭环不需要知道 ——
    只要每帧按"还差多少像素 × 增益"去推，最终一定会收敛到死区里。
    增益只影响收敛快慢，不影响最终准不准。

为什么增益要自己标定（GainTuner）：
    上面那句"只影响快慢"是真的，但快慢恰恰是用户在意的：增益乘上游戏灵敏度
    太小，准星就是一格一格慢慢滑过去，用户要的是【开镜就贴上】。
    而游戏灵敏度是个未知数 —— 推 N 个计数镜头转多少像素，只有游戏自己知道，
    同一台机器换个游戏、改个设置就变。所以不能猜死一个值：
    拿上一帧的实测结果当场反推（推了 c 个计数、误差从 e0 变成 e1，
    说明这台机器的灵敏度约等于 (e0-e1)/c），下一帧就按这个去推。
"""


def pick_best(targets, min_confidence):
    """从一堆目标里挑最值得瞄的那个，挑不出来返回 None。

    参数：
        targets          target_center 产出的列表，每项 {'x','y','confidence'}
        min_confidence   置信度门槛；None 表示不设门槛

    规则：先按门槛筛，再取 confidence 最大的。并列时取靠前的那个 ——
    不是随机，是为了同一帧重复调用结果稳定，方便复现问题。
    """
    if not targets:
        return None

    best = None
    for t in targets:
        if min_confidence is not None and t['confidence'] < min_confidence:
            continue
        if best is None or t['confidence'] > best['confidence']:
            best = t
    return best


def _steps(px, gain):
    """一维：屏幕像素差 -> 鼠标计数。

    方向永远保留：不足一个计数时也要凑成 ±1。
    游戏那边把不足一格的位移丢掉，如果这里返回 0，准星就永远差那么一点靠不拢
    （离得越近推得越少，最后停在死区外一点点，看着就是"总也瞄不准"）。
    """
    counts = int(round(px * gain))
    if counts == 0 and px != 0:
        counts = 1 if px > 0 else -1
    return counts


def plan_relative_move(target, crosshair, gain, deadzone):
    """算这一帧的鼠标相对位移，返回 (dx, dy)；不需要动就返回 None。

    参数：
        target       {'x','y','confidence'}，屏幕绝对坐标
        crosshair    (cx, cy)，屏幕绝对坐标，一般是屏幕正中心
        gain         像素差 -> 鼠标计数的倍率，两边共用
        deadzone     死区半径（像素）。目标落在这个圈里就当已经对准，
                     一动不动的意义是：不抖。准星附近一点点像素的框中心抖动，
                     换算成计数会让画面来回微颤，看着很难受。

    注意 target 和 crosshair 必须是同一个坐标系。target_center 加 region 偏移后
    已经是屏幕绝对坐标，屏幕中心也是屏幕绝对坐标，所以带不带 --region 都对得上。
    """
    dx = target['x'] - crosshair[0]
    dy = target['y'] - crosshair[1]

    # 平方比较，省一次开方；反正只比大小
    if dx * dx + dy * dy <= deadzone * deadzone:
        return None

    return _steps(dx, gain), _steps(dy, gain)


# ----------------------------------------------------------------------
# 增益自标定
# ----------------------------------------------------------------------

# 标定出来之后，每帧消掉误差的这个比例。为什么不是 1.0：标定本身有误差，
# 按 100% 去推，只要估大了一点就会冲过头，冲过头又会把标定带偏，
# 两帧一来一回地摆。留三成余量，三帧推完还剩 2.7%，够快也不会摆。
GAIN_FRACTION = 0.7

# 还没标定出来那一帧的倍率。故意取得很小：那一帧只是为了"探一下"灵敏度，
# 推小点才不会在灵敏度高的游戏里一帧就甩过目标（一个计数转多少像素是游戏
# 说了算，可能很大）。代价是这一帧推得少，所以只推这一帧，探出来立刻换掉。
PROBE_GAIN = 0.05

# 探测帧最多推这么多计数。误差特别大的时候由它起决定作用：
# 按 PROBE_GAIN 推出去会是一大坨（差 2000 像素就是 100 个计数），
# 灵敏度高的游戏里照样甩过头。封顶之后第一帧最多走这么大一步。
PROBE_COUNTS = 64

# 一帧至少推出这么多计数，反推灵敏度才算数。
# 只推 1、2 个计数的时候，游戏那边的丢步和取整比信号本身还大，
# 拿这种数据去改标定，等于把噪声学进去了。
# 探测帧也拿它当下限：误差小的时候按 PROBE_GAIN 算出来只有两三个计数，
# 学了也不作数，那就永远标定不出来 —— 表现是"一格一格慢慢滑过去"，
# 正是要修的那个毛病。宁可第一帧多推一点，也要换到一次能用的测量。
MIN_TRUST_COUNTS = 8

# 倍率的上下限。下限防的是"标定算出个 0，从此再也不动"；
# 上限防的是"某一帧目标自己往准星上撞，误差突然消失，被当成灵敏度过高"。
GAIN_MIN = 0.05
GAIN_MAX = 40.0

# 新测出来的灵敏度和老估计各占多少（0.5 = 各一半）。
# 目标会动，单帧的测量天生带噪声；取一半，几帧就能收敛，
# 又不会被某一帧的偶然结果带跑。
GAIN_BLEND = 0.5


class GainTuner:
    """自己把"像素差 -> 鼠标计数"这个倍率调出来。

    从第二帧起，用上一帧推了多少计数、误差因此消掉多少像素，反推出这台机器上
    "一个鼠标计数等于几个屏幕像素"，然后令 gain = GAIN_FRACTION / 灵敏度。
    这样不管游戏灵敏度是多少，一帧都消掉七成误差，两三帧就贴进死区 ——
    而不是按用户手填的那个值一格一格磨。

    过冲也能救：推过头会让误差反向，但反推出来的灵敏度仍然是对的
    （误差被消掉的总量除以推出去的计数），下一帧就纠正回来。

    第一帧还没有这个数，只能先探一下，由 gain_for() 给出那一帧的倍率。
    探测帧推出去的计数被夹在 MIN_TRUST_COUNTS 和 PROBE_COUNTS 之间 ——
    下限保它够大、这一帧的测量能用来标定，上限保它不会一帧甩过目标。
    至于甩不甩得过去，只看倍率和灵敏度的乘积：设这一帧的倍率是 g、
    游戏灵敏度是 k（一个计数目标移动几个像素），镜头走的距离换算到误差上
    就是 k*g 倍，k*g <= 1 就绝不会越过目标。倍率本身不超过 PROBE_GAIN，
    所以只要 k <= 1/PROBE_GAIN = 20（一个计数转 20 个像素，比实际游戏夸张），
    第一帧就只会逼近、不会甩过去。误差小到下限开始起作用的时候可能稍微过一点
    （推 8 个计数，撑死也就走 8k 个像素），下一帧立刻纠回来 ——
    这比"推得太小、永远学不到、一格一格慢慢滑"好得多。
    探出来之后每帧消掉七成，两三帧就贴上。
    """

    def __init__(self, fraction=GAIN_FRACTION, low=GAIN_MIN,
                 high=GAIN_MAX, blend=GAIN_BLEND):
        self._fraction = fraction
        self._low = low
        self._high = high
        self._blend = blend
        self._px_per_count = None      # 反推出来的灵敏度，单位 像素/计数
        self._gain = None              # 标定出来的倍率；没标出来之前是 None

    def gain_for(self, error):
        """这一帧该用的倍率。还没标定出来就按探测帧那一套给。

        探测帧限制的是推出去的【计数】，不是倍率本身：误差大的时候倍率压小，
        误差小的时候倍率反而要抬起来 —— 否则按 PROBE_GAIN 算出来只有两三个
        计数，低于 MIN_TRUST_COUNTS，这一帧的测量作废，标定永远做不出来。
        所以先定计数（在 [MIN_TRUST_COUNTS, PROBE_COUNTS] 之间），再折算成倍率。
        """
        if self._gain is not None:
            return self._gain

        span = max(abs(error[0]), abs(error[1]))
        if span <= 0:
            return PROBE_GAIN
        counts = min(PROBE_COUNTS, max(MIN_TRUST_COUNTS, span * PROBE_GAIN))
        return counts / span

    def observe(self, step, before, after):
        """拿上一帧的实测结果改进倍率。

        step     上一帧推出去的计数 (dx, dy)
        before   推之前的目标误差（像素）
        after    这一帧重新测到的误差（像素）

        误差是"目标 - 准星"的向量。镜头朝推的方向转了多少，误差就消掉多少，
        所以把"误差的减少量"投影到推的方向上，再除以推出去的计数，
        就是这个游戏的灵敏度。投影是为了把目标自己的移动尽量滤掉：
        它要是往别处走，那部分和推的方向垂直，投影结果里基本不出现。
        """
        cx, cy = step
        moved2 = cx * cx + cy * cy
        if moved2 < MIN_TRUST_COUNTS * MIN_TRUST_COUNTS:
            return

        shrink = ((before[0] - after[0]) * cx
                  + (before[1] - after[1]) * cy) / moved2

        # 误差没消掉：多半是目标自己在动，而不是我们推的没效果。
        # 这时候什么都不改，比拿一帧坏数据去改强。
        if shrink <= 0:
            return

        if self._px_per_count is None:
            self._px_per_count = shrink
        else:
            self._px_per_count = ((1 - self._blend) * self._px_per_count
                                  + self._blend * shrink)

        self._gain = min(self._high,
                         max(self._low, self._fraction / self._px_per_count))
