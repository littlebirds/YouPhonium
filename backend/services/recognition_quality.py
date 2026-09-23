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
        repeat_starts = []
        backward_repeat_count = 0
        active_ending = None
        ending_numbers = set()
        first_ending_measure = None
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

            # Repeat signs and volta brackets form a small control-flow
            # language. Validate it separately from rhythmic completeness so
            # a score that renders cleanly cannot silently jump to the start.
            for barline in measure.findall("barline"):
                repeat = barline.find("repeat")
                if repeat is not None:
                    direction = repeat.get("direction")
                    if direction == "forward":
                        repeat_starts.append(number)
                    elif direction == "backward":
                        backward_repeat_count += 1
                        if repeat_starts:
                            repeat_starts.pop()
                        else:
                            issue(
                                "repeat_unmatched_backward", part_id, number,
                                f"Measure {number}: backward repeat has no preceding forward repeat; playback may return to the beginning.",
                            )

                ending = barline.find("ending")
                if ending is not None:
                    ending_type = ending.get("type", "")
                    ending_number = ending.get("number", "?")
                    if ending_type == "start":
                        first_ending_measure = first_ending_measure or number
                        if active_ending is not None:
                            issue(
                                "ending_overlap", part_id, number,
                                f"Measure {number}: ending {ending_number} starts before ending {active_ending[0]} is closed.",
                            )
                        active_ending = (ending_number, number)
                        ending_numbers.update(value.strip() for value in ending_number.split(",") if value.strip())
                    elif ending_type in ("stop", "discontinue"):
                        if active_ending is None:
                            issue(
                                "ending_unmatched_close", part_id, number,
                                f"Measure {number}: ending {ending_number} closes without a matching start bracket.",
                            )
                        else:
                            opened_number, _ = active_ending
                            if ending_number != opened_number:
                                issue(
                                    "ending_number_mismatch", part_id, number,
                                    f"Measure {number}: ending {ending_number} closes ending {opened_number}.",
                                )
                            active_ending = None
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

        for start_number in repeat_starts:
            issue(
                "repeat_unmatched_forward", part_id, start_number,
                f"Measure {start_number}: forward repeat has no later backward repeat.",
            )
        if active_ending is not None:
            ending_number, start_number = active_ending
            issue(
                "ending_unclosed", part_id, start_number,
                f"Measure {start_number}: ending {ending_number} bracket is not closed.",
            )
        if "2" in ending_numbers and "1" not in ending_numbers:
            first_measure = next(
                (m.get("number", "?") for m in measures
                 if any(e.get("number") == "2" and e.get("type") == "start"
                        for b in m.findall("barline") for e in b.findall("ending"))),
                "?",
            )
            issue(
                "ending_sequence", part_id, first_measure,
                f"Measure {first_measure}: second ending is present but no first ending was found.",
            )
        if ending_numbers and backward_repeat_count == 0:
            issue(
                "ending_without_repeat", part_id, first_ending_measure or "?",
                "Volta endings were found without a backward repeat marker; playback order may be incomplete.",
            )

    counts = Counter(item["code"] for item in issues)
    return {
        "validator_version": 2,
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
               "overfull_staff": 8, "incomplete_staff": 5, "duration_mismatch": 5,
               "repeat_unmatched_backward": 12, "repeat_unmatched_forward": 8,
               "ending_overlap": 8, "ending_unmatched_close": 8,
               "ending_number_mismatch": 8, "ending_unclosed": 8,
               "ending_sequence": 6, "ending_without_repeat": 8}
    return sum(weights.get(code, 1) * count for code, count in report["issue_counts"].items())
