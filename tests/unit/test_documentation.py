"""Check documentation requirements across maintained Python source files."""

import ast
from pathlib import Path


def test_every_function_has_a_double_quoted_numpy_docstring():
    """Require concise NumPy docstrings on all functions, including nested helpers."""
    root = Path(__file__).parents[2]
    paths = [
        path
        for directory in ("src", "scripts", "tests")
        for path in (root / directory).rglob("*.py")
    ]
    paths.append(root / "tests/fixtures/skeleton/reactor")
    violations = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            label = f"{path.relative_to(root)}:{node.lineno} {node.name}"
            documentation = ast.get_docstring(node)
            if not documentation:
                violations.append(f"{label}: missing docstring")
                continue
            expression = ast.get_source_segment(source, node.body[0]) or ""
            if not expression.startswith('"""'):
                violations.append(f"{label}: use triple double quotes")
            if "Examples" in documentation.splitlines():
                violations.append(f"{label}: remove the Examples section")
            for heading, underline in (
                ("Parameters", "----------"),
                ("Returns", "-------"),
                ("Yields", "------"),
                ("Raises", "------"),
            ):
                lines = documentation.splitlines()
                if heading in lines:
                    index = lines.index(heading)
                    if index + 1 >= len(lines) or lines[index + 1] != underline:
                        violations.append(f"{label}: invalid NumPy {heading} section")
    assert not violations, "\n".join(violations)
