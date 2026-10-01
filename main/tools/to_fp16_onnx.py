# -*- coding: utf-8 -*-
"""
best.onnx (fp32)  ->  best_fp16.onnx
============================================================
TensorRT 11 移除了 BuilderFlag.FP16，精度只能由网络的张量类型决定。
所以走「先把 ONNX 转 fp16，再用 strongly-typed 网络解析」这条路。

keep_io_types=True：输入输出保持 fp32，中间层 fp16。
这样喂图和接后处理都不用改，精度也能对照。

用法:
    python to_fp16_onnx.py --src weights/best.onnx --dst weights/best_fp16.onnx
"""
import argparse
import os
import sys

import onnx
from onnxconverter_common import float16


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--dst', required=True)
    a = ap.parse_args()

    print(f'[1/3] 读 {a.src}')
    model = onnx.load(a.src)
    print(f'      opset {[o.version for o in model.opset_import]}')

    print('[2/3] 转 fp16（IO 保持 fp32）')
    m16 = float16.convert_float_to_float16(
        model, keep_io_types=True, disable_shape_infer=False)
    onnx.checker.check_model(m16)
    print('      checker 通过')

    print(f'[3/3] 写 {a.dst}')
    onnx.save(m16, a.dst)
    print(f'      {os.path.getsize(a.dst) / 1e6:.1f} MB '
          f'(fp32 原文件 {os.path.getsize(a.src) / 1e6:.1f} MB)')

    dts = {t.data_type for t in m16.graph.initializer}
    print('      initializer dtype 种类:',
          {onnx.TensorProto.DataType.Name(d) for d in dts})
    for io in list(m16.graph.input) + list(m16.graph.output):
        print(f'      {io.name}: '
              f'{onnx.TensorProto.DataType.Name(io.type.tensor_type.elem_type)}')


if __name__ == '__main__':
    main()
