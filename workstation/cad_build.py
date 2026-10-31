#!/usr/bin/env python3
"""Builds a part from the model's build123d code, for the app's design tools.

  cad_build.py build  <job_dir>            runs <job_dir>/design.py, writes model.stl and design.json
  cad_build.py export <job_dir> <format>   runs it again and writes model.<ext> (step, stl, 3mf, brep, obj, glb)

design.py must leave the finished solid in a variable called `result` (a Part, or the builder that
made it). Everything is millimetres. The report goes to stdout as JSON and to design.json; a
failure has "ok": false and an "error" that names the line of design.py that went wrong.

The code is the model's, so it is checked and fenced in before it runs. The fence is hygiene
against mistakes, not a security boundary: the real limits are that the work station is the user's
own machine, the run is capped on time (by the caller), CPU, memory and file size, and the network
is cut off when the kernel allows it (`isolated` in the report says whether it did).
"""

import ast
import json
import resource
import sys
import traceback
from pathlib import Path

CODE_FILE = "design.py"
REPORT_FILE = "design.json"
EXTENSIONS = {"step": "step", "stl": "stl", "3mf": "3mf", "brep": "brep", "obj": "obj", "glb": "glb"}
CPU_SECONDS = 120
MEMORY_BYTES = 5 * 1024**3
FILE_BYTES = 256 * 1024**2
# Finer than this and a small curved part becomes millions of triangles.
STL_TOLERANCE_MM = 0.05
STL_ANGULAR_TOLERANCE = 0.2

ALLOWED_MODULES = {
    "build123d",
    "math",
    "numpy",
    "typing",
    "itertools",
    "functools",
    "dataclasses",
    "collections",
    "enum",
    "copy",
}
# Names that reach the file system, the interpreter or the import machinery.
FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "memoryview", "exit", "quit", "help",
}  # fmt: skip
SAFE_BUILTINS = (
    "abs all any bool dict divmod enumerate filter float frozenset int isinstance issubclass iter "
    "len list map max min next pow print range reversed round set slice sorted str sum tuple zip "
    "True False None Exception ValueError TypeError ArithmeticError ZeroDivisionError IndexError "
    "KeyError StopIteration NotImplementedError RuntimeError AssertionError property "
    "staticmethod classmethod super object type repr hash chr ord callable bytes bytearray "
    "__build_class__ __name__"
).split()


class Rejected(Exception):
    """The code was refused before it ran."""


def check(source: str) -> ast.Module:
    """Parses the code and refuses what it must not do."""
    try:
        tree = ast.parse(source, CODE_FILE)
    except SyntaxError as problem:
        raise Rejected(f"line {problem.lineno}: syntax error: {problem.msg}") from None
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                _allow_module(alias.name, line)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise Rejected(f"line {line}: relative imports aren't available")
            _allow_module(node.module or "", line)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise Rejected(f"line {line}: `{node.id}` isn't available in design code")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise Rejected(f"line {line}: `.{node.attr}` isn't available in design code")
    return tree


def _allow_module(name: str, line: int) -> None:
    if name.split(".")[0] not in ALLOWED_MODULES:
        raise Rejected(
            f"line {line}: `{name}` can't be imported; use build123d (and math or numpy)"
        )


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    if level or name.split(".")[0] not in ALLOWED_MODULES:
        raise ImportError(f"`{name}` can't be imported in design code")
    return __import__(name, globals, locals, fromlist, level)


def limit_resources() -> bool:
    """Caps CPU, memory and file size, and cuts the network if the kernel lets us. Returns whether
    the network is cut."""
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
    try:
        import os

        os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)  # type: ignore[attr-defined]
        return True
    except (AttributeError, OSError):
        return False


def run_design(job_dir: Path):
    """Executes the code and returns the finished shape."""
    import builtins

    source = (job_dir / CODE_FILE).read_text()
    tree = check(source)
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS if hasattr(builtins, name)}
    safe["__import__"] = _guarded_import
    namespace: dict = {"__builtins__": safe, "__name__": "design"}
    exec(compile(tree, CODE_FILE, "exec"), namespace)  # noqa: S102
    if "result" not in namespace:
        raise Rejected("design.py must leave the finished part in a variable called `result`")
    return as_shape(namespace["result"])


def as_shape(value):
    """The solid from `result`, which may be a Part or the builder that made it."""
    from build123d import Shape

    shape = getattr(value, "part", value)
    if not isinstance(shape, Shape):
        raise Rejected(
            f"`result` is a {type(value).__name__}, not a part; assign the finished solid, "
            "e.g. `result = part.part` after a `with BuildPart() as part:` block"
        )
    if not shape.solids():
        raise Rejected("`result` has no solid in it: a sketch or curve can't be analysed")
    return shape


def describe(shape, notes: list[str]) -> dict:
    box = shape.bounding_box()
    low = [box.min.X, box.min.Y, box.min.Z]
    high = [box.max.X, box.max.Y, box.max.Z]
    solids = len(shape.solids())
    valid = bool(shape.is_valid)
    if solids > 1:
        notes.append(
            f"the part is {solids} separate solids; fuse them (`a + b`) before analysing it"
        )
    if not valid:
        notes.append("the geometry is not valid (self-intersections or open shells)")
    center = shape.center()
    return {
        "units": "mm",
        "bounding_box_mm": [[round(v, 4) for v in low], [round(v, 4) for v in high]],
        "size_mm": [round(h - lo, 4) for lo, h in zip(low, high, strict=True)],
        "volume_mm3": round(shape.volume, 3),
        "surface_area_mm2": round(shape.area, 3),
        "center_of_mass_mm": [round(center.X, 4), round(center.Y, 4), round(center.Z, 4)],
        "solids": solids,
        "faces": len(shape.faces()),
        "valid": valid,
    }


def write_model(shape, path: Path, fmt: str) -> None:
    from build123d import Mesher, export_brep, export_gltf, export_obj, export_step, export_stl

    if fmt == "step":
        if not export_step(shape, str(path)):
            raise RuntimeError("the STEP export failed")
    elif fmt == "stl":
        if not export_stl(
            shape,
            str(path),
            tolerance=STL_TOLERANCE_MM,
            angular_tolerance=STL_ANGULAR_TOLERANCE,
        ):
            raise RuntimeError("the STL export failed")
    elif fmt == "3mf":
        mesher = Mesher()
        mesher.add_shape(shape, linear_deflection=STL_TOLERANCE_MM, angular_deflection=0.2)
        mesher.write(str(path))
    elif fmt == "brep":
        if not export_brep(shape, str(path)):
            raise RuntimeError("the BREP export failed")
    elif fmt == "obj":
        if not export_obj(shape, str(path)):
            raise RuntimeError("the OBJ export failed")
    elif fmt == "glb":
        if not export_gltf(shape, str(path), binary=True):
            raise RuntimeError("the GLB export failed")
    else:
        raise Rejected(f"unknown format {fmt!r}; use one of {', '.join(EXTENSIONS)}")


def failure_message(error: BaseException) -> str:
    """What went wrong, with the line of design.py it happened on."""
    if isinstance(error, Rejected):
        return str(error)
    line = None
    for frame in traceback.extract_tb(error.__traceback__):
        if frame.filename == CODE_FILE:
            line = frame.lineno
    where = f"line {line}: " if line else ""
    return f"{where}{type(error).__name__}: {error}"


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("build", "export"):
        print(__doc__, file=sys.stderr)
        return 2
    job_dir = Path(argv[2])
    report: dict = {"ok": False}
    code = 1
    try:
        isolated = limit_resources()
        notes: list[str] = []
        shape = run_design(job_dir)
        report = {"ok": True, "isolated": isolated, **describe(shape, notes), "notes": notes}
        if argv[1] == "build":
            write_model(shape, job_dir / "model.stl", "stl")
        else:
            fmt = (argv[3] if len(argv) > 3 else "").lower()
            if fmt not in EXTENSIONS:
                raise Rejected(f"unknown format {fmt!r}; use one of {', '.join(EXTENSIONS)}")
            write_model(shape, job_dir / f"model.{EXTENSIONS[fmt]}", fmt)
            report["format"] = fmt
        code = 0
    except MemoryError:
        report = {"ok": False, "error": "the model ran out of memory; simplify it"}
    except BaseException as error:  # the model's code can raise anything
        report = {"ok": False, "error": failure_message(error)}
    (job_dir / REPORT_FILE).write_text(json.dumps(report, indent=1))
    print(json.dumps(report))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
