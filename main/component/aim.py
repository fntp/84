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
    增益只影响收敛快慢，不影响最终准不准，所以用户凭手感调就行。
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
