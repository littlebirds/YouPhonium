"""Structural OMR checks, not an estimate of transcription accuracy.

These checks deliberately never repair pitches or rhythms by guessing. A valid
bar can still contain wrong notes; warnings are a review aid, not certification.
"""

from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


def read_musicxml(path: Path) -> ET.Element:
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            names = [e.get("full-path") for e in container.iter()
                     if e.tag.rsplit("}", 1)[-1] == "rootfile"]
            roots = [ET.fromstring(archive.read(name)) for name in names if name]
            root = next(r for r in roots if r.tag.rsplit("}", 1)[-1] == "score-partwise")
    else:
        root = ET.parse(path).getroot()
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    if root.tag != "score-partwise":
        raise ValueError("Expected partwise MusicXML")
    return root


_TYPE_LENGTH = {
    "maxima": 32, "long": 16, "breve": 8, "whole": 4, "half": 2,
    "quarter": 1, "eighth": Fraction(1, 2), "16th": Fraction(1, 4),
    "32nd": Fraction(1, 8), "64th": Fraction(1, 16),
    "128th": Fraction(1, 32), "256th": Fraction(1, 64),
}


def analyze_musicxml(path: Path) -> dict:
    root = read_musicxml(path)
    issues = []
    note_count = 0
    measure_count = 0

    def issue(code, part, measure, message, staff=None):
        item = {"code": code, "part": part, "measure": measure, "message": message}
        if staff is not None:
            item["staff"] = staff
        if item not in issues:
            issues.append(item)

    for part in root.findall("part"):
        part_id = part.get("id", "")
        divisions, staves, meter = Fraction(1), 1, None
        measures = part.findall("measure")
        measure_count = max(measure_count, len(measures))
        previous = None
        for index, measure in enumerate(measures):
            number = measure.get("number", str(index + 1))
            if number.isdigit():
                current = int(number)
                if previous is not None and current > previous + 1:
                    issue("measure_gap", part_id, number,
                          f"Measure numbering jumps from {previous} to {current}; check for an omitted measure.")
                if previous is not None and current <= previous:
                    issue("measure_number", part_id, number,
                          f"Repeated or out-of-order measure number {number}.")
                previous = current
            cursor = last_start = Fraction(0)
            coverage = defaultdict(list)
            for element in measure:
                if element.tag == "attributes":
                    divisions = Fraction(element.findtext("divisions", str(divisions)))
                    staves = int(element.findtext("staves", str(staves)))
                    time = element.find("time")
                    if time is not None:
                        meter = None
                        if time.find("senza-misura") is None:
                            beats = time.findall("beats")
                            units = time.findall("beat-type")
                            if beats and len(beats) == len(units):
                                meter = sum(Fraction(sum(int(v) for v in b.text.split("+")) * 4,
                                                     int(u.text)) for b, u in zip(beats, units))
                elif element.tag in ("backup", "forward"):
                    duration = Fraction(element.findtext("duration", "0")) / divisions
                    cursor += duration * (-1 if element.tag == "backup" else 1)
                elif element.tag == "note":
                    if element.find("pitch") is not None:
                        note_count += 1
                    if element.find("grace") is not None:
                        continue
                    duration = Fraction(element.findtext("duration", "0")) / divisions
                    staff = int(element.findtext("staff", "1"))
                    start = last_start if element.find("chord") is not None else cursor
                    coverage[staff].append((start, start + duration))
                    if element.find("chord") is None:
                        last_start = cursor
                        cursor += duration
                    nominal = _TYPE_LENGTH.get(element.findtext("type"))
                    rest = element.find("rest")
                    if nominal is not None and not (rest is not None and rest.get("measure") == "yes"):
                        nominal = Fraction(nominal) * sum(Fraction(1, 2**i)
                                                         for i in range(len(element.findall("dot")) + 1))
                        modification = element.find("time-modification")
                        if modification is not None:
                            nominal *= Fraction(int(modification.findtext("normal-notes", "1")),
                                                int(modification.findtext("actual-notes", "1")))
                        if duration != nominal:
                            issue("duration_mismatch", part_id, number,
                                  f"Measure {number}, staff {staff}: note type and playback duration disagree.", staff)
            for staff in range(1, staves + 1):
                intervals = sorted(coverage[staff])
                if not intervals:
                    issue("empty_staff", part_id, number,
                          f"Measure {number}, staff {staff}: no notes or rests were exported.", staff)
                    continue
                if meter is None or measure.get("implicit") == "yes":
                    continue
                end = Fraction(0)
                covered = Fraction(0)
                for start, stop in intervals:
                    covered += max(Fraction(0), min(stop, meter) - max(start, end, 0))
                    end = max(end, stop)
                if end > meter:
                    issue("overfull_staff", part_id, number,
                          f"Measure {number}, staff {staff}: rhythm exceeds the time signature.", staff)
                # Short first/last bars can be complementary pickup bars.
                if covered < meter and index not in (0, len(measures) - 1):
                    issue("incomplete_staff", part_id, number,
                          f"Measure {number}, staff {staff}: rhythm has gaps; check rests and tuplets.", staff)

    counts = Counter(item["code"] for item in issues)
    return {
        "status": "needs_review" if issues else "unchecked",
        "measure_count": measure_count,
        "pitched_note_count": note_count,
        "has_encoded_breaks": any(p.get("new-system") == "yes" or p.get("new-page") == "yes"
                                  for p in root.iter("print")),
        "issues": issues,
        "issue_counts": dict(counts),
        "notice": "Automatic recognition is unverified. Compare notes, rests, accidentals and tuplets with the source.",
    }


def quality_cost(report: dict) -> int:
    """Prefer structural completeness, never raw note count or a claimed accuracy %."""
    weights = {"measure_gap": 30, "measure_number": 30, "empty_staff": 12,
               "overfull_staff": 8, "incomplete_staff": 5, "duration_mismatch": 5}
    return sum(weights.get(code, 1) * count for code, count in report["issue_counts"].items())
