# -*- coding: utf-8 -*-
"""项目入口。启动就一件事：抓屏幕 -> 检测 -> 输出坐标（按住右键时跟随鼠标）。

用法（在项目根目录下）：

    set PYTHONIOENCODING=utf-8
    set PYTHONUTF8=1
    C:\\Users\\fntp\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe start.py

起来之后按住鼠标右键（= 游戏里开镜）就跟随，一松开立刻停手。
没按的时候什么都不做 —— 不抓屏、不检测、不动鼠标。
不想用这个开关，加 --always 就是一直跟随。

跟随没反应 / 看不出来有没有生效，按这个顺序查：

    1. 先自检，确认环境这一层没问题（会真的推一下鼠标、真的等你按右键）：
         python start.py --check
    2. 再让每一帧说清楚它判成了什么（没检出人 / 分数没过门槛 / 已经在死区里）：
         python start.py --follow-trace
       加了它就会自动留在前台 —— 默认是后台跑，屏幕上本来什么都看不到。
    3. 想跳过右键开关直接一直跟随（用来分清"开关没打开"还是"跟随本身不工作"）：
         python start.py --always --follow-trace

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
