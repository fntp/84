# -*- coding: utf-8 -*-
"""Detector 类：本项目最核心的一个类，负责"加载模型 + 跑一次推理"。

对外只有三个方法要用：
    det = Detector('weights/best_fp16.engine')   # 加载，约 1 秒
    boxes, scores, clss = det.infer(pil_image)   # 传 PIL 图片，返回框
    det.close()                                  # 用完释放显存

依赖：numpy、Pillow、tensorrt、cuda-python。不需要 torch / opencv，
所以这个脚本启动很快，也不用装一堆几百 MB 的东西。

坐标口径：
    返回的 boxes 是【原图像素坐标】xyxy，左上角原点，x 向右 y 向下。
    engine 内部是 640x640 画布坐标，infer() 里已经还原过了，
    调用方不用再换算。

显存占用：加载一个 engine 大约 230 MB，跑多久都不涨，
    因为输入/输出缓冲区是一次性分配、每帧复用的。
"""

import os

import numpy as np
import tensorrt as trt
from cuda.bindings import runtime as cudart

from .postprocess import postprocess, scale_boxes
from .preprocess import preprocess

# 网络输入边长，要跟导出 engine 时保持一致
INPUT_SIZE = 640


def check_cuda(err, what):
    """检查 CUDA 调用的返回码，非 0 就抛异常。

    cuda-python 不抛异常，只返回错误码，忘了检查就会静默出错，
    很难查。所以每次调用完都必须过一道这个函数。

    参数：
        err   cudart.xxx() 返回的第一个值
        what  出错时打印的名字，用来定位是哪一步挂的
    """
    if err != cudart.cudaError_t.cudaSuccess:
        raise RuntimeError(f'{what} 失败: {err}')
    return err


class Detector:
    """一个 TensorRT engine 的封装。一个实例对应一块显存。"""

    def __init__(self, engine_path, verbose=False):
        """加载 engine 文件并做好准备。

        参数：
            engine_path  .engine 文件路径
            verbose      True 时打印 TensorRT 的详细日志，排查问题用
        """
        # verbose=False 时只打印错误，避免刷屏
        level = trt.Logger.INFO if verbose else trt.Logger.ERROR
        logger = trt.Logger(level)

        with open(engine_path, 'rb') as f:
            runtime = trt.Runtime(logger)
            self.engine = runtime.deserialize_cuda_engine(f.read())
        if self.engine is None:
            raise RuntimeError(f'engine 反序列化失败，文件可能损坏: {engine_path}')

        self.context = self.engine.create_execution_context()

        # 遍历所有输入/输出张量，把名字、形状、类型记下来。
        # 写死名字不好，因为不同版本导出的名字可能不一样。
        self.in_name = self.out_name = None
        for i in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(i)
            if self.engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
                self.in_name = name
            else:
                self.out_name = name

        self.in_shape = tuple(self.engine.get_tensor_shape(self.in_name))
        self.out_shape = tuple(self.engine.get_tensor_shape(self.out_name))
        self.in_dtype = self.engine.get_tensor_dtype(self.in_name)
        self.out_dtype = self.engine.get_tensor_dtype(self.out_name)

        # 固定用 0 号显卡
        err, = cudart.cudaSetDevice(0)
        check_cuda(err, 'cudaSetDevice')

        err, self.stream = cudart.cudaStreamCreate()
        check_cuda(err, 'cudaStreamCreate')

        # 主机侧（内存）缓冲区：输入输出各一块，每帧复用
        self.h_in = np.zeros(self.in_shape, dtype=np.float32)
        self.h_out = np.zeros(self.out_shape, dtype=np.float32)

        # 设备侧（显存）缓冲区。注意 cudaMalloc 会返回 (错误码, 指针)，
        # 必须一次拿到指针再检查错误码 —— 分两次调用会多分配一块显存且永远释放不掉。
        err, self.d_in = cudart.cudaMalloc(self.h_in.nbytes)
        check_cuda(err, 'cudaMalloc(in)')
        err, self.d_out = cudart.cudaMalloc(self.h_out.nbytes)
        check_cuda(err, 'cudaMalloc(out)')

        # 告诉 TensorRT：这次执行的输入输出分别在哪块显存上
        self.context.set_tensor_address(self.in_name, int(self.d_in))
        self.context.set_tensor_address(self.out_name, int(self.d_out))

        # execute_async_v3 要的是整数句柄，不是 Stream 对象
        self.stream_h = int(self.stream)

    def raw_infer(self, blob):
        """只跑网络，不做前后处理。传入前处理好的 blob，返回原始输出。

        一般不用直接调它，调 infer() 就行。
        """
        if blob.shape != self.in_shape:
            raise ValueError(f'输入形状不对: 收到 {blob.shape}，期望 {self.in_shape}')

        # 主机内存 -> 显存
        self.h_in[...] = blob
        err, = cudart.cudaMemcpyAsync(
            self.d_in, self.h_in.ctypes.data, self.h_in.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, self.stream)
        check_cuda(err, '拷贝到显存')

        # 真正跑推理
        if not self.context.execute_async_v3(self.stream_h):
            raise RuntimeError('execute_async_v3 返回 False，推理没能启动')

        # 显存 -> 主机内存。参数顺序是 (目标, 来源, 字节数, 方向, stream)：
        # 这一趟的目标是内存里的 h_out，来源是显存里的 d_out，别写反 ——
        # 写反了会报 cudaErrorInvalidValue，或者更糟：静默拷错地方。
        err, = cudart.cudaMemcpyAsync(
            self.h_out.ctypes.data, self.d_out, self.h_out.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost, self.stream)
        check_cuda(err, '拷贝回内存')

        # 上面全是异步操作，这行才是真正等它算完
        err, = cudart.cudaStreamSynchronize(self.stream)
        check_cuda(err, 'cudaStreamSynchronize')

        return self.h_out

    def infer(self, img, conf_thres=0.25, iou_thres=0.45):
        """最常用的入口：一张 PIL 图片进去，检测框出来。

        参数：
            img        PIL.Image，任意尺寸
            conf_thres 置信度阈值
            iou_thres  NMS 重叠阈值

        返回：
            boxes   (N, 4) float32，原图【像素】坐标 xyxy
            scores  (N,)   float32，置信度，从高到低大致有序
            clss    (N,)   int64，类别下标（本模型只有 person，恒为 0）

        一张图都没检出来时返回三个空数组，不是 None，下游可以放心用 .shape[0]。
        """
        # 1. 前处理：原图 -> 640x640，同时记下缩放比例和补齐偏移
        blob, r, pad = preprocess(img, INPUT_SIZE)

        # 2. 跑网络。这里 .copy() 是必须的：
        #    h_out 是复用的缓冲区，下一帧就会把它覆盖掉
        raw = self.raw_infer(blob).copy()

        # 3. 后处理：筛分数 + NMS，得到画布坐标下的框
        boxes, scores, clss = postprocess(raw, conf_thres, iou_thres)

        # 4. 画布坐标 -> 原图坐标
        if boxes.shape[0]:
            boxes = scale_boxes(boxes, r, pad, img.size)

        return boxes, scores, clss

    def describe(self):
        """返回一行文字，说明这个 engine 的输入输出规格，打印用。"""
        return (f'in {self.in_shape} {self.in_dtype} '
                f'out {self.out_shape} {self.out_dtype}')

    def close(self):
        """释放显存。程序结束前一定要调，否则显存要等进程退出才还回去。"""
        for name, ptr in (('in', self.d_in), ('out', self.d_out)):
            try:
                cudart.cudaFree(ptr)
            except Exception as e:      # 已经在别处释放过就忽略
                print(f'释放显存 {name} 出错（忽略）: {e}')
        try:
            cudart.cudaStreamDestroy(self.stream)
        except Exception as e:
            print(f'销毁 stream 出错（忽略）: {e}')


def engine_size_mb(engine_path):
    """返回 engine 文件大小，单位 MB。打印用。"""
    return os.path.getsize(engine_path) / 1e6
