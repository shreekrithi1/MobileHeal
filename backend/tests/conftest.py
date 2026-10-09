"""Shared test setup.

Several suites copy the project into a temp workspace and exercise the crash demo bugs. Those bugs are
intentional and get *fixed* whenever someone runs the Crash demo (the fix is merged into the real project),
so every copied workspace is put back into the "bug present" state — tests never depend on demo history.
"""
import importlib
import re
import shutil
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
_copytree = shutil.copytree


def restore_demo_bugs(root: Path) -> None:
    from app import demo
    for rel, src in demo.ORIGINALS.items():
        p = root / rel
        if p.exists():
            p.write_text(src, encoding="utf-8")
        # the server under test imports these modules from the real project — load the bugged code into them too
        mod = "app." + rel[len("backend/app/"):-3].replace("/", ".")
        m = sys.modules.get(mod) or importlib.import_module(mod)
        exec(compile(src, m.__file__, "exec"), m.__dict__)   # project path → incidents map to the workspace copy
    for rel, bug, pat in ((demo.ANDROID_FILE, demo.ANDROID_BUG_LINE, r"^([ \t]*)val phone = .*phone_number.*$"),
                          (demo.IOS_FILE, demo.IOS_BUG_LINE, r"^([ \t]*)let phone = .*phone_number.*$")):
        p = root / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        if bug.strip() in src:
            continue
        new = re.sub(r"^.*MH-DEMO-BUG.*$", lambda m: bug, src, count=1, flags=re.M)
        if new == src:
            new = re.sub(pat, lambda m: m.group(1) + bug.strip(), src, count=1, flags=re.M)
        p.write_text(new, encoding="utf-8")
    for t in (root / "backend" / "tests").glob("test_inc_*.py"):   # regression tests for fixed demo bugs
        t.unlink()


@pytest.fixture(autouse=True)
def _demo_bugs_in_copies(monkeypatch):
    def copytree(src, dst, *a, **kw):
        out = _copytree(src, dst, *a, **kw)
        if Path(src).resolve() == PROJECT:
            restore_demo_bugs(Path(dst))
        return out
    monkeypatch.setattr(shutil, "copytree", copytree)
    yield
