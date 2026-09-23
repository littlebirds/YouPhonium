"""Shared output-library and upload de-duplication regressions."""

import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import main


SCORE = b'''<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Euphonium</part-name></score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><type>whole</type></note></measure></part></score-partwise>'''


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library_patch = patch.object(main, '_library_dir', return_value=self.root)
        self.library_patch.start()
        self.addCleanup(self.library_patch.stop)

    def add_score(self, output_name, original, content_hash, engine='homr'):
        path = self.root / output_name
        path.write_bytes(SCORE)
        main._write_library_metadata(path, original, content_hash, engine)
        return path

    def test_identity_requires_both_original_name_and_content_hash(self):
        first_hash = hashlib.sha256(b'first').hexdigest()
        second_hash = hashlib.sha256(b'second').hexdigest()
        first = self.add_score('March--a7937b64.musicxml', 'March.pdf', first_hash)

        self.assertEqual(main._find_duplicate('March.pdf', first_hash, 'homr'), first)
        self.assertIsNone(main._find_duplicate('March.pdf', second_hash, 'homr'))
        self.assertIsNone(main._find_duplicate('Other.pdf', first_hash, 'homr'))
        self.assertNotEqual(
            main._hashed_output_name('March.pdf', first_hash, 'homr'),
            main._hashed_output_name('March.pdf', second_hash, 'homr'),
        )

    def test_library_lists_legacy_and_managed_scores(self):
        managed = self.add_score('March--a7937b64.musicxml', 'March.pdf', 'abc')
        legacy = self.root / 'Legacy.musicxml'
        legacy.write_bytes(SCORE)

        items = main.list_library()['items']

        self.assertEqual({item['filename'] for item in items}, {'March.pdf', 'Legacy.musicxml'})
        self.assertEqual(main._library_path(main._library_id(managed)), managed)

    def test_short_hash_collision_gets_numeric_suffix(self):
        self.add_score('March--12345678.musicxml', 'March.pdf', '12345678aaaa')
        self.assertEqual(
            main._hashed_output_name('March.pdf', '12345678bbbb', 'homr'),
            'March--12345678-2.pdf',
        )

    def test_delete_removes_only_selected_score_family(self):
        selected = self.add_score('March--a7937b64.musicxml', 'March.pdf', 'abc')
        selected.with_suffix('.recognition.json').write_text('{}')
        keep = self.add_score('March--16367aac.musicxml', 'March.pdf', 'def')

        result = main.delete_library_item(main._library_id(selected))

        self.assertTrue(result['success'])
        self.assertFalse(selected.exists())
        self.assertFalse(selected.with_suffix('.recognition.json').exists())
        self.assertFalse(selected.with_suffix('.library.json').exists())
        self.assertTrue(keep.exists())

    def test_library_marker_edit_saves_xml_and_refreshes_warnings(self):
        selected = self.add_score('March--a7937b64.musicxml', 'March.pdf', 'abc')
        selected.with_suffix('.recognition.json').write_text(json.dumps({
            'issues': [], 'source_layout': {'preserved': True},
        }))
        item_id = main._library_id(selected)

        batch = main.MusicXmlEditBatch(edits=[main.MusicXmlMarkerEdit(
            measure='1', action='add_backward_repeat')])
        with patch.object(main, 'musicxml_to_midi', return_value=b''):
            main.save_library_edits(item_id, batch)

        xml = selected.read_text()
        self.assertIn('repeat direction="backward"', xml)
        report = json.loads(selected.with_suffix('.recognition.json').read_text())
        self.assertEqual(report['issue_counts']['repeat_unmatched_backward'], 1)
        self.assertEqual(report['source_layout'], {'preserved': True})

    def test_library_validation_refreshes_report_without_editing_score(self):
        selected = self.add_score('March--a7937b64.musicxml', 'March.pdf', 'abc')
        before = selected.read_bytes()

        result = main.validate_library_item(main._library_id(selected))

        self.assertTrue(result['success'])
        self.assertEqual(result['recognition_report']['validator_version'], 2)
        self.assertEqual(selected.read_bytes(), before)
        self.assertTrue(selected.with_suffix('.recognition.json').exists())

    def test_marker_preview_is_not_saved_until_batch_commit(self):
        selected = self.add_score('March--a7937b64.musicxml', 'March.pdf', 'abc')
        item_id = main._library_id(selected)
        before = selected.read_bytes()
        batch = main.MusicXmlEditBatch(edits=[main.MusicXmlMarkerEdit(
            measure='1', action='add_forward_repeat')])

        preview = main.preview_library_edits(item_id, batch)

        self.assertEqual(selected.read_bytes(), before)
        self.assertIn(b'repeat direction="forward"', base64.b64decode(preview['musicxml_base64']))
        with patch.object(main, 'musicxml_to_midi', return_value=b''):
            saved = main.save_library_edits(item_id, batch)
        self.assertEqual(saved['edit_changed'], 1)
        self.assertIn(b'repeat direction="forward"', selected.read_bytes())

    def test_upload_job_reuses_exact_duplicate_without_running_omr(self):
        content = b'same source bytes'
        digest = hashlib.sha256(content).hexdigest()
        existing = self.add_score('Ballad--' + digest[:8] + '.musicxml', 'Ballad.pdf', digest)
        upload = self.root / 'upload.pdf'
        upload.write_bytes(content)
        job_id = 'dedup-job'
        main._upload_jobs[job_id] = {'status': 'processing', 'message': 'Starting'}
        self.addCleanup(lambda: main._upload_jobs.pop(job_id, None))
        loaded = {'success': True, 'library_id': main._library_id(existing),
                  'engine': 'homr', 'musicxml_path': str(existing)}

        with patch.object(main, '_recognize_upload', side_effect=AssertionError('OMR must not run')), \
             patch.object(main, '_result_from_musicxml', return_value=loaded):
            main._run_upload_job(job_id, upload, 'Ballad.pdf', 'homr', False)

        result = main.upload_status(job_id)['result']
        self.assertTrue(result['deduplicated'])
        self.assertEqual(result['library_id'], main._library_id(existing))
        self.assertFalse(upload.exists())


if __name__ == '__main__':
    unittest.main()
