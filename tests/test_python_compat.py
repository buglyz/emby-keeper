import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PY38_RUNTIME_GENERIC_NAMES = {"dict", "frozenset", "list", "set", "tuple", "type"}


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
            continue
        break
    return False


def _annotation_nodes(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            yield node.returns
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield node.annotation


def _uses_py39_runtime_generics(annotation: ast.AST) -> bool:
    for node in ast.walk(annotation):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in PY38_RUNTIME_GENERIC_NAMES:
                return True
    return False


def test_py38_runtime_generic_annotations_are_deferred():
    offenders = []
    for package in ("embykeeper", "embykeeperapi"):
        for path in (REPO_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _has_future_annotations(tree):
                continue
            if any(_uses_py39_runtime_generics(annotation) for annotation in _annotation_nodes(tree)):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []
