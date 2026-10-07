"""Check catalog references in every branch without invoking external tools."""

import ast
from pathlib import Path
from string import Formatter

from orionis_installer.messages import MESSAGES as CORE_MESSAGES
from orionis_installer.ui.messages import CLI_HELP, LABELS
from orionis_installer.ui.messages import MESSAGES as UI_MESSAGES


def test_message_keys_and_named_format_arguments_exist():
    """Validate catalog keys and named format fields in every source branch."""
    package = Path(__file__).parents[2] / "src" / "orionis_installer"
    formatter = Formatter()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        catalogs = {}
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom):
                for name in statement.names:
                    alias = name.asname or name.name
                    if statement.module == "orionis_installer.messages" and name.name == "MESSAGES":
                        catalogs[alias] = CORE_MESSAGES
                    elif statement.module == "orionis_installer.ui.messages":
                        catalog = {
                            "MESSAGES": UI_MESSAGES,
                            "CLI_HELP": CLI_HELP,
                            "LABELS": LABELS,
                        }.get(name.name)
                        if catalog is not None:
                            catalogs[alias] = catalog
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id in catalogs
                and isinstance(node.slice, ast.Constant)
            ):
                continue
            assert node.slice.value in catalogs[node.value.id], (
                path,
                node.lineno,
                node.slice.value,
            )
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
                and isinstance(node.func.value, ast.Subscript)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id in catalogs
                and isinstance(node.func.value.slice, ast.Constant)
            ):
                continue
            reference = node.func.value
            template = catalogs[reference.value.id][reference.slice.value]
            required = {field for _, field, _, _ in formatter.parse(template) if field is not None}
            assert required == {keyword.arg for keyword in node.keywords}, (path, node.lineno)
            assert not node.args
