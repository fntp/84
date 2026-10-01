# -*- coding: utf-8 -*-
"""main 包 · 项目主要包。

子包：
    component   组件包：可复用的功能模块（前处理 / 推理 / 坐标换算 / 输出）
    tools       工具包：一个个能单独运行的命令行脚本

同级公共文件：
    config.py     全项目的路径和默认值
    args.py       命令行参数定义
    runmode.py    前台 / 后台怎么跑
    app.py        启动函数：抓屏 -> 检测 -> 输出坐标
    selfcheck.py  start.py --check：把跟随那条链一环一环验一遍
"""
