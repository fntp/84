# -*- coding: utf-8 -*-
"""量内存用的小工具：本进程占了多少内存、显卡用了多少显存。

纯粹是"看数字"用的，不参与推理，删掉也不影响主流程。

三个数据来源：
    rss()            本进程物理内存 —— Windows API，最准
    rss_peak()       本进程内存峰值 —— 同上，看"最多扛过多少"
    gpu_free_bytes() 显存剩余 —— CUDA 运行时，最快
    nvsmi_used()     整卡显存占用 —— 调 nvidia-smi，慢但是全卡视角
"""

import ctypes
import subprocess


# ----------------------------------------------------------------------
# 本进程内存
# ----------------------------------------------------------------------

class ProcessMemoryCounters(ctypes.Structure):
    """Windows GetProcessMemoryInfo 要填的结构体，字段顺序不能改。"""

    _fields_ = [
        ('cb', ctypes.c_ulong),
        ('PageFaultCount', ctypes.c_ulong),
        ('PeakWorkingSetSize', ctypes.c_size_t),
        ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t),
        ('PeakPagefileUsage', ctypes.c_size_t),
    ]


def _counters():
    """读一次进程内存计数器。读失败返回 None。"""
    pmc = ProcessMemoryCounters()
    pmc.cb = ctypes.sizeof(pmc)

    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.windll.kernel32.GetCurrentProcess(),
        ctypes.byref(pmc),
        pmc.cb,
    )
    return pmc if ok else None


def rss():
    """本进程当前占用的物理内存，单位字节。取不到时返回 0。

    比 psutil 轻，不用装第三方包。对应任务管理器「内存」那一列。
    """
    pmc = _counters()
    return pmc.WorkingSetSize if pmc else 0


def rss_peak():
    """本进程启动到现在出现过的内存峰值，单位字节。取不到时返回 0。

    峰值比当前值更能说明问题：跑完推理后内存会掉一些，
    但峰值才是这台机器真正扛过的量。
    """
    pmc = _counters()
    return pmc.PeakWorkingSetSize if pmc else 0


# ----------------------------------------------------------------------
# 显存
# ----------------------------------------------------------------------

def gpu_free_bytes():
    """当前显卡还剩多少可用显存，单位字节。失败返回 None。

    注意：这是【整卡】剩余量，不只是本进程的。别的程序也在用这块卡。
    """
    try:
        from cuda.bindings import runtime as cudart
        err, free, _total = cudart.cudaMemGetInfo()
        if err != cudart.cudaError_t.cudaSuccess:
            return None
        return int(free)
    except Exception:
        return None


def nvsmi_used():
    """整卡已用显存，单位字节。失败返回 None。

    走 nvidia-smi 命令行，启动一次要几百毫秒，别放在循环里反复调。
    """
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=memory.used',
             '--format=csv,noheader,nounits'],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        # 输出形如 "1234"，单位是 MiB
        return int(out.strip().splitlines()[0]) * 1024 * 1024
    except Exception:
        return None


# ----------------------------------------------------------------------
# 换算
# ----------------------------------------------------------------------

def mb(nbytes):
    """字节 -> MB（1 MB = 1024x1024 字节）。None 原样返回。"""
    if nbytes is None:
        return None
    return nbytes / (1024 * 1024)


def fmt_mb(nbytes):
    """格式化成带单位的字符串，None 显示成问号。"""
    v = mb(nbytes)
    return '未知' if v is None else f'{v:.1f} MB'
