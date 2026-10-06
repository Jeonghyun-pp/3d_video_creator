"""The project's gate severity inside Blender (studio/gates.py decides it; build_scene stores it on the scene).
A code not in the table is an error: only gates the host lists as softenable can ever be warnings."""
import json

import bpy


def table():
    return json.loads(bpy.context.scene.get('studio_gate_severity', '{}'))


def is_error(code):
    return table().get(code, 'error') == 'error'


def split(failures, key):
    """(errors, warnings) of a list of failure dicts, by the code under `key`."""
    errors = [f for f in failures if is_error(f[key])]
    return errors, [f for f in failures if f not in errors]
