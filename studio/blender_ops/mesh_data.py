"""Shared mesh datablocks: objects of the same shape share one mesh (linked duplicates). Anything that edits
an object's mesh - materials, face attributes, smoothing, polygon flips - calls unique_data(obj) first, so the
edit never leaks into the other objects that share it."""
from __future__ import annotations


def unique_data(obj):
    """Give obj its own copy of a mesh other objects share (no-op when it is the only user)."""
    data = getattr(obj, 'data', None)
    if data is not None and data.users > 1:
        obj.data = data.copy()
    return obj
