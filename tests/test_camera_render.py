"""Blender integration checks for cut camera control."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
try:   # the same lookup the engine uses (STUDIO_BLENDER, PATH, /Applications/Blender.app)
    from studio.common import StudioError, blender_binary
    BLENDER = blender_binary()
except StudioError:
    BLENDER = None


@unittest.skipUnless(BLENDER, "Blender unavailable")
class CameraRenderTest(unittest.TestCase):
    def test_keyframes_and_named_scene_camera_move_in_rendered_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            blend = temp / "scene.blend"
            setup = temp / "setup.py"
            setup.write_text(f'''
import bpy
from mathutils import Vector
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
cube = bpy.context.object
cube.name = "Subject"
collection = bpy.data.collections.new("subject")
bpy.context.scene.collection.children.link(collection)
for source in list(cube.users_collection):
    source.objects.unlink(cube)
collection.objects.link(cube)
material = bpy.data.materials.new("Red")
material.diffuse_color = (1, 0, 0, 1)
cube.data.materials.append(material)
camera = bpy.data.objects.new("AuthoredCamera", bpy.data.cameras.new("AuthoredCamera"))
bpy.context.scene.collection.objects.link(camera)
camera.data.type = "ORTHO"
camera.data.ortho_scale = 7
for frame, x in ((1, 0), (30, 3)):
    camera.location = (x, -10, 0)
    camera.rotation_euler = (Vector((x, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.keyframe_insert(data_path="location", frame=frame)
    camera.keyframe_insert(data_path="rotation_euler", frame=frame)
bpy.context.scene.camera = camera
bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})
''')
            subprocess.run([BLENDER, "-b", "-noaudio", "--python", str(setup)], check=True,
                           capture_output=True, text=True)
            scene = {"fps": 30, "objects": [{"id": "subject"}], "scenes": [{
                "id": "shot", "duration": 1, "visible_objects": ["subject"],
                "animation": {"type": "none"}, "camera": {}
            }]}
            cameras = [
                {"type": "orthographic", "movement": "keyframes", "interpolation": "BEZIER",
                 "keyframes": [
                     {"t": 0, "location": [0, -10, 0], "target": [0, 0, 0], "ortho_scale": 7},
                     {"t": 1, "location": [3, -10, 0], "target": [3, 0, 0], "ortho_scale": 7}]},
                {"type": "orthographic", "movement": "scene_camera", "camera_name": "AuthoredCamera"},
                {"type": "perspective", "movement": "keyframes", "interpolation": "LINEAR",
                  "keyframes": [
                      {"t": 0, "location": [0, -10, 0], "target": [0, 0, 0], "lens_mm": 20},
                      {"t": 1, "location": [0, -10, 0], "target": [0, 0, 0], "lens_mm": 60}]}
            ]
            for index, camera in enumerate(cameras):
                for fps in (12, 30):
                    with self.subTest(camera=camera["movement"], fps=fps):
                        self.check_render(temp, blend, scene, camera, index, fps)

    def check_render(self, temp, blend, scene, camera, index, fps):
                    scene["scenes"][0]["camera"] = camera
                    spec = temp / f"scene_{index}.json"
                    spec.write_text(json.dumps(scene))
                    output = temp / f"frames_{index}_{fps}"
                    output.mkdir()
                    result = subprocess.run([BLENDER, "-b", str(blend), "-noaudio", "--python",
                                    str(ROOT / "scripts" / "blender_render.py"), "--",
                                    str(spec), "shot", str(output), "64", "64", str(fps)],
                                   check=True, capture_output=True, text=True, timeout=120,
                                   env={**os.environ, "STUDIO_RENDER_ENGINE": "BLENDER_WORKBENCH"})
                    frames = sorted(output.glob("*.png"))
                    self.assertEqual(len(frames), fps, result.stdout + result.stderr)
                    def red_pixels(path):
                        image = Image.open(path).convert("RGB")
                        return [x for y in range(image.height) for x in range(image.width)
                                if (rgb := image.getpixel((x, y)))[0] > rgb[1] * 1.4 and rgb[0] > 80]
                    first, last = red_pixels(frames[0]), red_pixels(frames[-1])
                    self.assertTrue(first and last)
                    if "lens_mm" in camera.get("keyframes", [{}])[0]:
                        self.assertGreater(len(last), len(first) * 2)
                    else:
                        self.assertGreater(sum(first) / len(first) - sum(last) / len(last), 10)


if __name__ == "__main__":
    unittest.main()
