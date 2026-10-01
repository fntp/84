# -*- coding: utf-8 -*-
"""项目入口。启动就一件事：抓屏幕 -> 检测 -> 输出坐标（开关打开时跟随鼠标）。

用法（在项目根目录下）：

    set PYTHONIOENCODING=utf-8
    set PYTHONUTF8=1
    C:\\Users\\fntp\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe start.py

起来之后鼠标右键按一下开始跟随，再按一下停手。不按就一直什么都不做 ——
不抓屏、不检测、不动鼠标。不想用这个开关，加 --always 就是一直跟随。

参数说明用 --help 看。
"""

import os
import sys

# 把项目根目录加到模块搜索路径最前面，这样 main 包才 import 得到。
# 不管从哪个目录启动，ROOT 都等于本文件所在目录。
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.app import main  # noqa: E402  （必须在 sys.path 设置之后导入）

if __name__ == '__main__':
    main()
