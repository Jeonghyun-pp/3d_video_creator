"""The newest of numbered versions, by number and never by text: v10 after v9, blender-4.10 after blender-4.9 (2026-10-12
audit: string sorts picked v9 over v10). Pure Python, host and Blender."""
from __future__ import annotations

import re


def version_key(name):
    """The numbers in a version's name, in order: 'v0010' -> (10,), 'blender-4.10.json' -> (4, 10)."""
    return tuple(int(n) for n in re.findall(r'\d+', str(name)))


def newest(items, name=lambda item: getattr(item, 'name', item)):
    """The item whose name has the highest version_key (paths by their name, strings as they are)."""
    return max(items, key=lambda item: version_key(name(item)))
