"""Run: PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -v"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageDraw

from services.recognition_quality import analyze_musicxml, quality_cost, read_musicxml
from services.musicxml_editor import edit_marker
from services.score_image import prepare_score_image, staff_spacing, load_score_image
from services.audiveris_recovery import prepare_wedge_recovery
from services import omr


ATTRIBUTES = """<attributes><divisions>12</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
<staves>2</staves><clef number="1"><sign>G</sign><line>2</line></clef>
<clef number="2"><sign>F</sign><line>4</line></clef></attributes>"""


def pitched(step="C", duration=48, type_="whole", staff=1, extra="", octave=4):
    return (f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
            f"<duration>{duration}</duration><voice>{staff}</voice><type>{type_}</type>"
            f"{extra}<staff>{staff}</staff></note>")


def score(measures):
    return ('<score-partwise version="4.0"><part-list><score-part id="P1">'
            '<part-name>Piano</part-name></score-part></part-list><part id="P1">'
            + measures + '</part></score-partwise>')


def bar(content, number=1, attributes=True, implicit=False):
    return (f'<measure number="{number}" implicit="{"yes" if implicit else "no"}">'
            + (ATTRIBUTES if attributes else '') + content + '</measure>')


def two_staff_bar(number=1):
    return bar(pitched() + '<backup><duration>48</duration></backup>'
               + pitched(staff=2, octave=3), number)


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def xml(self, content, name="score.musicxml"):
        path = self.root / name
        path.write_text(content)
        return path

    def test_full_grand_staff_is_not_overfull(self):
        report = analyze_musicxml(self.xml(score(two_staff_bar())))
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["status"], "unchecked")

    def test_missing_staff_is_not_silently_accepted(self):
        report = analyze_musicxml(self.xml(score(bar(pitched(staff=2)))))
        self.assertEqual(report["issue_counts"], {"empty_staff": 1})
        self.assertEqual(report["issues"][0]["staff"], 1)

    def test_explicit_rest_counts_as_staff_content(self):
        content = (pitched() + '<backup><duration>48</duration></backup>'
                   '<note><rest measure="yes"/><duration>48</duration><staff>2</staff></note>')
        self.assertEqual(analyze_musicxml(self.xml(score(bar(content))))["issues"], [])

    def test_chords_do_not_advance_cursor(self):
        content = (pitched() + pitched("E").replace('<note>', '<note><chord/>')
                   + '<backup><duration>48</duration></backup>' + pitched(staff=2))
        self.assertEqual(analyze_musicxml(self.xml(score(bar(content))))["issues"], [])

    def test_missing_and_duplicate_measure_numbers(self):
        report = analyze_musicxml(self.xml(score(two_staff_bar(1) + two_staff_bar(3) + two_staff_bar(3))))
        self.assertEqual(report["issue_counts"], {"measure_gap": 1, "measure_number": 1})

    def test_unmatched_repeat_markers_are_reported(self):
        backward = '<barline location="right"><repeat direction="backward"/></barline>'
        path = self.xml(score(two_staff_bar(1) + two_staff_bar(2)))
        root = ET.parse(path)
        root.find(".//measure[@number='2']").append(ET.fromstring(backward))
        root.write(path)

        report = analyze_musicxml(path)

        self.assertEqual(report["issue_counts"]["repeat_unmatched_backward"], 1)
        self.assertIn("return to the beginning", report["issues"][-1]["message"])

    def test_matched_repeat_and_volta_markers_are_balanced(self):
        path = self.xml(score(two_staff_bar(1) + two_staff_bar(2) + two_staff_bar(3)))
        edit_marker(path, "1", "add_forward_repeat")
        edit_marker(path, "2", "add_ending_start", "1")
        edit_marker(path, "2", "add_ending_stop", "1")
        edit_marker(path, "2", "add_backward_repeat")
        edit_marker(path, "3", "add_ending_start", "2")
        edit_marker(path, "3", "add_ending_discontinue", "2")

        report = analyze_musicxml(path)

        self.assertFalse(any(item["code"].startswith(("repeat_", "ending_"))
                             for item in report["issues"]))
        edited = read_musicxml(path)
        first = edited.find(".//measure[@number='1']/barline/repeat")
        second = edited.find(".//measure[@number='2']/barline/repeat")
        self.assertEqual((first.get("direction"), second.get("direction")), ("forward", "backward"))

    def test_midi_expands_repeat_and_skips_first_ending_on_second_pass(self):
        path = self.xml(score(
            bar(pitched("C"), 1)
            + bar(pitched("D"), 2, attributes=False)
            + bar(pitched("E"), 3, attributes=False)
        ))
        edit_marker(path, "1", "add_forward_repeat")
        edit_marker(path, "2", "add_ending_start", "1")
        edit_marker(path, "2", "add_ending_stop", "1")
        edit_marker(path, "2", "add_backward_repeat")
        edit_marker(path, "3", "add_ending_start", "2")
        edit_marker(path, "3", "add_ending_stop", "2")

        from music21 import midi
        from services.converter import musicxml_to_midi
        from services.musicxml_layout import get_playback_time_map
        mf = midi.MidiFile()
        mf.readstr(musicxml_to_midi(path))
        performed = midi.translate.midiFileToStream(mf, quantizePost=False)
        pitches = [item.pitch.name for item in performed.recurse().notes]

        self.assertEqual(pitches, ["C", "D", "C", "E"])
        timeline = get_playback_time_map(path)
        self.assertEqual([item["measure_index"] for item in timeline], [0, 1, 0, 2])
        self.assertEqual([item["score_start"] for item in timeline], [0.0, 2.0, 0.0, 4.0])
        right_barline = read_musicxml(path).find(".//measure[@number='2']/barline[@location='right']")
        self.assertEqual([child.tag for child in right_barline], ["ending", "repeat"])

    def test_marker_removal_is_idempotent(self):
        path = self.xml(score(two_staff_bar(1)))
        self.assertEqual(edit_marker(path, "1", "add_forward_repeat"), 1)
        self.assertEqual(edit_marker(path, "1", "add_forward_repeat"), 0)
        self.assertEqual(edit_marker(path, "1", "remove_forward_repeat"), 1)
        self.assertEqual(edit_marker(path, "1", "remove_forward_repeat"), 0)
        with self.assertRaisesRegex(ValueError, "Measure 99 was not found"):
            edit_marker(path, "99", "add_backward_repeat")

    def test_tuplets_dots_and_multiple_voices(self):
        triplet = '<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>'
        content = ''.join(pitched(step, 4, 'eighth', extra=triplet) for step in 'CDE')
        content += pitched('G', 36, 'half', extra='<dot/>')
        content += '<backup><duration>48</duration></backup>' + pitched(staff=2, octave=3)
        path = self.xml(score(bar(content)))
        self.assertEqual(analyze_musicxml(path)["issues"], [])
        from services import converter
        from music21 import midi
        with patch.object(converter, '_normalize_durations', side_effect=AssertionError('Do not round tuplets')), \
             patch.object(converter, '_fix_eighth_as_quarter', side_effect=AssertionError('Do not guess rhythms')), \
             patch.object(converter, '_fix_false_augmentation_dots', side_effect=AssertionError('Do not remove dots')):
            result = converter.musicxml_to_midi(path)
            normalized, _ = converter.musicxml_to_normalized_musicxml(path)
        self.assertIn(b'<actual-notes>3</actual-notes>', normalized)
        mf = midi.MidiFile()
        mf.readstr(result)
        parsed = midi.translate.midiFileToStream(mf, quantizePost=False)
        events = [(n.pitch.nameWithOctave, float(n.quarterLength)) for n in parsed.recurse().notes]
        for step in 'CDE':
            duration = next(d for name, d in events if name == step + '4')
            self.assertAlmostEqual(duration, 1 / 3, places=4)
        self.assertIn(('G4', 3.0), events)
        self.assertIn(('C3', 4.0), events)

    def test_duration_mismatch_is_flagged_not_changed(self):
        path = self.xml(score(bar(pitched(duration=24) + '<backup><duration>24</duration></backup>' + pitched(staff=2))))
        before = path.read_bytes()
        self.assertEqual(analyze_musicxml(path)["issue_counts"]["duration_mismatch"], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_mxl_container_first_and_namespaced_xml(self):
        path = self.root / 'score.mxl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="music/main.xml"/></rootfiles></container>')
            archive.writestr('music/main.xml', score(two_staff_bar()).replace('<score-partwise ', '<score-partwise xmlns="http://www.musicxml.org/ns/musicxml" '))
        self.assertEqual(read_musicxml(path).tag, 'score-partwise')
        self.assertEqual(analyze_musicxml(path)["pitched_note_count"], 2)

    def test_image_scaling_uses_pixels_not_dpi(self):
        path = self.root / 'transparent.png'
        image = Image.new('RGBA', (240, 140), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for first in (20, 80):
            for i in range(5):
                draw.line((15, first + i * 8, 220, first + i * 8), fill='black', width=1)
        image.save(path, dpi=(96, 96))
        self.assertEqual(staff_spacing(load_score_image(path)), 8)
        out = self.root / 'prepared.png'
        preparation = prepare_score_image(path, out)
        self.assertEqual(preparation['scale'], 2)
        with Image.open(out) as result:
            self.assertEqual(result.size, (480, 280))
            self.assertEqual(result.getpixel((0, 0)), 255)
        self.assertEqual(prepare_score_image(path, out, alternate=True)['scale'], 1.5)

    def test_blank_image_not_arbitrarily_enlarged(self):
        image = Image.new('L', (300, 300), 255)
        self.assertIsNone(staff_spacing(image))

    def test_export_recovery_preserves_notes_and_original(self):
        original = self.root / 'original.omr'
        sheet = '''<sheet><voice><slots><entry><value chord="10" status="BEGIN"/></entry></slots></voice>
        <sig><inters><head-chord id="10"/><head-chord id="11"/><wedge id="20"/><wedge id="21"/></inters>
        <relations><relation source="10" target="20"><chord-wedge/></relation>
        <relation source="11" target="21"><chord-wedge/></relation></relations></sig></sheet>'''
        with zipfile.ZipFile(original, 'w') as archive:
            archive.writestr('book.xml', '<book/>')
            archive.writestr('sheet#1/sheet#1.xml', sheet)
        before = original.read_bytes()
        recovered = self.root / 'recovered.omr'
        self.assertEqual(prepare_wedge_recovery(original, recovered), 1)
        self.assertEqual(original.read_bytes(), before)
        with zipfile.ZipFile(recovered) as archive:
            root = ET.fromstring(archive.read('sheet#1/sheet#1.xml'))
        self.assertEqual([e.get('id') for e in root.iter('wedge')], ['20'])
        self.assertEqual(len(list(root.iter('head-chord'))), 2)

    def test_selection_prefers_complete_structure_not_note_count(self):
        poor = self.xml(score(bar(pitched(staff=2))), 'poor.xml')
        good = self.xml(score(two_staff_bar()), 'good.xml')
        self.assertGreater(quality_cost(analyze_musicxml(poor)), quality_cost(analyze_musicxml(good)))
        source = self.root / 'source.png'
        Image.new('L', (30, 30), 255).save(source)
        with patch.object(omr, 'run_omr', side_effect=[poor, good]) as runner:
            self.assertEqual(omr._audiveris_image(source, self.root / 'work'), good)
        self.assertEqual(runner.call_count, 2)

    def test_failed_alternate_keeps_successful_primary(self):
        poor = self.xml(score(bar(pitched(staff=2))))
        source = self.root / 'source.png'
        Image.new('L', (30, 30), 255).save(source)
        with patch.object(omr, 'run_omr', side_effect=[poor, RuntimeError('failed retry')]):
            self.assertEqual(omr._audiveris_image(source, self.root / 'work'), poor)

    def test_complete_primary_skips_second_pass(self):
        good = self.xml(score(two_staff_bar()))
        source = self.root / 'source.png'
        Image.new('L', (30, 30), 255).save(source)
        with patch.object(omr, 'run_omr', return_value=good) as runner:
            self.assertEqual(omr._audiveris_image(source, self.root / 'work'), good)
        self.assertEqual(runner.call_count, 1)

    def test_low_resolution_retry_when_staff_estimate_unavailable(self):
        good = self.xml(score(two_staff_bar()))
        source = self.root / 'source.png'
        Image.new('L', (30, 40), 255).save(source)
        sizes = []
        def recognize(prepared, *args, **kwargs):
            with Image.open(prepared) as image:
                sizes.append(image.size)
            if len(sizes) == 1:
                raise RuntimeError('Too low interline value')
            return good
        with patch.object(omr, 'run_omr', side_effect=recognize):
            self.assertEqual(omr._audiveris_image(source, self.root / 'work'), good)
        self.assertEqual(sizes, [(30, 40), (60, 80)])


if __name__ == '__main__':
    unittest.main()
