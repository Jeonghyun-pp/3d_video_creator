"""Which source files a Blender-side module runs: itself plus every module in this folder it imports, at any depth and
from anywhere in the file (look.py imports its passes inside functions). Pure Python, no bpy - the host's freeze check
uses the same answer as the look's input hash, so a new import is covered without editing a list."""
from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).parent


def module_closure(start, folder=HERE):
    folder = Path(folder)
    seen, todo = set(), [start if start.endswith('.py') else f'{start}.py']
    while todo:
        name = todo.pop()
        if name in seen or not (folder / name).is_file():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((folder / name).read_text())):
            if isinstance(node, ast.Import):
                todo += [f"{alias.name.split('.')[0]}.py" for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                todo.append(f"{node.module.split('.')[0]}.py")
    return tuple(sorted(seen))
