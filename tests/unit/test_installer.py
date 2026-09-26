"""install.sh chooses what each Mac can run (--dry-run --machine simulates the Mac)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# A maintainer's local tool, kept out of the repository: its tests run only where it exists.
needs_install_local = pytest.mark.skipif(not (ROOT / "install-local.sh").exists(), reason="install-local.sh is local only")


def dry_run(*args: str) -> tuple[int, str]:
    env = {**os.environ, "NO_COLOR": "1", "GORUNRUN_HOME": "/nonexistent/gorunrun"}
    r = subprocess.run(["bash", str(ROOT / "install.sh"), "--dry-run", "--yes", *args],
                       capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL, check=False)
    return r.returncode, r.stdout + r.stderr


def card(out: str, label: str) -> str:
    line = next(ln for ln in out.splitlines() if ln.strip().startswith(f"│ {label} "))
    return line.split(label, 1)[1].strip(" │")


@pytest.mark.parametrize("machine,model,video,budget,tested", [
    ("M1Max-32GB", "Gemma 4 (12B)", "not included", 22, False),
    ("M3Max-36GB", "Gemma 4 (12B)", "not included", 25, False),
    ("M4Max-48GB", "Gemma 4 (12B)", "not included", 33, False),   # LTX is opt-in (untested) here
    ("M5Max-64GB", "Qwen 3.5 (35B)", "LTX-2.3", 44, True),
    ("M3Max-96GB", "Qwen 3.5 (35B)", "LTX-2.3", 67, False),
    ("M4Max-128GB", "Qwen 3.5 (35B)", "LTX-2.3", 89, False),
])
def test_defaults_per_mac(machine, model, video, budget, tested):
    code, out = dry_run(f"--machine={machine}")
    assert code == 0, out
    assert card(out, "AI model").startswith(model)
    assert card(out, "Video").startswith(video)
    assert f"memory_budget_gb: {budget}" in out
    assert ("(tested)" in card(out, "Mac")) is tested
    # Gemma-only Macs keep Gemma loaded (it's the main model there)
    assert ("ttl_s: 0" in out) is model.startswith("Gemma")


@pytest.mark.parametrize("machine,reason", [
    ("Intel-16GB", "Apple Silicon Mac (M1 or newer) is required"),
    ("M2-24GB", "At least 32 GB of memory is required"),
])
def test_unsupported_macs_stop(machine, reason):
    code, out = dry_run(f"--machine={machine}")
    assert code == 1 and reason in out


def test_old_macos_stops():
    code, out = dry_run("--machine=M1Max-64GB", "--macos=14.6")
    assert code == 1 and "macOS 15 or newer is required" in out


def test_asking_for_more_than_the_mac_can_run_is_refused():
    code, out = dry_run("--machine=M4Max-48GB", "--model=qwen", "--video=both")
    assert code == 0
    assert "Qwen 3.5 needs 64 GB of memory and this Mac has 48 GB" in out
    assert "Wan 2.2 needs 64 GB of memory and this Mac has 48 GB" in out
    assert card(out, "AI model").startswith("Gemma") and card(out, "Video").startswith("LTX-2.3")

    _, out = dry_run("--machine=M1Max-32GB", "--video=ltx")
    assert "Video creation needs at least 48 GB of memory" in out and card(out, "Video") == "not included"


def test_options_a_mac_can_run_are_honoured():
    _, out = dry_run("--machine=M4Max-48GB", "--video=ltx")
    assert card(out, "Video").startswith("LTX-2.3")                       # opt-in on 48 GB
    _, out = dry_run("--machine=M5Max-64GB", "--model=gemma", "--video=both")
    assert card(out, "AI model").startswith("Gemma") and "LTX-2.3 and Wan 2.2" in card(out, "Video")
    _, out = dry_run("--machine=M5Max-64GB", "--without-video")
    assert card(out, "Video") == "not included"


def test_machine_only_with_dry_run():
    r = subprocess.run(["bash", str(ROOT / "install.sh"), "--machine=M1Max-32GB"], capture_output=True,
                       text=True, timeout=30, stdin=subprocess.DEVNULL, check=False)
    assert r.returncode == 2 and "only work with --dry-run" in r.stdout + r.stderr


@needs_install_local
def test_install_local_dry_run_needs_no_github(tmp_path):
    env = {**os.environ, "NO_COLOR": "1", "GORUNRUN_HOME": "/nonexistent/gorunrun",
           "GORUNRUN_LOCAL_SOURCE": str(tmp_path / "src")}
    r = subprocess.run(["bash", str(ROOT / "install-local.sh"), "--dry-run", "--yes", "--machine=M5Max-64GB"],
                       capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL, check=False)
    assert r.returncode == 0 and "Dry run: nothing was changed." in r.stdout
    assert not (tmp_path / "src").exists()                  # a dry run doesn't even make the snapshot


@needs_install_local
def test_install_local_snapshots_this_folder_and_stacks_updates(tmp_path):
    """install-local.sh copies committed and uncommitted files (not ignored ones) into a local repo
    that install.sh clones, and each run is a new commit on top, so updates fast-forward."""
    src = tmp_path / "checkout"
    src.mkdir()
    git = lambda *a, cwd=src: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
    git("init", "-q", "-b", "main")
    (src / ".gitignore").write_text("data/\n")
    (src / "committed.txt").write_text("v1")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    (src / "uncommitted.txt").write_text("new")
    (src / "data").mkdir()
    (src / "data" / "chats.db").write_text("private")
    (src / "install-local.sh").write_text((ROOT / "install-local.sh").read_text())
    # Stand-in installer: clone (or fast-forward) from GORUNRUN_REPO like get_program does.
    (src / "install.sh").write_text(
        'set -e; H="$GORUNRUN_HOME"; if [ -d "$H/.git" ]; then git -C "$H" remote set-url origin "$GORUNRUN_REPO"; '
        'git -C "$H" fetch -q origin main; git -C "$H" merge -q --ff-only origin/main; '
        'else git clone -q --branch main "$GORUNRUN_REPO" "$H"; fi\n')
    snap, home = tmp_path / "snap", tmp_path / "home"
    env = {**os.environ, "GORUNRUN_LOCAL_SOURCE": str(snap), "GORUNRUN_HOME": str(home)}
    run = lambda: subprocess.run(["bash", str(src / "install-local.sh")], env=env, capture_output=True,
                                 text=True, timeout=60, check=False)
    r = run()
    assert r.returncode == 0, r.stderr
    assert (home / "uncommitted.txt").read_text() == "new" and (home / "committed.txt").exists()
    assert not (home / "data").exists()                              # ignored files never get copied
    (src / "committed.txt").write_text("v2")                        # an edit, then run again: an update
    (src / "uncommitted.txt").unlink()
    assert run().returncode == 0
    assert (home / "committed.txt").read_text() == "v2" and not (home / "uncommitted.txt").exists()
    assert git("rev-list", "--count", "main", cwd=snap).strip() == "2"

    # Reported: a dry-run uninstall deleted the snapshot, then the next update failed with
    # "refusing to merge unrelated histories". A dry run must keep it...
    (src / "install.sh").write_text((src / "install.sh").read_text().replace(
        "set -e;", 'set -e; case " $* " in *" --dry-run "*) exit 0 ;; esac;'))
    subprocess.run(["bash", str(src / "install-local.sh"), "--uninstall", "--dry-run"], env=env,
                   capture_output=True, text=True, timeout=60, check=True)
    assert (snap / ".git").exists()
    # ...and if the snapshot is lost anyway, the next run continues the install's history.
    subprocess.run(["rm", "-rf", str(snap)], check=True)
    (src / "committed.txt").write_text("v3")
    r = run()
    assert r.returncode == 0, r.stderr
    assert (home / "committed.txt").read_text() == "v3"
