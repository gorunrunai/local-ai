"""Memory accounting on macOS.

RSS undercounts Metal allocations, so per-process usage is read from the kernel's
`phys_footprint` (what Activity Monitor calls "Memory"), via libproc.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
from dataclasses import dataclass

import psutil

_RUSAGE_INFO_V2 = 2
_libproc = None


def _lib():
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    return _libproc


def phys_footprint(pid: int) -> int:
    """Physical footprint of a process in bytes (0 if unavailable)."""
    buf = ctypes.create_string_buffer(512)
    if _lib().proc_pid_rusage(pid, _RUSAGE_INFO_V2, buf) != 0:
        return 0
    # rusage_info_v2: 16-byte uuid, then uint64 fields; phys_footprint is the 8th.
    fields = (ctypes.c_uint64 * 20).from_buffer_copy(buf.raw[16:16 + 160])
    return int(fields[7])


def tree_footprint(pid: int) -> int:
    """Footprint of a process plus all its descendants."""
    try:
        proc = psutil.Process(pid)
        pids = [pid] + [c.pid for c in proc.children(recursive=True)]
    except psutil.Error:
        return 0
    return sum(phys_footprint(p) for p in pids)


@dataclass
class SystemMemory:
    total_gb: float
    available_gb: float
    used_gb: float
    gpu_wired_limit_mb: int | None  # None/0 means macOS default (~75% of RAM)


def system_memory() -> SystemMemory:
    vm = psutil.virtual_memory()
    limit = None
    try:
        out = subprocess.run(["sysctl", "-n", "iogpu.wired_limit_mb"], capture_output=True,
                             text=True, timeout=2, check=False).stdout.strip()
        limit = int(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    gb = 1024 ** 3
    return SystemMemory(total_gb=round(vm.total / gb, 1), available_gb=round(vm.available / gb, 1),
                        used_gb=round((vm.total - vm.available) / gb, 1), gpu_wired_limit_mb=limit)
