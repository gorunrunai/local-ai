"""code_exec: run Python in a macOS sandbox (no network, writes only to its working dir)."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import signal
import sys
import time
from pathlib import Path

from inference.config import ROOT
from orchestrator.tools.base import Tool, ToolContext, ToolResult

MAX_OUTPUT_CHARS = 12_000
MAX_FILE_BYTES = 50 * 1024 * 1024
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}

PROFILE = """(version 1)
(allow default)
(deny network*)
(allow network* (local unix))
(deny file-write*)
(allow file-write* (subpath "{workdir}") (literal "/dev/null") (subpath "/dev/fd") (literal "/dev/tty"))
(deny file-read* (subpath "/Users") (subpath "/Volumes"))
(allow file-read-metadata)
(allow file-read* (subpath "{workdir}") (subpath "{venv}") (subpath "{base_prefix}"))
"""

# Runs inside the sandbox before the user's code. Limits are set as hard limits, which an
# unprivileged process can lower but never raise again. (Setting them from the parent via
# preexec_fn is unsafe here: forking a process that has MLX/torch threads and then running
# Python code in the child can deadlock, and asyncio waits for that exec on the event loop.)
RUNNER = """import resource, runpy, sys
resource.setrlimit(resource.RLIMIT_CPU, ({cpu}, {cpu}))
resource.setrlimit(resource.RLIMIT_FSIZE, ({fsize}, {fsize}))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
import matplotlib
matplotlib.use("Agg")
sys.argv = ["snippet.py"]
runpy.run_path(".snippet.py", run_name="__main__")
"""


def sandbox_profile(workdir: Path) -> str:
    return PROFILE.format(workdir=str(workdir.resolve()), venv=str(Path(sys.prefix).resolve()),
                          base_prefix=str(Path(sys.base_prefix).resolve()))


async def run_sandboxed(code: str, workdir: Path, timeout_s: int = 60) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / ".mpl").mkdir(exist_ok=True)
    prof = workdir / ".sandbox.sb"
    prof.write_text(sandbox_profile(workdir))
    (workdir / ".snippet.py").write_text(code)
    runner = workdir / ".runner.py"
    runner.write_text(RUNNER.format(cpu=timeout_s + 5, fsize=MAX_FILE_BYTES))
    before = {p: p.stat().st_mtime for p in workdir.iterdir() if p.is_file()}
    env = {"PATH": "/usr/bin:/bin", "HOME": str(workdir), "TMPDIR": str(workdir),
           "MPLCONFIGDIR": str(workdir / ".mpl"), "PYTHONDONTWRITEBYTECODE": "1", "LANG": "en_US.UTF-8"}
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/sandbox-exec", "-f", str(prof), sys.executable, "-I", str(runner),
        cwd=str(workdir), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL, start_new_session=True)
    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = await proc.communicate()
    produced = [p for p in workdir.iterdir() if p.is_file() and not p.name.startswith(".")
                and (p not in before or p.stat().st_mtime > before[p])]
    return {"stdout": out.decode(errors="replace"), "stderr": err.decode(errors="replace"),
            "exit_code": proc.returncode, "timed_out": timed_out, "files": produced,
            "elapsed_s": round(time.perf_counter() - t0, 2)}


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n // 2] + f"\n… [{len(s) - n} chars omitted] …\n" + s[-n // 2:]


class CodeExec(Tool):
    name = "code_exec"
    description = (
        "Run Python 3.12 code in a sandbox and return stdout/stderr. No network access. numpy, pandas "
        "and matplotlib are available. Each call starts a fresh interpreter, but files written to the "
        "current directory persist for this conversation. Save charts with plt.savefig('name.png'); "
        "saved files are shown to the user. Print results you need to see.")
    parameters = {"type": "object", "properties": {
        "code": {"type": "string", "description": "Python source to execute"},
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 300, "default": 60}},
        "required": ["code"]}

    def __init__(self, default_timeout: int = 60):
        self.default_timeout = default_timeout

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        workdir = Path(ctx.workdir_root) / ctx.conversation_id
        res = await run_sandboxed(args["code"], workdir, int(args.get("timeout_s") or self.default_timeout))
        files, images = [], []
        for p in res["files"][:20]:
            if ctx.incognito:
                att = {"id": None, "filename": p.name, "size": p.stat().st_size, "path": str(p)}
            else:
                dest, sha, size = ctx.files.save_bytes(p.read_bytes(), p.name)
                att = await ctx.store.add_attachment(
                    sha256=sha, filename=p.name, mime=mimetypes.guess_type(p.name)[0], size=size,
                    path=str(dest), kind="image" if p.suffix.lower() in IMAGE_EXT else "document",
                    source="tool", conversation_id=ctx.conversation_id)
            files.append({"id": att["id"], "filename": p.name, "size": att["size"]})
            if p.suffix.lower() in IMAGE_EXT - {".svg"}:
                images.append(att["path"])
        parts = []
        if res["timed_out"]:
            parts.append(f"Timed out after {args.get('timeout_s') or self.default_timeout}s.")
        parts.append(f"exit code: {res['exit_code']}")
        if res["stdout"]:
            parts.append("stdout:\n" + _clip(res["stdout"], MAX_OUTPUT_CHARS))
        if res["stderr"]:
            parts.append("stderr:\n" + _clip(res["stderr"], MAX_OUTPUT_CHARS // 2))
        if files:
            parts.append("files written: " + ", ".join(f["filename"] for f in files))
        ok = res["exit_code"] == 0 and not res["timed_out"]
        return ToolResult("\n".join(parts), ok=ok, images=images, files=files,
                          data={"exit_code": res["exit_code"], "stdout": _clip(res["stdout"], 4000),
                                "stderr": _clip(res["stderr"], 2000), "files": files,
                                "elapsed_s": res["elapsed_s"], "timed_out": res["timed_out"]})


def default_workdir_root() -> Path:
    return ROOT / "data" / "sandbox"
