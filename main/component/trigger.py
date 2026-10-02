# -*- coding: utf-8 -*-
"""右键开关：按着（开镜）才跟随，一松开就停手。

为什么要有它：
    主循环一帧（抓屏 + 推理）就要几十毫秒，一检出人就动鼠标。可用户自己
    也在用鼠标转视角，程序一动就等于两只手抢一个鼠标 ——
    手感就是"我划到哪它给我拽回来"。
    所以得有个"我只想让它管的时候它才管"的开关。绑鼠标右键：
    按着不放的时候跟随，松开就停手。

为什么是"按着"而不是"按一下翻一次"：
    这个游戏是按住右键开镜的，按住多久完全看用户。做成"按一次翻一次"的话，
    跟随的开关状态就跟着按键次数的【奇偶】走：第一次按下（开镜）跟上了，
    松手收镜时程序还认为自己是开着的，下一次开镜（第二次按下）反而被关掉 ——
    用户看到的就是"我关镜的时候它跟着、我开镜的时候它不动"，
    再点三次四次哪一下有效完全说不准。
    按住多久、按过几次都不影响的东西，只有【电平】：按着就是开，松开就是关。

为什么不用键盘钩子：
    钩子（SetWindowsHookEx）要自己跑消息循环，而且低权限进程装的钩子收不到
    高权限窗口的输入 —— 游戏基本都是管理员权限跑的，钩子会一声不吭地失效。
    GetAsyncKeyState 是直接问系统"这个键现在什么状态"，不需要窗口、不需要
    钩子，游戏用原始输入（raw input）读鼠标也不影响它 ——
    而且它给的正是电平，正好是这里要的。

运行方式对它的影响：
    后台模式跑的是 pythonw，没有自己的窗口，但 GetAsyncKeyState 不是窗口消息，
    照样能读到。唯一前提是本进程权限不能低于游戏 —— 权限不够时右键能读、跟随
    却没反应，`start.py --check` 会把这一条查出来。

【数错一下按下的代价】：这个开关是全流程唯一的入口。它漏一次，用户看到的是
"开镜了没反应"，然后会以为是跟随坏了、驱动坏了、模型坏了，四处乱调；
它多算一次，用户看到的是"没按它自己动了"。所以下面每一处判断都写明了
为什么不能反过来写。
"""

import ctypes
import sys
import threading

# 高位：此刻按着没有。这是 GetAsyncKeyState 返回值的约定，也是这个开关
# 【唯一】参与判断的位 —— 电平语义就靠它。
# 低位（0x0001）是"上次问过之后按过"，只有按下去的那一瞬间亮，而且是读一次
# 清一次（游戏自己也在读鼠标的话会先被它读走）。它表达不了"现在按着没有"，
# 所以这里不用它。
DOWN = 0x8000

# 独立线程读键的间隔，单位秒。5 毫秒 = 每秒 200 次。
# 电平本身一直在，读得慢也不会漏，但这个间隔就是【按下到开始跟随】的反应延迟 ——
# 用户要的是"开镜就贴上"，所以压到 5 毫秒。
POLL_INTERVAL = 0.005


def get_async_key_state(vk):
    """问一次系统：这个键现在什么状态。返回 0 ~ 0xFFFF 的整数。

    非 Windows（比如在 Linux 上跑测试）直接返回 0：永远"没按着"，
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


class RightButtonHold:
    """右键开关。poll() 返回"此刻右键按着没有"。

    电平语义，没有内部开关状态：按着就是 True，松开就是 False。
    按住多久、按过几次都不影响结果 —— 这正是它和"按一次翻一次"的区别，
    见模块开头的说明。

    presses 是【按下次数】（从"没按着"变成"按着"记一次）。跟随的判断不看它，
    只给 --check 用：那边 20 毫秒才问一次，用户手快点一下可能整段都夹在两次
    问之间（问的时候已经松开了）。电平那一问会漏掉，按下次数不会。
    """

    def __init__(self, vk, key_state=None, interval=POLL_INTERVAL):
        """参数：

        vk          虚拟键码。鼠标右键是 0x02（VK_RBUTTON），见 config.py
        key_state   读键状态的函数，默认是 get_async_key_state。
                    测试时塞一个假的进来，就不用真去按键。
        interval    自己那个读键线程多久读一次，见 start()。
        """
        self._vk = int(vk)
        self._key_state = key_state or get_async_key_state
        self._interval = interval
        self.on = False
        self.presses = 0
        self._thread = None
        self._stop = None

    # ------------------------------------------------------------------
    # 读一次
    # ------------------------------------------------------------------

    def _read_once(self):
        """读一次键，返回此刻按着没有。

        只有高位参与判断。按下的瞬间把 presses 加一，是为了给 --check 一个
        不受自己询问频率影响的口径（见类说明）。
        """
        down = bool(self._key_state(self._vk) & DOWN)
        if down and not self.on:
            self.presses += 1
        self.on = down
        return down

    def poll(self):
        """问一句"此刻按着没有"。

        起了读键线程（start()）的话，真正读键的活儿在那边做，这里只把结果
        拿出来 —— 两边都去读是多余的，而且按下次数会被数两遍。
        没起线程（测试、--check、非 Windows）就顺手读一次。
        """
        if self._thread is None:
            return self._read_once()
        return self.on

    # ------------------------------------------------------------------
    # 独立读键线程
    # ------------------------------------------------------------------

    def start(self):
        """起一个自己的线程，按 interval 一直读键。重复调用无事发生。

        为什么非得起线程：主循环一帧要抓屏 + 推理，一帧几十到几百毫秒，
        而开关的状态是跟着右键一起变的 —— 用户松手的那一瞬间要是夹在两次
        主循环询问之间，主循环就会以为他还按着，跟随会多跟一帧。
        线程把间隔压到 5 毫秒，按下和松开最多差 5 毫秒就被看见，
        也就是"开镜就贴、松手就停"的实际手感。

        线程是 daemon：正常路径由 close() 收，异常退出时也不拦着进程结束。
        """
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name='right-button-hold', daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            self._read_once()
            # 用 wait 而不是 sleep：close() 一置位立刻就退出来，
            # 不用等满一个间隔（否则关进程时会拖一下）
            self._stop.wait(self._interval)

    def close(self):
        """停掉读键线程。没起过就是空操作。"""
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
