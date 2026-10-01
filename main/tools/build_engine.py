# -*- coding: utf-8 -*-
"""
ONNX  ->  TensorRT engine
============================================================
TensorRT 11 移除了 BuilderFlag.FP16 / INT8，而且
ILayer.set_output_type 和 ITensor.dtype 的 setter 也都没了
（ITensor.dtype 变成只读）。

所以精度只能由【网络自己的张量类型】决定：
    - fp32 engine：直接解析 best.onnx
    - fp16 engine：先 tools/to_fp16_onnx.py 转出 best_fp16.onnx，再解析它

解析时一律用 STRONGLY_TYPED 网络，TRT 才会老老实实按 ONNX 的 dtype 走。

用法:
    python build_engine.py --onnx weights/best.onnx      --out weights/best_fp32.engine
    python build_engine.py --onnx weights/best_fp16.onnx --out weights/best_fp16.engine

只做编译，不做推理；run_infer.py 才负责跑图。
"""
import argparse
import os
import sys
import time

import tensorrt as trt


def build(onnx_path, engine_path, workspace_gb=4.0, verbose=False):
    logger = trt.Logger(trt.Logger.INFO if verbose else trt.Logger.WARNING)
    builder = trt.Builder(logger)

    network = builder.create_network(
        1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
    parser = trt.OnnxParser(network, logger)

    print(f'[1/3] 解析 {os.path.basename(onnx_path)}')
    if not parser.parse_from_file(onnx_path):
        for i in range(parser.num_errors):
            print('  ', parser.get_error(i))
        sys.exit('ONNX 解析失败')

    inp = network.get_input(0)
    out = network.get_output(0)
    print(f'      in  {inp.name} {tuple(inp.shape)} {inp.dtype}')
    print(f'      out {out.name} {tuple(out.shape)} {out.dtype}')
    print(f'      层数 {network.num_layers}')

    cfg = builder.create_builder_config()
    cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE,
                              int(workspace_gb * (1 << 30)))

    print('[2/3] 编译中（RTX 3060 上大约 1~4 分钟）...')
    t0 = time.time()
    serialized = builder.build_serialized_network(network, cfg)
    dt = time.time() - t0
    if serialized is None:
        sys.exit('编译失败：build_serialized_network 返回 None')
    print(f'      完成，耗时 {dt:.1f}s')

    print(f'[3/3] 写盘 {engine_path}')
    with open(engine_path, 'wb') as f:
        f.write(serialized)
    size = os.path.getsize(engine_path) / 1e6
    print(f'      {size:.1f} MB')

    # 回读校验：能反序列化才说明 engine 可用
    with open(engine_path, 'rb') as f:
        runtime = trt.Runtime(logger)
        engine = runtime.deserialize_cuda_engine(f.read())
    if engine is None:
        sys.exit('反序列化失败：engine 文件不可用')
    print(f'      回读 OK，{engine.num_io_tensors} 个 IO 张量：')
    for i in range(engine.num_io_tensors):
        n = engine.get_tensor_name(i)
        print(f'        {n}  {engine.get_tensor_mode(n)}  '
              f'{tuple(engine.get_tensor_shape(n))}  {engine.get_tensor_dtype(n)}')
    return engine_path, dt, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--onnx', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--workspace', type=float, default=4.0)
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    build(a.onnx, a.out, a.workspace, a.verbose)


if __name__ == '__main__':
    main()
