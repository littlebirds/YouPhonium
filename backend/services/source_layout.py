"""Preserve source page/system boundaries without changing recognized music."""

from pathlib import Path
from fractions import Fraction
import re
import xml.etree.ElementTree as ET
import zipfile

from .recognition_quality import read_musicxml


def read_source_layout(project: Path) -> dict:
    """Read physical systems/staves from Audiveris's source-image geometry."""
    systems, pages = [], []
    with zipfile.ZipFile(project) as archive:
        sheets = sorted((n for n in archive.namelist()
                         if re.fullmatch(r"sheet#\d+/sheet#\d+\.xml", n)),
                        key=lambda n: int(re.search(r"\d+", n).group()))
        for page_index, name in enumerate(sheets):
            sheet = ET.fromstring(archive.read(name))
            picture = sheet.find("picture")
            pages.append({"width": int(picture.get("width", "0")) if picture is not None else 0,
                          "height": int(picture.get("height", "0")) if picture is not None else 0})
            for system in sheet.findall("./page/system"):
                stacks = system.findall("stack")
                if not stacks:
                    continue
                staff_counts = [sum(s.tag in ("staff", "one-line-staff") for s in part)
                                for part in system.findall("part")]
                systems.append({"page": page_index, "measure_count": len(stacks),
                                "measure_numbers": [s.get("id", "") for s in stacks],
                                "part_staff_counts": staff_counts,
                                "staff_count": sum(staff_counts)})
    return {"origin": "source_geometry", "pages": pages, "systems": systems}


def _write_musicxml(path: Path, root: ET.Element) -> None:
    """Change the score entry only; retain other archive entries and metadata."""
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if path.suffix.lower() != ".mxl":
        path.write_bytes(data)
        return
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        score_name = next(e.get("full-path") for e in container.iter()
                          if e.tag.rsplit("}", 1)[-1] == "rootfile"
                          and ET.fromstring(archive.read(e.get("full-path"))).tag.rsplit("}", 1)[-1] == "score-partwise")
        entries = [(info, data if info.filename == score_name else archive.read(info))
                   for info in archive.infolist()]
    with zipfile.ZipFile(path, "w") as archive:
        for info, content in entries:
            archive.writestr(info, content)


def normalize_compact_multiple_rests(path: Path) -> list[str]:
    """Expand an OMR engine's compact multi-rest into MusicXML measure slots.

    Some OMR output represents an eight-bar rest as one measure carrying
    ``multiple-rest=8`` and puts the next sounding bar immediately after it.
    MusicXML renderers treat the next seven elements as part of the rest and
    hide them.  Insert those seven rest measures and renumber later measures so
    the multi-rest remains intact and the following music remains visible.
    """
    root = read_musicxml(path)
    warnings = []
    for part in root.findall("part"):
        measures = part.findall("measure")

        def rest_state(at: int):
            divisions, meter, staves = 1, Fraction(4), 1
            for prior in measures[:at + 1]:
                attributes = prior.find("attributes")
                if attributes is None:
                    continue
                divisions = int(attributes.findtext("divisions", str(divisions)))
                staves = int(attributes.findtext("staves", str(staves)))
                time = attributes.find("time")
                if time is not None and time.find("senza-misura") is None:
                    beats, units = time.findall("beats"), time.findall("beat-type")
                    if beats and len(beats) == len(units):
                        meter = sum(
                            (Fraction(sum(int(v) for v in beat.text.split("+")) * 4,
                                      int(unit.text)) for beat, unit in zip(beats, units)),
                            Fraction(0),
                        )
            duration = meter * divisions
            return (int(duration) if duration.denominator == 1 else None), staves

        def add_full_measure_rests(target: ET.Element, duration: int, staves: int) -> None:
            for staff in range(1, staves + 1):
                if staff > 1:
                    backup = ET.SubElement(target, "backup")
                    ET.SubElement(backup, "duration").text = str(duration)
                note = ET.SubElement(target, "note")
                ET.SubElement(note, "rest", measure="yes")
                ET.SubElement(note, "duration").text = str(duration)
                ET.SubElement(note, "voice").text = str(staff)
                ET.SubElement(note, "staff").text = str(staff)

        for index, measure in enumerate(measures):
            for marker in list(measure.findall("./attributes/measure-style/multiple-rest")):
                try:
                    count = int(marker.text or "")
                except ValueError:
                    continue
                if count <= 1:
                    continue
                # If the marked measure itself sounds, the display directive is
                # contradictory rather than a compact rest representation.
                if any(note.find("pitch") is not None or note.find("unpitched") is not None
                       for note in measure.findall("note")):
                    style = next((parent for parent in measure.findall("./attributes/measure-style")
                                  if marker in list(parent)), None)
                    if style is not None:
                        style.remove(marker)
                    warnings.append(
                        f"Removed a conflicting {count}-measure rest marking at measure "
                        f"{measure.get('number', str(index + 1))}."
                    )
                    continue

                following = measures[index + 1:index + count]
                compact = any(
                    note.find("pitch") is not None or note.find("unpitched") is not None
                    for later in following for note in later.findall("note")
                )
                if not compact:
                    continue
                number = measure.get("number", str(index + 1))
                if not number.isdigit():
                    continue
                duration, staves = rest_state(index)
                if duration is None:
                    continue

                start = int(number)
                for later in measures[index + 1:]:
                    later_number = later.get("number", "")
                    if later_number.isdigit():
                        later.set("number", str(int(later_number) + count - 1))

                insert_at = list(part).index(measure) + 1
                if not measure.findall("note"):
                    add_full_measure_rests(measure, duration, staves)
                for offset in range(1, count):
                    rest_measure = ET.Element("measure", number=str(start + offset))
                    add_full_measure_rests(rest_measure, duration, staves)
                    part.insert(insert_at + offset - 1, rest_measure)
                warnings.append(
                    f"Expanded the compact {count}-measure rest at measure {number}; "
                    f"following music now resumes at measure {start + count}."
                )
    if warnings:
        _write_musicxml(path, root)
    return warnings


def continue_measure_numbers_across_pages(path: Path) -> list[str]:
    """Continue page-local OMR measure numbers at explicit page breaks.

    Page-by-page recognition commonly starts every page at measure 1.  Only
    treat a non-increasing number as a reset when that measure explicitly starts
    a new page; repeated/out-of-order numbers within a page remain review issues.
    """
    root = read_musicxml(path)
    warnings = []
    changed = False
    for part in root.findall("part"):
        offset = 0
        previous = None
        for measure in part.findall("measure"):
            raw = measure.get("number", "")
            if not raw.isdigit():
                continue
            current = int(raw) + offset
            printed = measure.find("print")
            starts_page = printed is not None and printed.get("new-page") == "yes"
            if previous is not None and starts_page and current <= previous:
                old = int(raw)
                offset += previous + 1 - current
                current = old + offset
                warnings.append(
                    f"Continued measure numbering at a page break from {old} to {current}."
                )
            if offset:
                measure.set("number", str(current))
                changed = True
            previous = current
    if changed:
        _write_musicxml(path, root)
    return warnings


def preserve_source_layout(musicxml: Path, project: Path) -> dict:
    """Lock a copied export to source geometry, or explain why it cannot be mapped.

    Do not invent missing measures or guess where missing parts belong. The raw
    Audiveris project remains untouched. Only print/staff-visibility metadata in
    the MusicXML changes; notes, rests, voices and durations are left alone.
    """
    source = read_source_layout(project)
    source.update({"preserved": False, "warnings": []})
    systems = source["systems"]
    root = read_musicxml(musicxml)
    parts = root.findall("part")
    expected_numbers = [n for system in systems for n in system["measure_numbers"]]
    if not systems or not expected_numbers:
        source["warnings"].append("Source system layout could not be detected; layout matching is unavailable.")
        return source
    for part in parts:
        numbers = [m.get("number", "") for m in part.findall("measure")]
        if numbers != expected_numbers:
            source["warnings"].append(
                "Source measures and exported measures do not match. Source layout cannot be locked until missing or extra measures are corrected.")
            return source
    if not parts or any(len(s["part_staff_counts"]) != len(parts) for s in systems):
        source["warnings"].append("Source and exported parts do not match; staff layout needs review.")
        return source
    # A changing visible staff count needs explicit part mapping, not blind
    # condensation. Keep the original export and surface that limitation.
    staff_counts = systems[0]["part_staff_counts"]
    if any(s["part_staff_counts"] != staff_counts for s in systems) or not all(staff_counts):
        source["warnings"].append("Visible staves vary between source systems; automatic staff-layout locking is unavailable.")
        return source
    for part, count in zip(parts, staff_counts):
        if any(int(n.findtext("staff", "1")) > count for n in part.findall(".//note")):
            source["warnings"].append("Recognized notes refer to staves absent from the source layout; review required.")
            return source

    starts = {}
    offset = 0
    for index, system in enumerate(systems):
        starts[offset] = (index, system)
        offset += system["measure_count"]
    for part, count in zip(parts, staff_counts):
        for index, measure in enumerate(part.findall("measure")):
            for printed in measure.findall("print"):
                printed.attrib.pop("new-system", None)
                printed.attrib.pop("new-page", None)
            if index in starts:
                system_index, system = starts[index]
                printed = measure.find("print")
                if printed is None:
                    printed = ET.Element("print")
                    measure.insert(0, printed)
                if system_index:
                    previous_page = systems[system_index - 1]["page"]
                    printed.set("new-page" if previous_page != system["page"] else "new-system", "yes")
                attributes = measure.find("attributes")
                if attributes is None:
                    attributes = ET.Element("attributes")
                    measure.insert(list(measure).index(printed) + 1, attributes)
                staves = attributes.find("staves")
                if staves is None:
                    staves = ET.Element("staves")
                    # MusicXML orders staves after time and before clef.
                    at = next((i for i, child in enumerate(attributes)
                               if child.tag not in ("footnote", "level", "divisions", "key", "time")), len(attributes))
                    attributes.insert(at, staves)
                staves.text = str(count)
            # An empty but present staff must remain visible, not disappear.
            for details in measure.findall("./attributes/staff-details"):
                details.set("print-object", "yes")
    _write_musicxml(musicxml, root)
    source["preserved"] = True
    return source
