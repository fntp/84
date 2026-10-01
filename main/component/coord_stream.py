# -*- coding: utf-8 -*-
"""坐标流文件：一行一帧，每行一个 JSON 数组。

为什么要有这个东西？
    后台模式是【没有窗口】的，print 出去的东西没人看得见
    （pythonw 进程里 sys.stdout 是 None，print 等于闭嘴）。
    所以后台模式下坐标必须落到文件里，别的程序盯着这个文件追加读，
    就能持续拿到每一帧的检测结果。

文件格式（一行 = 一帧，行尾换行）：

    [{"x":1250,"y":438,"confidence":0.94}]
    [{"x":900,"y":512,"confidence":0.87},{"x":1420,"y":700,"confidence":0.66}]
    []

    没有目标也照写一行 []，这样下游能分辨两种情况：
        []  -> 这一帧跑过了，屏幕上确实没人
        没新行 -> 进程卡住或者退出了
    这是刻意的设计，不要改成"没目标就不写"。

文件不会无限长：
    每处理若干帧，app.py 会调一次 trim()，只留最近 keep_lines 行，
    所以文件大小是恒定的（大约几十 KB）。调用点不在这里，
    这里只提供 trim()，谁调、多久调一次由 app.py 决定。
"""

import json
import os

from ..config import KEEP_LINES


class CoordStream:
    """往一个文件里追加坐标行。

    用法：
        cs = CoordStream('out/coords.jsonl')   # 传 None 就是关掉，什么都不写
        cs.write(targets)                      # 每帧调一次
        cs.write_line('[{"x":1,"y":2}]')       # 已经有字符串了就用这个
        cs.trim()                              # 定期调，防止文件无限长
        cs.close()                             # 结束时调

    参数：
        path        写到哪个文件。None 表示不写文件。
        keep_lines  trim() 之后最多留多少行
    """

    def __init__(self, path=None, keep_lines=KEEP_LINES):
        self.keep_lines = max(1, int(keep_lines))
        self._fh = None

        if path:
            # 目录可能还不存在（比如第一次跑 out\ 还没建），先建出来
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            # 'a' 追加：重启一次进程不会把上一段的记录冲掉
            self._fh = open(path, 'a', encoding='utf-8')

    @property
    def enabled(self):
        """是否真的在写文件。没传 path 时是 False。"""
        return self._fh is not None

    def write(self, targets):
        """写一帧。targets 是 get_target_centers() 那种列表，空列表也写。

        内部就是先拼成 JSON 字符串，再交给 write_line() 落盘。
        谁要是已经自己拼好了字符串（比如 app.py 要先把这串东西
        交给用户自己的程序），直接调 write_line()，别在这儿再拼一遍。
        """
        # separators 去掉空格，一行更短；ensure_ascii=False 保留中文
        self.write_line(json.dumps(targets, ensure_ascii=False,
                                   separators=(',', ':')))

    def write_line(self, line):
        """把【已经拼好的】一行 JSON 字符串追加到文件，空列表那行也照写。

        写完整行再 flush，保证下游读到的一定是完整的一行，
        不会读到半行 JSON。
        没开文件（path 传了 None）时什么都不做。
        """
        if not self._fh:
            return

        self._fh.write(line + '\n')
        self._fh.flush()

    def trim(self):
        """把文件裁到只留最近 keep_lines 行，返回丢掉了多少行。

        做法：全读进来 -> 关句柄 -> 整体重写 -> 重新按追加打开。
        没开文件时直接返回 0。
        """
        if not self._fh:
            return 0

        path = self._fh.name
        self._fh.flush()

        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        dropped = max(0, len(lines) - self.keep_lines)
        if dropped:
            self._fh.close()
            with open(path, 'w', encoding='utf-8') as f:
                f.writelines(lines[-self.keep_lines:])
            self._fh = open(path, 'a', encoding='utf-8')

        return dropped

    def close(self):
        """关掉文件。重复调用是安全的。"""
        if self._fh:
            self._fh.close()
            self._fh = None
