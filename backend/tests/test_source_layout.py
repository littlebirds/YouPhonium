from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET
import zipfile

from services.source_layout import (
    preserve_source_layout,
    read_source_layout,
    normalize_compact_multiple_rests,
    continue_measure_numbers_across_pages,
)
from services.recognition_quality import read_musicxml

try:
    import verovio
except ImportError:
    verovio = None


class SourceLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def project(self, pages, counts=None):
        path = self.directory / 'source.omr'
        number = 1
        with zipfile.ZipFile(path, 'w') as archive:
            for page_index, sizes in enumerate(pages, 1):
                sheet = ET.Element('sheet')
                ET.SubElement(sheet, 'picture', width='1200', height='1500')
                page = ET.SubElement(sheet, 'page')
                for system_index, size in enumerate(sizes):
                    system = ET.SubElement(page, 'system')
                    for _ in range(size):
                        ET.SubElement(system, 'stack', id=str(number))
                        number += 1
                    part = ET.SubElement(system, 'part', id='1')
                    for i in range(counts[system_index] if counts else 2):
                        ET.SubElement(part, 'staff', id=str(i + 1))
                archive.writestr(f'sheet#{page_index}/sheet#{page_index}.xml', ET.tostring(sheet))
        return path

    def score(self, count, missing=None, compressed=False):
        root = ET.Element('score-partwise', version='4.0')
        plist = ET.SubElement(root, 'part-list')
        partinfo = ET.SubElement(plist, 'score-part', id='P1')
        ET.SubElement(partinfo, 'part-name').text = 'Piano'
        part = ET.SubElement(root, 'part', id='P1')
        for number in range(1, count + 1):
            if number == missing:
                continue
            measure = ET.SubElement(part, 'measure', number=str(number))
            # Deliberately wrong breaks: every measure starts a system.
            ET.SubElement(measure, 'print', {'new-system': 'yes'})
            if number == 1:
                attributes = ET.SubElement(measure, 'attributes')
                ET.SubElement(attributes, 'divisions').text = '1'
                time = ET.SubElement(attributes, 'time')
                ET.SubElement(time, 'beats').text = '4'
                ET.SubElement(time, 'beat-type').text = '4'
                ET.SubElement(attributes, 'staves').text = '2'
                for staff, sign, line in [(1, 'G', '2'), (2, 'F', '4')]:
                    clef = ET.SubElement(attributes, 'clef', number=str(staff))
                    ET.SubElement(clef, 'sign').text = sign
                    ET.SubElement(clef, 'line').text = line
                ET.SubElement(attributes, 'staff-details', {'number': '1', 'print-object': 'no'})
            # Only bass notes: the empty treble must still be rendered.
            note = ET.SubElement(measure, 'note')
            pitch = ET.SubElement(note, 'pitch')
            ET.SubElement(pitch, 'step').text = 'C'
            ET.SubElement(pitch, 'octave').text = '3'
            ET.SubElement(note, 'duration').text = '4'
            ET.SubElement(note, 'type').text = 'whole'
            ET.SubElement(note, 'staff').text = '2'
        path = self.directory / ('score.mxl' if compressed else 'score.musicxml')
        if compressed:
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
                archive.writestr('score.xml', ET.tostring(root))
                archive.writestr('extra.txt', b'keep me')
        else:
            path.write_bytes(ET.tostring(root))
        return path

    def test_exact_unequal_systems_and_page_breaks(self):
        project = self.project([[2, 3], [1]])
        path = self.score(6)
        notes_before = [ET.tostring(n) for n in read_musicxml(path).iter('note')]
        source = preserve_source_layout(path, project)
        self.assertTrue(source['preserved'])
        self.assertEqual([s['measure_count'] for s in source['systems']], [2, 3, 1])
        self.assertEqual([s['staff_count'] for s in source['systems']], [2, 2, 2])
        root = read_musicxml(path)
        breaks = [(m.get('number'), m.find('print').attrib) for m in root.findall('./part/measure')]
        self.assertEqual(breaks, [('1', {}), ('2', {}), ('3', {'new-system': 'yes'}),
                                  ('4', {}), ('5', {}), ('6', {'new-page': 'yes'})])
        self.assertEqual([ET.tostring(n) for n in root.iter('note')], notes_before)
        self.assertEqual(root.find('.//staff-details').get('print-object'), 'yes')

    def test_single_system_is_still_locked_without_break_elements(self):
        source = preserve_source_layout(self.score(4), self.project([[4]]))
        self.assertTrue(source['preserved'])
        self.assertEqual(len(source['systems']), 1)

    def test_mxl_keeps_other_entries(self):
        path = self.score(4, compressed=True)
        source = preserve_source_layout(path, self.project([[2, 2]]))
        self.assertTrue(source['preserved'])
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.read('extra.txt'), b'keep me')
        self.assertEqual(read_musicxml(path).findall('./part/measure')[2].find('print').get('new-system'), 'yes')

    def test_missing_measure_does_not_shift_later_systems(self):
        path = self.score(6, missing=2)
        before = path.read_bytes()
        source = preserve_source_layout(path, self.project([[2, 3], [1]]))
        self.assertFalse(source['preserved'])
        self.assertIn('missing or extra measures', source['warnings'][0])
        self.assertEqual(path.read_bytes(), before)

    def test_number_mismatch_is_not_guessed_from_total(self):
        path = self.score(4)
        path.write_text(path.read_text().replace('number="4"', 'number="5"'))
        self.assertFalse(preserve_source_layout(path, self.project([[2, 2]]))['preserved'])

    def test_variable_visible_staves_are_not_silently_expanded(self):
        path = self.score(4)
        before = path.read_bytes()
        self.assertFalse(preserve_source_layout(path, self.project([[2, 2]], counts=[2, 1]))['preserved'])
        self.assertEqual(path.read_bytes(), before)

    def test_compact_multiple_rest_gets_slots_before_following_notes(self):
        path = self.score(9)
        root = read_musicxml(path)
        measure = root.find("./part/measure[@number='2']")
        attributes = ET.Element('attributes')
        style = ET.SubElement(attributes, 'measure-style')
        ET.SubElement(style, 'multiple-rest').text = '8'
        measure.insert(0, attributes)
        for note in measure.findall('note'):
            measure.remove(note)
        path.write_bytes(ET.tostring(root))
        pitches_before = [ET.tostring(n.find('pitch')) for n in root.iter('note')
                          if n.find('pitch') is not None]

        warnings = normalize_compact_multiple_rests(path)

        repaired = read_musicxml(path)
        self.assertEqual(len(warnings), 1)
        self.assertIn('measure 2', warnings[0])
        self.assertEqual(repaired.findtext('.//multiple-rest'), '8')
        self.assertEqual([ET.tostring(n.find('pitch')) for n in repaired.iter('note')
                          if n.find('pitch') is not None], pitches_before)
        measures = repaired.findall('./part/measure')
        self.assertEqual([m.get('number') for m in measures], [str(n) for n in range(1, 17)])
        self.assertFalse(any(m.findall('note/pitch') for m in measures[1:9]))
        self.assertTrue(all(m.find("note/rest[@measure='yes']") is not None for m in measures[1:9]))
        self.assertTrue(measures[9].findall('note/pitch'))

    def test_genuine_multiple_rest_is_preserved(self):
        path = self.score(9)
        root = read_musicxml(path)
        measure = root.find("./part/measure[@number='2']")
        attributes = ET.Element('attributes')
        style = ET.SubElement(attributes, 'measure-style')
        ET.SubElement(style, 'multiple-rest').text = '8'
        measure.insert(0, attributes)
        for later in root.findall('./part/measure')[1:9]:
            for note in later.findall('note'):
                pitch = note.find('pitch')
                if pitch is not None:
                    note.remove(pitch)
                    note.insert(0, ET.Element('rest'))
        path.write_bytes(ET.tostring(root))

        self.assertEqual(normalize_compact_multiple_rests(path), [])
        self.assertEqual(read_musicxml(path).findtext('.//multiple-rest'), '8')

    def test_measure_numbers_continue_at_explicit_page_break(self):
        path = self.score(6)
        root = read_musicxml(path)
        measures = root.findall('./part/measure')
        for measure, number in zip(measures[3:], (1, 2, 3)):
            measure.set('number', str(number))
        measures[3].find('print').set('new-page', 'yes')
        path.write_bytes(ET.tostring(root))

        warnings = continue_measure_numbers_across_pages(path)

        self.assertEqual(len(warnings), 1)
        self.assertEqual(
            [m.get('number') for m in read_musicxml(path).findall('./part/measure')],
            ['1', '2', '3', '4', '5', '6'],
        )

    def test_duplicate_within_page_remains_visible_for_review(self):
        path = self.score(4)
        root = read_musicxml(path)
        root.findall('./part/measure')[2].set('number', '2')
        path.write_bytes(ET.tostring(root))
        before = path.read_bytes()

        self.assertEqual(continue_measure_numbers_across_pages(path), [])
        self.assertEqual(path.read_bytes(), before)

    @unittest.skipIf(verovio is None, 'Optional real Verovio engraving regression')
    def test_engraving_keeps_pages_systems_and_empty_staves(self):
        path = self.score(6)
        preserve_source_layout(path, self.project([[2, 3], [1]]))
        toolkit = verovio.toolkit()
        toolkit.setOptions({'pageWidth': 2100, 'pageHeight': 2625, 'scale': 100,
                            'breaks': 'encoded', 'condense': 'none', 'adjustPageHeight': True,
                            'breaksNoWidow': False, 'systemMaxPerPage': 0})
        self.assertTrue(toolkit.loadFile(str(path)))
        self.assertEqual(toolkit.getPageCount(), 2)
        ns = {'s': 'http://www.w3.org/2000/svg'}
        page_systems = []
        for number in range(1, 3):
            root = ET.fromstring(toolkit.renderToSVG(number))
            systems = root.findall('.//s:g[@class="system"]', ns)
            page_systems.append([len(system.findall('s:g[@class="measure"]', ns)) for system in systems])
            for system in systems:
                for measure in system.findall('s:g[@class="measure"]', ns):
                    self.assertEqual(len(measure.findall('s:g[@class="staff"]', ns)), 2)
        self.assertEqual(page_systems, [[2, 3], [1]])


if __name__ == '__main__':
    unittest.main()
