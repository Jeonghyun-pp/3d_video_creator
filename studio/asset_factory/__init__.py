"""Standards-based CAD asset factory: spec + standard tables -> gated GLB -> library asset.

Host modules (spec, factory) are stdlib-only; cad_worker.py runs only in the
CAD interpreter (.venvs/cad or STUDIO_CAD_PYTHON); blender/ runs inside Blender.
"""
