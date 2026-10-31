"""The work station's CAD builder (workstation/cad_build.py): what code it refuses to run."""

import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[2] / "workstation" / "cad_build.py"
_spec = importlib.util.spec_from_file_location("cad_build", _path)
cad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cad)

GOOD = "from build123d import *\nimport math\nresult = Box(10, 10, math.pi)\n"


def test_ordinary_design_code_passes_the_check():
    cad.check(GOOD)
    cad.check("import numpy as np\nfrom build123d import Box, Cylinder\nresult = Box(1, 1, 1)")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("import os\nresult = 1", "line 1: `os` can't be imported"),
        ("from subprocess import run", "`subprocess` can't be imported"),
        ("x = 1\nopen('/etc/passwd')", "line 2: `open` isn't available"),
        ("eval('1')", "`eval` isn't available"),
        ("x = ().__class__.__bases__", "`.__bases__` isn't available"),
        ("getattr(1, 'real')", "`getattr` isn't available"),
        ("from . import x", "relative imports"),
        ("def broken(:\n  pass", "line 1: syntax error"),
    ],
)
def test_dangerous_or_broken_code_is_refused_before_it_runs(source, message):
    with pytest.raises(cad.Rejected, match=message.replace("`", ".")):
        cad.check(source)


def test_the_guarded_import_only_lets_the_allowed_modules_through():
    assert cad._guarded_import("math") is not None
    with pytest.raises(ImportError):
        cad._guarded_import("socket")
    with pytest.raises(ImportError):
        cad._guarded_import("math", level=1)
