# -*- coding: utf-8 -*-
"""component 组件包 · 可复用的功能模块。

这里的模块都是"零件"，不直接运行，只被 main/app.py 和 main/tools/*.py 调用。

    preprocess.py        前处理：原图 -> 640x640 网络输入
    postprocess.py       后处理：网络原始输出 -> 原图上的框
    detector.py          Detector 类：加载 engine + 跑推理（核心）
    target_center.py     框 -> 中心点坐标（全项目唯一口径）
    detection_output.py  检测结果 -> JSONL / 控制台
    coord_stream.py      坐标 -> 文件（后台无窗口时的出口）
    screen.py            屏幕相关：DPI 声明 / 抓图 / region 解析 / 屏幕尺寸
    trigger.py           右键开关：按着（开镜）就跟随，一松开就停手
    aim.py               瞄准算法：挑目标、算这一步推多少（纯计算，不碰鼠标）
    follow.py            把 aim 算出来的位移推给鼠标，同一句报错只说一次
    report.py            把一帧结果排版成给人看的文字
    visualize.py         读图 / 画框存盘（离线看结果用）
    meminfo.py           量内存占用的小工具
    engine_probe.py      分三段量一个 engine 吃多少内存和显存
"""
