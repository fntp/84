# -*- coding: utf-8 -*-
"""跟随执行：把 aim 算出来的相对位移真的推给鼠标，并管好出错时的嘴。

aim.py 只管算，不碰鼠标；app.py 管主循环。中间这一层负责：
    0. 记住上一帧推了多少、误差变成了多少，交给 aim.GainTuner 去标定增益
       （不标的话，用户填的那个倍率不对就是"准星一格一格慢慢滑过去"）
    0.5 把标定出来的灵敏度（一个计数几个像素）传给 aim，让它判断"不足一格凑成
       一格"会不会把准星推过目标 —— 会就不推。少了这一步，高倍镜下每帧都在目标
       两侧蹦一格，画面一直在抖。
    1. 每帧按算出来的位移调一次鼠标
    2. 出错时【只说一次】—— 每帧都报错的话会把 out\\bg.log 刷成几万行，
       真正有用的那几行就被埋了（现在的 bg.log 就是这么被刷爆的）
    3. 加 --follow-trace 时，每帧说清楚这一帧为什么动、为什么不动。
       不加这个的话，"没检出目标""分数没过门槛""已经在死区里""还差一点点
       但不足一格"这几种情况从外面看起来完全一样 —— 鼠标不动、
       也不打印任何东西，用户根本没法判断功能到底有没有生效。

mover 是注入进来的：真跑的时候是 buke_km 的 move_relative，
所以本文件可以脱离 Windows 和 DLL 单独测。
"""

from . import aim


class Follower:
    """每帧调一次 update()，它自己知道该不该动、动多少。"""

    def __init__(self, mover, crosshair, min_confidence, gain, deadzone,
                 trace=None, auto_gain=True):
        """参数：

        mover            move_relative 那类函数，收 (dx, dy)，单位是鼠标计数
        crosshair        (cx, cy)，准星的屏幕绝对坐标，一般是屏幕正中心
        min_confidence   跟随的最低置信度
        gain             像素差 -> 鼠标计数的倍率。auto_gain 打开时这只是【起点】，
                         之后由 GainTuner 按每帧实测结果自己改
        deadzone         死区半径（像素）
        trace            可选的回调（就是 say），收一句话。给了就每帧汇报判定过程；
                         不给（默认）时和以前完全一样，一个字都不多打。
        auto_gain        要不要自己标定增益。见 aim.GainTuner 的说明 ——
                         开着时第一帧会先"探"一下（倍率取得很小、推出去的计数
                         也封了顶，所以只会逼近、不会甩过目标），从第二帧起
                         每帧消掉七成误差，两三帧就贴上。关掉就是老行为：
                         整场都用 gain 这一个值，一格一格慢慢磨过去。
        """
        self._mover = mover
        self._crosshair = crosshair
        self._min_confidence = min_confidence
        self._gain = gain
        self._tuner = aim.GainTuner() if auto_gain else None
        self._deadzone = deadzone
        self._trace = trace
        self._last_msg = None
        self._moves = 0        # 累计真的推了鼠标多少次
        self._last = None      # 上一帧推出去的 (计数, 推之前的误差)，给标定用

    def update(self, targets):
        """处理这一帧。返回要转告用户的一句话；没什么新鲜的就返回 None。"""
        best = aim.pick_best(targets, self._min_confidence)
        step = None
        stuck = None        # 想推但一格都不该推时，(还差多少像素, 一格多少像素)

        if best is None:
            # 目标丢了，上一帧那笔"推完误差变成多少"就无从比对了，作废。
            self._last = None
        else:
            error = (best['x'] - self._crosshair[0],
                     best['y'] - self._crosshair[1])
            self._learn(error)
            gain = self._gain_now(error)
            per_count = self._per_count_now(gain)
            step = aim.plan_relative_move(
                best, self._crosshair, gain, self._deadzone, per_count)
            if step == (0, 0):
                # 两个轴都是"不足一格，但推一格会跨过目标"：这一帧不推。
                # 这是高倍镜下准星一直在抖的正面解药，见 aim._steps。
                #
                # 这里连鼠标都不碰：(0,0) 到了 buke_km 那边仍然是一次真实的
                # 鼠标调用（HID 模式下等于每帧多发一份空报文），白费一帧；
                # 还会把 --follow-trace 的"第 N 次"刷成几千，看着像一直在动。
                stuck = (error[0], error[1], per_count)
                step = None
            # 记的是【推之前】的误差；下一帧重测到的误差才是结果。
            # 没推出去的帧（死区里、或者这一帧不推）没得比，也清掉。
            self._last = None if step is None else (step, error)

        if self._trace:
            self._trace(self._describe(targets, best, step, stuck))

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

    def reset(self):
        """新一轮跟随开始，把上一轮那笔配对作废。

        为什么必须清：_learn() 比的是"上一帧推了多少计数、误差因此变成多少"。
        没按右键的那段时间一帧都没有，_last 就一直停在上一次跟随的最后一帧上；
        再开镜时第一帧拿它跟这一帧的误差去比，配的根本不是同一件事 ——
        反推出来的灵敏度可以错得离谱，第一下就把准星甩到别处去。
        用户报的就是"开了右键之后鼠标先乱动几下"，这是其中一半的原因。

        开关每次从关到开都要清（runmode.gated_frames 在上升沿回调这里）。

        【不清】_tuner 里已经标定好的灵敏度：那是这台机器和这个游戏的性质，
        跟开关没关系。留着，第二下开镜才不用重新探一遍 —— 这是"标定结果一直
        留着"的那一半，两件事不能混。
        """
        self._last = None

    def _gain_now(self, error):
        """这一帧该用的倍率。开了自标定就交给 GainTuner，否则是用户填的那个。

        标定还没做出来的第一帧，倍率取决于误差有多大（探测帧要限制的是
        推出去的计数），所以 error 得传进去。
        """
        if self._tuner is None:
            return self._gain
        return self._tuner.gain_for(error)

    def _per_count_now(self, gain):
        """这台机器上一个鼠标计数大概移动几个屏幕像素。

        这是 aim 判断"不足一格的那一格会不会跨过目标"要用的数，也就是
        GainTuner 标出来的那个灵敏度。没标出来（自标定关着，或者刚开镜第一帧）
        就退回 1/gain：用户填的倍率本来就写着"像素 -> 计数"，反过来是一样的意思，
        --no-auto-gain 时那正是他要的灵敏度。gain 小到取不了倒数
        （有 GAIN_MIN 兜着，不该出现）就退回 1.0 —— 宁可多推一格，也不许除零。
        """
        if self._tuner is not None:
            k = self._tuner.px_per_count
            if k is not None:
                return k
        return 1.0 / gain if gain > 0 else 1.0

    def _learn(self, error):
        """拿上一帧的结果修正倍率：上一帧推了 step、误差从 prev 变成 error。

        只在上一帧真的推过鼠标时才有可比的对象 —— 死区里不动、或者刚丢过
        目标的那一帧，都没有"推完变成多少"这件事。
        """
        if self._tuner is None or self._last is None:
            return
        self._tuner.observe(self._last[0], self._last[1], error)

    def _describe(self, targets, best, step, stuck=None):
        """这一帧的判定过程，一句话说完。

        五种结局必须能分辨开，否则用户看到的现象统统只是"鼠标没动"：
            没检出目标 / 检出了但分数不够 / 选中了谁并推多少 /
            已经在死区里 / 还差一点点但不足一格
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
            if stuck is not None:
                # 和"已在死区"分开说：这句解释的是"为什么明明还差着像素却不动"，
                # 不说清楚的话用户会当成跟随卡住了，然后去调增益和死区。
                dx, dy, per_count = stuck
                return (f'{head}  还差 ({dx:.0f},{dy:.0f}) 像素，不到半格'
                        f'（一格约 {per_count:.0f} 像素），再推就过头了，不动')
            return f'{head}  已在死区 {self._deadzone} 像素内，不动'

        return f'{head}  推 ({step[0]},{step[1]})  第 {self._moves + 1} 次'

    def _once(self, msg):
        """同一句话只说一次；换了内容才重新说。"""
        if msg == self._last_msg:
            return None
        self._last_msg = msg
        return msg
