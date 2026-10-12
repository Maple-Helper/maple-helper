"""The nightly KB refresh (kb-update.yml) installs only Pillow, not the app's UI libraries: its tools must run
without PySide6. translate_kb crashed every night from 0.17.0 on (the Cash Shop texts imported ui/shoppage.py),
and the step's "|| echo ::warning::" let it pass unseen."""
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_nightly_translation_runs_without_pyside6(tmp_path):
    code = textwrap.dedent(f"""
        import sys
        class NoQt:
            def find_spec(self, name, path=None, target=None):
                if name == "PySide6" or name.startswith("PySide6."):
                    raise ImportError("PySide6 is not installed on the nightly runner")
        sys.meta_path.insert(0, NoQt())
        sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / "tools")!r}]
        import translate_kb
        from maplehelper import osms
        osms.items = lambda *a, **k: [osms.Item(**{{f: None for f in osms.Item.__dataclass_fields__}})]
        class KB:
            entities = {{}}
            def get(self, key):
                return None
        translate_kb.cash_jobs(KB(), __import__("pathlib").Path({str(tmp_path)!r}))
        assert "PySide6" not in sys.modules
    """)
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0, run.stderr[-2000:]
