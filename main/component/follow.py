# -*- coding: utf-8 -*-
"""跟随执行：把 aim 算出来的相对位移真的推给鼠标，并管好出错时的嘴。

aim.py 只管算，不碰鼠标；app.py 管主循环。中间这一层负责：
    1. 每帧按算出来的位移调一次鼠标
    2. 出错时【只说一次】—— 每帧都报错的话会把 out\\bg.log 刷成几万行，
       真正有用的那几行就被埋了（现在的 bg.log 就是这么被刷爆的）

mover 是注入进来的：真跑的时候是 buke_km 的 move_relative，
所以本文件可以脱离 Windows 和 DLL 单独测。
"""

from . import aim


class Follower:
    """每帧调一次 update()，它自己知道该不该动、动多少。"""

    def __init__(self, mover, crosshair, min_confidence, gain, deadzone):
        """参数：

        mover            move_relative 那类函数，收 (dx, dy)，单位是鼠标计数
        crosshair        (cx, cy)，准星的屏幕绝对坐标，一般是屏幕正中心
        min_confidence   跟随的最低置信度
        gain             像素差 -> 鼠标计数的倍率
        deadzone         死区半径（像素）
        """
        self._mover = mover
        self._crosshair = crosshair
        self._min_confidence = min_confidence
        self._gain = gain
        self._deadzone = deadzone
        self._last_msg = None

    def update(self, targets):
        """处理这一帧。返回要转告用户的一句话；没什么新鲜的就返回 None。"""
        step = self._plan(targets)
        if step is None:
            # 没目标或已经对准，都算正常。清掉上次的错误，
            # 这样同样的错以后【再犯的时候】还能再说一次。
            self._last_msg = None
            return None

        try:
            self._mover(step[0], step[1])
        except Exception as e:
            return self._once(f'跟随出错：{type(e).__name__}: {e}')

        self._last_msg = None
        return None

    def _plan(self, targets):
        """这一帧该推多少；不用动就返回 None。"""
        target = aim.pick_best(targets, self._min_confidence)
        if target is None:
            return None
        return aim.plan_relative_move(target, self._crosshair,
                                      self._gain, self._deadzone)

    def _once(self, msg):
        """同一句话只说一次；换了内容才重新说。"""
        if msg == self._last_msg:
            return None
        self._last_msg = msg
        return msg
