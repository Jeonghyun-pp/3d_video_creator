"""reference collect (studio/reference_fetch.py): licensed photos of a real subject, from allow-listed hosts only, each
with an index row; the broker starts the download outside the sandbox only for a job that writes inside its own
project's references/photos (2026-10-11: the booster was modelled with no photo - the agent had no way to get one).
No network: the API answers and downloads are recorded fixtures."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from studio import broker, jobs, reference_fetch as rf
from studio.common import REPO, StudioError, read_json, write_json


def page(pageid, licence, mime='image/jpeg', url=None, title=None):
    return {'pageid': pageid, 'title': title or f'File:p{pageid}.jpg', 'imageinfo': [{
        'mime': mime, 'thumburl': url or f'https://upload.wikimedia.org/x/p{pageid}.jpg', 'descriptionurl': f'https://commons.wikimedia.org/wiki/File:p{pageid}.jpg',
        'width': 3000, 'height': 2000, 'extmetadata': {'LicenseShortName': {'value': licence}, 'Artist': {'value': '<a href="x">NASA</a>'}}}]}


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (40, 30), (200, 210, 220)).save(out, 'JPEG')
    return out.getvalue()


class LicenceAndHostTest(unittest.TestCase):
    def test_free_licences_pass_and_the_rest_are_refused(self):
        for name in ('CC0', 'Public domain', 'PD-USGov-NASA', 'CC BY 2.0', 'CC BY-SA 4.0', 'CC BY-SA 3.0 igo'):
            self.assertTrue(rf.licence_ok(name), name)
        for name in ('', None, 'CC BY-NC 2.0', 'CC BY-ND 4.0', 'CC BY-NC-SA 2.0', 'All rights reserved', 'Fair use', 'unknown'):
            self.assertFalse(rf.licence_ok(name), name)

    def test_only_https_on_the_sources_hosts(self):
        self.assertTrue(rf.allowed_url('https://upload.wikimedia.org/a.jpg', 'wikimedia_commons'))
        for url in ('http://upload.wikimedia.org/a.jpg', 'https://evil.example/a.jpg', 'https://upload.wikimedia.org.evil.example/a.jpg', 'file:///etc/passwd'):
            self.assertFalse(rf.allowed_url(url, 'wikimedia_commons'), url)
        with self.assertRaises(StudioError):
            rf._get('https://evil.example/a.jpg', 'wikimedia_commons')

    def test_api_rows_keep_licensed_photos_and_say_why_others_are_refused(self):
        data = {'query': {'pages': {'1': page(1, 'CC0'), '2': page(2, 'CC BY-NC 2.0'), '3': page(3, 'Public domain', mime='video/webm'),
                                    '4': page(4, 'CC BY 2.0', url='https://cdn.example/p4.jpg'), '5': {'title': 'File:gone.jpg', 'missing': ''},
                                    '6': page('../../x', 'CC0', title='File:odd.jpg')}}}
        kept, refused = rf.rows_from_api(data)
        self.assertEqual([r['page_id'] for r in kept], [1])
        self.assertEqual(kept[0]['artist'], 'NASA')   # markup stripped
        why = {r['title']: r['why'] for r in refused}
        self.assertIn('licence', why['File:p2.jpg'])
        self.assertIn('not a photo', why['File:p3.jpg'])
        self.assertIn('host', why['File:p4.jpg'])
        self.assertEqual(why['File:gone.jpg'], 'missing')
        self.assertIn('page id', why['File:odd.jpg'])   # the page id names the stored file

    def test_only_decodable_images_are_kept_and_re_encoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(rf.store_image(jpeg(), 'image/jpeg', Path(tmp) / 'a.jpg'), [40, 30])
            for body, kind in ((b'<html>', 'text/html'), (b'not an image', 'image/jpeg')):
                with self.assertRaises(StudioError):
                    rf.store_image(body, kind, Path(tmp) / 'b.jpg')


class CollectTest(unittest.TestCase):
    def setUp(self):
        (REPO / 'projects').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=REPO / 'projects')
        self.project = Path(self.tmp.name).resolve()
        write_json(self.project / 'project.json', {'project_id': 'collect_test'})

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, **more):
        path = self.project / 'runs' / 'r1' / 'jobs' / 'j1' / 'job.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        job = {'job_id': 'j1', 'operation': 'reference_fetch', 'source': 'wikimedia_commons', 'project_dir': str(self.project),
               'output_dir': str(self.project / rf.PHOTOS_DIR), 'titles': ['File:p1.jpg'], 'query': None, 'subject_id': 'booster',
               'status': 'queued', 'updated_at': '2026-10-11T00:00:00+00:00', 'worker_token': 't',
               'cancel_path': str(path.parent / 'cancel'), 'progress_path': str(path.parent / 'progress.json'), **more}
        write_json(path, job)
        return path

    def test_collect_downloads_indexes_and_reuses(self):
        answer = {'query': {'pages': {'1': page(1, 'CC0'), '2': page(2, 'CC BY-NC 2.0')}}}
        with mock.patch.object(rf, '_api', return_value=answer), mock.patch.object(rf, '_get', return_value=(jpeg(), 'image/jpeg')) as get, \
                mock.patch.object(rf, 'PAUSE_S', 0):
            result = rf.run_reference_worker(self.job(), 't')
            self.assertEqual(result['status'], 'complete', result)
            self.assertEqual(get.call_count, 1)
            again = rf.run_reference_worker(self.job(), 't')   # already collected and unchanged: not fetched again
            self.assertEqual(get.call_count, 1)
        row = rf.index_rows(self.project)[0]
        self.assertEqual((row['file'], row['licence'], row['use'], row['subject_id']), ('references/photos/commons_1.jpg', 'CC0', 'compare', 'booster'))
        self.assertEqual(rf.provenance(self.project, 'references/photos/commons_1.jpg')['page_id'], 1)
        self.assertEqual([r['title'] for r in again['result']['refused']], ['File:p2.jpg'])
        self.assertTrue(Path(result['result']['contact_sheet']).is_file())

    def test_a_job_writing_elsewhere_is_refused(self):
        result = rf.run_reference_worker(self.job(output_dir=str(self.project / 'shots')), 't')
        self.assertEqual((result['status'], result['error']['code']), ('failed', 'REFERENCE_FETCH_REFUSED'))
        with self.assertRaises(StudioError):
            rf.run_reference_worker(self.job(), 'wrong token')

    def test_collected_photo_views_keep_provenance_and_stay_local(self):
        from studio.photo_match import check_view
        out = self.project / rf.PHOTOS_DIR
        out.mkdir(parents=True)
        rf.store_image(jpeg(), 'image/jpeg', out / 'stray.jpg')
        record = {'id': 'v', 'image': 'references/photos/stray.jpg', 'licence': 'local_only', 'subject_id': 's', 'mask': {}, 'points': [], 'parts': {}}
        self.assertIn('no row', ' '.join(check_view(self.project, record)))   # dropped in by hand: no source, no licence
        with mock.patch.object(rf, '_api', return_value={'query': {'pages': {'1': page(1, 'CC0')}}}), \
                mock.patch.object(rf, '_get', return_value=(jpeg(), 'image/jpeg')), mock.patch.object(rf, 'PAUSE_S', 0):
            rf.run_reference_worker(self.job(), 't')
        record['image'] = 'references/photos/commons_1.jpg'
        self.assertEqual(check_view(self.project, record), [])
        self.assertIn('local_only', ' '.join(check_view(self.project, {**record, 'licence': 'cleared'})))
        Image.new('RGB', (40, 30), 'black').save(out / 'commons_1.jpg')
        self.assertIn('changed after it was collected', ' '.join(check_view(self.project, record)))

    def test_the_broker_starts_only_a_valid_reference_job_outside_the_sandbox(self):
        path = self.job()
        with mock.patch.dict(os.environ, {broker.BROKERED: '1'}), mock.patch.object(jobs, 'spawn_worker') as spawn:
            self.assertIsNone(jobs.start_worker(path))
            spawn.assert_not_called()
        self.assertEqual(read_json(path)['status'], 'awaiting_dispatch')
        from studio import freeze
        clean = {'baseline': 'b', 'changed': {}, 'warnings': []}
        with mock.patch.object(freeze, 'check', return_value=clean), mock.patch.object(jobs, 'spawn_worker') as spawn:
            self.assertEqual([s['entry'] for s in broker.dispatch([str(path)])], ['_reference_worker'])
        for bad, word in (({'output_dir': str(self.project / 'shots')}, 'references/photos'),
                          ({'output_dir': str(Path.home() / '.ssh')}, 'output_dir'),
                          ({'source': 'anywhere'}, 'source'), ({'titles': [], 'query': None}, 'titles')):
            job = self.job(**bad)
            data = read_json(job); data['status'] = 'awaiting_dispatch'; write_json(job, data)
            with mock.patch.object(freeze, 'check', return_value=clean), mock.patch.object(jobs, 'spawn_worker') as spawn:
                self.assertEqual(broker.dispatch([str(job)]), [], bad)
                spawn.assert_not_called()
            self.assertIn(word, read_json(job)['error']['message'])

    def test_the_agents_own_sandbox_is_told_to_use_studio_run(self):
        from studio.references import request_collect
        with mock.patch.dict(os.environ, {'CODEX_SANDBOX': 'seatbelt'}), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(broker.BROKERED, None)
            with self.assertRaises(StudioError) as caught:
                request_collect(self.project, 'booster', ['File:p1.jpg'])
        self.assertEqual(caught.exception.code, 'NETWORK_UNAVAILABLE')
        with self.assertRaises(StudioError):
            request_collect(self.project, 'booster', ['File:p1.jpg'], query='both')


if __name__ == '__main__':
    unittest.main()
