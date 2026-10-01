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

【数错一下点击的代价】：这个开关是全流程唯一的入口。它漏一次，用户看到的是
"按了没反应"，然后会以为是跟随坏了、驱动坏了、模型坏了，四处乱调；
它多翻一次，用户看到的是"按了开、松开又关"。所以下面每一处判断都写明了
为什么不能反过来写。
"""

import ctypes
import sys
import threading

# 高位：此刻按着 / 低位：上次问过之后按过。两个位的含义都是系统的约定
DOWN = 0x8000
PRESSED = 0x0001

# 独立线程读键的间隔，单位秒。5 毫秒 = 每秒 200 次，
# 远密于一次点击的按下时长（大约 50 毫秒），按下必定被看见。
POLL_INTERVAL = 0.005


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
    """右键开关。poll() 返回"现在是不是开着"。

    两次 poll 之间隔着一整帧，用户"点一下"是按下加抬起 ——
    快的时候整段都夹在两帧中间，只看"此刻按着没有"会完全看不到。

    只用【高位】做边沿判断：从"没按着"变成"按着"的那一刻算一次点击。
    低位（0x0001）只在"此刻没按着"的分支里当补丁用 —— 那种整段夹在
    两次 poll 之间的点击，只能靠它补上。

    低位【不能】在"按着"的分支里参与判断：它是"上次问过之后按过"，
    真机上按着不放的时候它可能一直亮着（同一个 API 在不同外设/驱动下
    表现不一致），那样每 poll 一次就翻一次 —— 按一下就开开关关闪个不停，
    用户看到的正是"按了没反应"或者"按了又自己关掉"。
    一次按住只能算一次，判据是"上一轮我们以为它没按着"。
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
        self._down = False
        self.on = False
        self._thread = None
        self._stop = None

    # ------------------------------------------------------------------
    # 读一次
    # ------------------------------------------------------------------

    def _read_once(self):
        """读一次键，该翻就翻。返回翻完之后开没开。"""
        state = self._key_state(self._vk)
        down = bool(state & DOWN)

        if down:
            # 按着：只有"上一轮以为没按"才是新的一次按下。
            # 这里【故意不看低位】—— 按着的时候它可能是亮的，看了就会每轮翻一次。
            flipped = not self._down
        else:
            # 没按着：低位亮着说明刚才是"按下又抬起"，整段夹在两次读之间。
            flipped = bool(state & PRESSED)

        self._down = down
        if flipped:
            self.on = not self.on
        return self.on

    def poll(self):
        """问一句"现在开着没有"。

        起了读键线程（start()）的话，真正读键的活儿在那边做，这里只把结果
        拿出来 —— 两边都去读的话，同一次点击会被翻两下，正好互相抵消。
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

        为什么非得起线程：主循环一帧要抓屏 + 推理，一帧几百毫秒，
        而一次点击（按下到抬起）只有几十毫秒 —— 夹在两帧之间的那一下
        就只剩低位兜着。可低位是"上次问过之后按过"，谁问都算数、
        问一次就清零，游戏自己也在读右键的话就会先被它读走，
        我们这边什么都看不到，表现就是"按了没反应"，而且时有时无。
        线程把间隔压到 5 毫秒，按下必定被高位这个【电平】看见 ——
        电平不会被任何人读走，这才是稳的。

        线程是 daemon：正常路径由 close() 收，异常退出时也不拦着进程结束。
        """
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name='right-button-toggle', daemon=True)
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
