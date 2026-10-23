"""A world written again keeps its elements' links (studio/world.with_links): 2026-10-23, r3 - each rewrite of the world
(a shell, a why) dropped every link and ten elements read as unbuilt clay boxes."""
from pathlib import Path
import tempfile
import unittest

from studio import world
from studio.common import write_json


class WithLinksTest(unittest.TestCase):
    def test_a_rewrite_keeps_what_builds_each_element_unless_it_says_otherwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp) / 'project.json', {'project_id': 'w'})
            write_json(Path(tmp) / 'world.json', {'elements': [{'id': 'tank', 'ids': ['boat/tank']}, {'id': 'sea', 'ids': ['sea_box']}]})
            body = {'elements': [{'id': 'tank'}, {'id': 'sea', 'ids': ['sea_plane']}, {'id': 'gull'}]}
            got = {e['id']: e.get('ids') for e in world.with_links(tmp, body)['elements']}
            self.assertEqual(got, {'tank': ['boat/tank'], 'sea': ['sea_plane'], 'gull': None})
            self.assertEqual(world.with_links(None, body), body)


if __name__ == '__main__':
    unittest.main()
