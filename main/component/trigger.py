# -*- coding: utf-8 -*-
"""右键开关：按一下开，再按一下关。

为什么要有它：
    主循环是每 0.1 秒一帧，一检出人就动鼠标。可用户自己也在用鼠标转视角，
    程序一动就等于两只手抢一个鼠标 —— 手感就是"我划到哪它给我拽回来"。
    所以得有个"我只想让它管的时候它才管"的开关。默认绑鼠标右键：
    按一次开始跟随，再按一次停手。

为什么不用键盘钩子：
    钩子（SetWindowsHookEx）要自己跑消息循环，而且低权限进程装的钩子收不到
    高权限窗口的输入 —— 游戏基本都是管理员权限跑的，钩子会一声不吭地失效。
    GetAsyncKeyState 是直接问系统"这个键现在什么状态"，不需要窗口、不需要
    钩子，游戏用原始输入（raw input）读鼠标也不影响它。

运行方式对它的影响：
    后台模式跑的是 pythonw，没有自己的窗口，但 GetAsyncKeyState 不是窗口消息，
    照样能读到。唯一前提是本进程权限不能低于游戏，见 README。
"""

import ctypes
import sys

# 高位：此刻按着 / 低位：上次问过之后按过。两个位的含义都是系统的约定
DOWN = 0x8000
PRESSED = 0x0001


def get_async_key_state(vk):
    """问一次系统：这个键现在什么状态。返回 0 ~ 0xFFFF 的整数。

    非 Windows（比如在 Linux 上跑测试）直接返回 0：永远"没按过"，
    开关就一直关着，不会误触发。

    restype 必须声明成 c_short：这个 API 返回的是 16 位有符号数，
    高位置 1 时（按着不放）不声明就会按默认的 int 读，读到别的位上去。
    """
    if not sys.platform.startswith('win'):
        return 0
    user32 = ctypes.windll.user32
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    return user32.GetAsyncKeyState(int(vk)) & 0xFFFF


class RightButtonToggle:
    """右键开关。poll() 每帧看一次，返回"现在是不是开着"。

    两次 poll 之间隔着一整帧（默认 0.1 秒），用户"点一下"是按下加抬起 ——
    快的时候整段都夹在两帧中间，只看"此刻按着没有"会完全看不到。所以两个位都要看：

        低位 0x0001  "上次问过之后按过"  —— 夹在两帧中间的那一下靠它
        高位 0x8000  "此刻正按着"        —— 长按不放靠它，配合去抖

    低位管"漏没漏"，高位管"别重复"，两个条件或起来，一次点击只翻一次。
    """

    def __init__(self, vk, key_state=None):
        """参数：

        vk          虚拟键码。鼠标右键是 0x02（VK_RBUTTON），见 config.py
        key_state   读键状态的函数，默认是 get_async_key_state。
                    测试时塞一个假的进来，就不用真去按键。
        """
        self._vk = int(vk)
        self._key_state = key_state or get_async_key_state
        self._down = False
        self.on = False

    def poll(self):
        """看一次键状态，该翻就翻。返回翻完之后开没开。"""
        state = self._key_state(self._vk)
        down = bool(state & DOWN)
        clicked = bool(state & PRESSED) or (down and not self._down)

        # 先记状态再判断：这样同一次按下不会因为"高位还按着"又翻一次
        self._down = down
        if clicked:
            self.on = not self.on
        return self.on
