"""Blender entry scripts run as one module namespace: an import inside a module-level `if` rebinds the global name
for the rest of the script (build_scene's `compare` once became workbench_tools.compare after a workbench commit and
broke the preserve check). A name bound by imports from two different sources is refused for every ops file."""
import ast
from pathlib import Path
import unittest

OPS = Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'


def module_level_imports(tree):
    """(name, source) for imports executed at module level, including inside module-level if/try/with/for blocks."""
    found = []

    def visit(statements):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.Import):
                found.extend([((alias.asname or alias.name).split('.')[0], alias.name) for alias in node.names])
            elif isinstance(node, ast.ImportFrom):
                found.extend([(alias.asname or alias.name, f'{node.module}.{alias.name}') for alias in node.names])
            for field in ('body', 'orelse', 'finalbody', 'handlers'):
                block = getattr(node, field, None)
                if isinstance(block, list):
                    visit(block)
    visit(tree.body)
    return found


class BlenderOpsImportTest(unittest.TestCase):
    def test_no_name_is_imported_from_two_sources(self):
        problems = []
        for path in sorted(OPS.rglob('*.py')):
            sources = {}
            for name, source in module_level_imports(ast.parse(path.read_text())):
                sources.setdefault(name, set()).add(source)
            problems += [f'{path.relative_to(OPS)}: {name} <- {sorted(found)}' for name, found in sources.items() if len(found) > 1]
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()
