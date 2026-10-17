"""Blender: an id still written after the depth EXR holds the palette's display values (picture_passes._png).

The depth pass renders a multilayer EXR, which leaves linear float output settings on the scene; a PNG written with them
stores linear values (a 0.5 display grey comes out 0.214), and the id pass's palette then decodes the grey support as
the key colour - on 2026-10-17 a sea read as part of the subject. A grey plane, flat-shaded in its object colour, is
rendered after an EXR: with _png it reads 0.5, without it the linear value.
Run: blender --background --factory-startup --python tests/studio/picture_passes_png_smoke.py
"""
from pathlib import Path
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import id_view  # noqa: E402
import picture_passes  # noqa: E402

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=10)
plane = bpy.context.object
plane.color = (0.214, 0.214, 0.214, 1.0)          # the palette's support grey (display 0.5)
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
scene.collection.objects.link(cam)
cam.location = (0, 0, 6)
scene.camera = cam
scene.render.resolution_x = scene.render.resolution_y = 32


def grey_after_exr(fix):
    with tempfile.TemporaryDirectory() as tmp:
        picture_passes._render(scene, Path(tmp) / 'd.exr', 'OPEN_EXR_MULTILAYER')
        s = scene.render.image_settings
        s.media_type, s.file_format = 'IMAGE', 'PNG'   # what the passes did before _png: format back, depth not
        id_view.apply(scene)
        if fix:
            picture_passes._png(scene.render, 'RGBA')
        print('SETTINGS', fix, s.color_depth, getattr(s, 'color_management', None))
        scene.render.filepath = str(Path(tmp) / 'id.png')
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(str(Path(tmp) / 'id.png'))
        px = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        value = float(np.median(px.reshape(-1, 4)[:, 0]))
        bpy.data.images.remove(img)
        return value


fixed = grey_after_exr(True)
assert abs(fixed - 0.5) < 0.02, fixed
print('PICTURE_PASSES_PNG_SMOKE ok', round(fixed, 3), 'unfixed', round(grey_after_exr(False), 3))
