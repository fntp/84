# -*- coding: utf-8 -*-
"""量一个 TensorRT engine 的内存账：权重 / 激活 / IO 缓冲，分三段量。

本模块只负责"量"，不负责"打印"：probe_engine() 返回一个数字字典，
脚本想怎么显示都行。这样以后换输出格式不用动测量逻辑。

三段各自的含义：
    权重     反序列化 engine 时加载的 —— 这个文件的固有体积，改不了
    激活     建 execution context 时分配的 —— 跟网络结构和 batch 有关
    IO 缓冲  自己 cudaMalloc 出来的输入/输出 —— 输入 640x640，输出 25200x6

显存数字取自 cudaMemGetInfo，它给的是【整卡】剩余量；相邻两次相减，
得到的就是"这一步本进程多吃掉的显存"，不受桌面其它程序干扰。

为什么不用 main.component.detector.Detector？
    Detector 把"反序列化"和"建 context"包在一次构造里，量不出中间
    那一步的增量。这里要的是分步明细。

用法（一般由 main/tools/mem_report.py 调用）：

    from main.component.engine_probe import probe_engine
    m = probe_engine('weights/best_fp16.engine', blob, iters=50)
    print(m['load']['gpu'], m['ctx']['gpu'], m['run']['gpu'])
"""

import os
import time

from .meminfo import rss, rss_peak


def probe_engine(path, blob, iters=50):
    """把一个 engine 从文件到跑完推理的内存账量清楚。

    参数：
        path    engine 文件路径
        blob    已经前处理好的输入张量，形状 (1, 3, 640, 640)
        iters   跑多少次推理

    返回字典的键：
        name      engine 文件名（不带目录）
        file_mb   engine 文件大小（MB）
        load_ms   反序列化耗时（毫秒）
        load/ctx/run  三次测量点的明细，每个是
                      {'cpu': 这一刻的 CPU 占用, 'cpu_delta': 比上一次多多少,
                       'gpu': 这一步吃掉的显存}
                      run 里还有 'cpu_peak'（CPU 峰值）和
                      'in_bytes'/'out_bytes'（两端 IO 缓冲各多大）

    本函数自己分配、自己释放，不会把显存留给下一个 engine。
    """
    # 这几个库调用方那边早就 import 过了，这里再 import 是白拿，不会重复加载。
    import numpy as np
    import tensorrt as trt
    from cuda.bindings import runtime as cudart

    c0, f0 = rss(), cudart.cudaMemGetInfo()[1]

    t0 = time.perf_counter()
    with open(path, 'rb') as f:
        engine = trt.Runtime(trt.Logger(trt.Logger.ERROR)) \
                    .deserialize_cuda_engine(f.read())
    load_ms = (time.perf_counter() - t0) * 1000

    c1, f1 = rss(), cudart.cudaMemGetInfo()[1]

    ctx = engine.create_execution_context()
    c2, f2 = rss(), cudart.cudaMemGetInfo()[1]

    in_name, out_name = _io_names(engine, trt)
    h_in = np.zeros(tuple(engine.get_tensor_shape(in_name)), np.float32)
    h_out = np.zeros(tuple(engine.get_tensor_shape(out_name)), np.float32)

    # cudaMalloc 返回 (错误码, 指针)，一次调用就把指针拿到手。
    # 千万别为了"单独检查错误码"再调一次 —— 那会真的分配第二块显存，
    # 第一块指针直接丢掉，永远回收不了。
    d_in = cudart.cudaMalloc(h_in.nbytes)[1]
    d_out = cudart.cudaMalloc(h_out.nbytes)[1]
    stream = cudart.cudaStreamCreate()[1]
    ctx.set_tensor_address(in_name, int(d_in))
    ctx.set_tensor_address(out_name, int(d_out))

    for _ in range(iters):
        _run_once(cudart, ctx, stream, h_in, h_out, d_in, d_out, blob)

    f3 = cudart.cudaMemGetInfo()[1]
    c3, pk3 = rss(), rss_peak()

    # 量完就还回去，不然跑下一个 engine 时这个还占着显存
    cudart.cudaFree(d_in)
    cudart.cudaFree(d_out)
    cudart.cudaStreamDestroy(stream)
    del ctx, engine
    time.sleep(0.4)

    return {
        'name': os.path.basename(path),
        'file_mb': os.path.getsize(path) / 1e6,
        'load_ms': load_ms,
        'load': {'cpu': c1, 'cpu_delta': c1 - c0, 'gpu': f0 - f1},
        'ctx': {'cpu': c2, 'cpu_delta': c2 - c1, 'gpu': f1 - f2},
        'run': {'cpu': c3, 'cpu_delta': c3 - c2, 'cpu_peak': pk3,
                'gpu': f2 - f3,
                'in_bytes': h_in.nbytes, 'out_bytes': h_out.nbytes},
    }


def _io_names(engine, trt):
    """从 engine 里找出输入张量和输出张量的名字。"""
    in_name = out_name = None
    for i in range(engine.num_io_tensors):
        name = engine.get_tensor_name(i)
        if engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
            in_name = name
        else:
            out_name = name
    return in_name, out_name


def _run_once(cudart, ctx, stream, h_in, h_out, d_in, d_out, blob):
    """跑一次完整的 host -> device -> 推理 -> device -> host。"""
    h_in[...] = blob
    cudart.cudaMemcpyAsync(d_in, h_in.ctypes.data, h_in.nbytes,
                           cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, stream)
    ctx.execute_async_v3(int(stream))
    cudart.cudaMemcpyAsync(h_out.ctypes.data, d_out, h_out.nbytes,
                           cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost, stream)
    cudart.cudaStreamSynchronize(stream)
