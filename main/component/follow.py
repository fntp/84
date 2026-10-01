# -*- coding: utf-8 -*-
"""跟随执行：把 aim 算出来的相对位移真的推给鼠标，并管好出错时的嘴。

aim.py 只管算，不碰鼠标；app.py 管主循环。中间这一层负责：
    1. 每帧按算出来的位移调一次鼠标
    2. 出错时【只说一次】—— 每帧都报错的话会把 out\\bg.log 刷成几万行，
       真正有用的那几行就被埋了（现在的 bg.log 就是这么被刷爆的）
    3. 加 --follow-trace 时，每帧说清楚这一帧为什么动、为什么不动。
       不加这个的话，"没检出目标""分数没过门槛""已经在死区里"三种情况
       从外面看起来完全一样 —— 鼠标不动、也不打印任何东西，
       用户根本没法判断功能到底有没有生效。

mover 是注入进来的：真跑的时候是 buke_km 的 move_relative，
所以本文件可以脱离 Windows 和 DLL 单独测。
"""

from . import aim


class Follower:
    """每帧调一次 update()，它自己知道该不该动、动多少。"""

    def __init__(self, mover, crosshair, min_confidence, gain, deadzone,
                 trace=None):
        """参数：

        mover            move_relative 那类函数，收 (dx, dy)，单位是鼠标计数
        crosshair        (cx, cy)，准星的屏幕绝对坐标，一般是屏幕正中心
        min_confidence   跟随的最低置信度
        gain             像素差 -> 鼠标计数的倍率
        deadzone         死区半径（像素）
        trace            可选的回调（就是 say），收一句话。给了就每帧汇报判定过程；
                         不给（默认）时和以前完全一样，一个字都不多打。
        """
        self._mover = mover
        self._crosshair = crosshair
        self._min_confidence = min_confidence
        self._gain = gain
        self._deadzone = deadzone
        self._trace = trace
        self._last_msg = None
        self._moves = 0        # 累计真的推了鼠标多少次

    def update(self, targets):
        """处理这一帧。返回要转告用户的一句话；没什么新鲜的就返回 None。"""
        best = aim.pick_best(targets, self._min_confidence)
        step = None if best is None else aim.plan_relative_move(
            best, self._crosshair, self._gain, self._deadzone)

        if self._trace:
            self._trace(self._describe(targets, best, step))

        if step is None:
            # 没目标或已经对准，都算正常。清掉上次的错误，
            # 这样同样的错以后【再犯的时候】还能再说一次。
            self._last_msg = None
            return None

        try:
            self._mover(step[0], step[1])
        except Exception as e:
            return self._once(f'跟随出错：{type(e).__name__}: {e}')

        self._moves += 1
        self._last_msg = None
        return None

    def _describe(self, targets, best, step):
        """这一帧的判定过程，一句话说完。

        四种结局必须能分辨开，否则用户看到的现象统统只是"鼠标没动"：
            没检出目标 / 检出了但分数不够 / 选中了谁并推多少 / 已经对准不用推
        """
        if not targets:
            return '跟随  这一帧没检出任何目标'

        if best is None:
            top = max(t['confidence'] for t in targets)
            return (f'跟随  检出 {len(targets)} 个，最高的 {top:.2f} '
                    f'不到门槛 {self._min_confidence}，不瞄')

        cx, cy = self._crosshair
        head = (f'跟随  目标 ({best["x"]},{best["y"]}) 分 {best["confidence"]:.2f}  '
                f'准星 ({cx},{cy})')

        if step is None:
            return f'{head}  已在死区 {self._deadzone} 像素内，不动'

        return f'{head}  推 ({step[0]},{step[1]})  第 {self._moves + 1} 次'

    def _once(self, msg):
        """同一句话只说一次；换了内容才重新说。"""
        if msg == self._last_msg:
            return None
        self._last_msg = msg
        return msg
