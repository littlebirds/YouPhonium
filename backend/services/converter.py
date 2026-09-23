"""Convert MusicXML to MIDI using music21.

Playback priorities (see PLAN.md): Pitches and rhythm are essential; dynamics
and grace notes are optional. Conversion preserves the recognized rhythm;
structural problems are reported separately, never fixed by guessing durations.
"""

import copy
import io
import logging
import re
import tempfile
from pathlib import Path
from typing import List, Tuple

log = logging.getLogger(__name__)

# Lazy import: music21 takes 10–30s to load; defer until first conversion
_m21_cache = None

def _music21():
    global _m21_cache
    if _m21_cache is None:
        from music21 import converter, midi, note, chord
        _m21_cache = (converter, midi, note, chord)
    return _m21_cache



# Standard quarter lengths for common note types (helps fix OMR duration errors)
# 0.125=32nd, 0.25=16th, 0.5=eighth, 0.75=dotted eighth, 1.0=quarter, etc.
_STANDARD_DURATIONS = (0.125, 0.25, 0.375, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)


def _round_to_nearest_standard(ql: float) -> float:
    """Round quarter length to nearest standard note duration."""
    if ql <= 0:
        return 0.25
    best = _STANDARD_DURATIONS[0]
    for d in _STANDARD_DURATIONS:
        if abs(d - ql) < abs(best - ql):
            best = d
    if ql > _STANDARD_DURATIONS[-1]:
        return round(ql * 4) / 4  # Round to nearest quarter
    return best


def _score_duration_sequence(durations: List[float]) -> float:
    """
    Score a sequence of note durations. Higher is better.
    - Prefer standard durations (0.5, 1.0, etc.)
    - Prefer eighth-note pairs (common rhythm pattern)
    """
    score = 0.0
    for ql in durations:
        # Standard duration bonus
        if ql in _STANDARD_DURATIONS:
            score += 2.0
        else:
            best = min(_STANDARD_DURATIONS, key=lambda d: abs(d - ql))
            score += max(0, 1.0 - abs(best - ql))
    # Eighth-note pair bonus: consecutive 0.5s
    for i in range(len(durations) - 1):
        if durations[i] == 0.5 and durations[i + 1] == 0.5:
            score += 0.5
    return score


def _fix_overflow_measure(m, measure_ql: float, notes_in_measure: List, total_ql: float) -> None:
    """
    When a measure overflows (total_ql > measure_ql), the OMR reading is wrong.
    Try multiple correction hypotheses and apply the most probable one.
    - Hypothesis 1: Split K quarters into eighths (OMR misread eighths as quarters)
    - Hypothesis 2: Scale down (fallback; produces non-standard durations)
    """
    notes_with_offset = [(n, n.getOffsetBySite(m)) for n in notes_in_measure]
    notes_with_offset.sort(key=lambda x: x[1])
    N = len(notes_in_measure)
    excess = total_ql - measure_ql

    candidates: List[Tuple[str, float, object]] = []

    # Hypothesis 1: Convert K quarters to eighths (halve duration)
    # OMR misread eighths as quarters. (N-K)*1.0 + K*0.5 = measure_ql => K = 2*(total_ql - measure_ql)
    if all(n.quarterLength == 1.0 for n in notes_in_measure):
        K = int(round(2 * excess))
        if 0 < K <= N:
            # Try different selections: first K, last K, middle K
            mid_start = max(0, (N - K) // 2)
            for name, indices in [
                ("quarters_to_eighths_first", list(range(K))),
                ("quarters_to_eighths_last", list(range(N - K, N))),
                ("quarters_to_eighths_middle", list(range(mid_start, mid_start + K))),
            ]:
                if len(indices) != K or any(i < 0 or i >= N for i in indices):
                    continue
                # Resulting durations: K eighths + (N-K) quarters
                durations = [0.5 if i in indices else 1.0 for i in range(N)]
                if abs(sum(durations) - measure_ql) > 0.01:
                    continue
                s = _score_duration_sequence(durations)
                candidates.append((name, s, ("halve", indices)))

    # Hypothesis 2: Scale down (fallback)
    scale = measure_ql / total_ql
    scaled_durations = [n.quarterLength * scale for n in notes_in_measure]
    scale_score = _score_duration_sequence(scaled_durations)
    candidates.append(("scale", scale_score, ("scale",)))

    if not candidates:
        return

    # Pick best by score
    best = max(candidates, key=lambda c: c[1])
    strategy = best[2]

    if strategy[0] == "scale":
        for n in notes_in_measure:
            n.duration.quarterLength = n.quarterLength * scale
    elif strategy[0] == "halve":
        indices_to_halve = set(strategy[1])
        for i in indices_to_halve:
            notes_in_measure[i].duration.quarterLength = 0.5


def _has_beam(n) -> bool:
    """True if note/chord has beam info from MusicXML (OMR recognized the beam)."""
    _, _, note, chord = _music21()
    if isinstance(n, note.Note):
        beams = getattr(n, "beams", None)
    elif isinstance(n, chord.Chord):
        beams = getattr(n, "beams", None)
        if beams is None and n.notes:
            beams = getattr(n.notes[0], "beams", None)
    else:
        return False
    return beams is not None and len(beams) > 0


def _fix_measure_full_of_quarters(m, measure_ql: float, notes_in_measure: list) -> None:
    """
    Measure has exactly N quarters filling a 4/4 (or similar) measure.
    Cases:
    - 4 beamed eighths misread as 4 quarters (often with 2 quarters dropped after):
      halve each -> 4 eighths. Use beam info when present; else use N=4 heuristic.
    - 8 eighths misread as 4 quarters (no beams): split each -> 8 eighths.
    """
    notes_with_offset = [(n, n.getOffsetBySite(m)) for n in notes_in_measure]
    notes_with_offset.sort(key=lambda x: x[1])
    N = len(notes_in_measure)

    # If beams present on any note: beamed quarters are eighths.
    beamed_count = sum(1 for n in notes_in_measure if _has_beam(n))
    # Also: 4 quarters in 4/4 with no beam info often = 4 beamed eighths (Audiveris
    # may not export beams; the 2 quarters after were dropped). Prefer halve for N=4.
    use_halve = beamed_count >= N // 2 or (N == 4 and measure_ql == 4.0)

    if use_halve:
        # Halve all: 4 quarters -> 4 eighths
        for n in notes_in_measure:
            n.duration.quarterLength = 0.5
    else:
        # Assume 8 eighths misread as 4 quarters; split each
        for n, off in sorted(notes_with_offset, key=lambda x: -x[1]):
            n.duration.quarterLength = 0.5
            dup = copy.deepcopy(n)
            dup.duration.quarterLength = 0.5
            m.insert(off + 0.5, dup)


def _fix_false_augmentation_dots(score):
    """
    Remove augmentation dots that cause a measure to overflow its time signature.

    Strategy: for each overflowing measure, find dotted notes whose dot (the extra
    duration = ql / 3) accounts for the excess. Remove the dot from whichever note
    brings the measure closest to its capacity without going under.

    Only acts on overflowing measures — real dots in measures that fit are untouched.
    """
    _, _, note, chord = _music21()
    if hasattr(score, "flat"):
        measures = score.flat.getElementsByClass("Measure")
    else:
        measures = []
        for part in getattr(score, "parts", [score]):
            measures.extend(part.getElementsByClass("Measure"))

    for m in measures:
        ts = m.timeSignature
        if ts is None:
            continue
        measure_ql = ts.quarterLength
        notes_in_measure = [n for n in m.notes if isinstance(n, (note.Note, chord.Chord))]
        if not notes_in_measure:
            continue
        total_ql = sum(n.quarterLength for n in notes_in_measure)

        if total_ql <= measure_ql * 1.001:
            continue  # Measure fits fine; real dots are safe

        # Find notes that have augmentation dots
        dotted_notes = [n for n in notes_in_measure if getattr(n.duration, "dots", 0) > 0]
        if not dotted_notes:
            continue

        # Try removing one dot at a time; pick the removal that best resolves the overflow
        best_note = None
        best_diff = float("inf")
        for n in dotted_notes:
            # Removing one dot: undotted duration = ql * 2/3
            undotted_ql = n.quarterLength * 2.0 / 3.0
            dot_contribution = n.quarterLength - undotted_ql
            new_total = total_ql - dot_contribution
            # Only accept if it brings the measure within bounds
            if new_total <= measure_ql * 1.001:
                diff = abs(new_total - measure_ql)
                if diff < best_diff:
                    best_diff = diff
                    best_note = (n, undotted_ql)

        if best_note:
            n, undotted_ql = best_note
            old_ql = n.quarterLength
            n.duration.quarterLength = undotted_ql
            log.info(
                "[converter] Removed false augmentation dot in measure %s: %.3f -> %.3f ql",
                m.measureNumber, old_ql, undotted_ql,
            )


def _fix_eighth_as_quarter(score):
    """
    Fix OMR error where eighth notes are exported as quarter notes (duration doubled).
    When measure overflows: try multiple correction hypotheses, apply most probable.
    When measure has exactly N notes of 1.0 filling the measure: use beam info
    when available (beamed -> halve to eighths); else split each into two eighths.
    """
    _, _, note, chord = _music21()
    if hasattr(score, "flat"):
        measures = score.flat.getElementsByClass("Measure")
    else:
        measures = []
        for part in getattr(score, "parts", [score]):
            measures.extend(part.getElementsByClass("Measure"))
    for m in measures:
        ts = m.timeSignature
        if ts is None:
            continue
        measure_ql = ts.quarterLength
        notes_in_measure = [n for n in m.notes if isinstance(n, (note.Note, chord.Chord))]
        if not notes_in_measure:
            continue
        total_ql = sum(n.quarterLength for n in notes_in_measure)

        if total_ql > measure_ql * 1.001:
            _fix_overflow_measure(m, measure_ql, notes_in_measure, total_ql)
        elif (
            measure_ql >= 2.0
            and len(notes_in_measure) == int(measure_ql)
            and all(n.quarterLength == 1.0 for n in notes_in_measure)
            and abs(total_ql - measure_ql) < 0.01
        ):
            _fix_measure_full_of_quarters(m, measure_ql, notes_in_measure)


def _strip_musicxml_ids(xml_bytes: bytes) -> bytes:
    """Remove music21-generated id attributes (long numbers) from MusicXML."""
    # Strip id="..." attributes; music21 adds id="1234567890.123" etc.
    return re.sub(rb'\s+id="[^"]*"', b"", xml_bytes)


def _set_euphonium_header(score):
    """Set instrument/part name to Euphonium (OMR often outputs Piano or wrong instrument)."""
    try:
        from music21 import instrument
    except ImportError:
        return
    parts = getattr(score, "parts", None)
    if not parts:
        parts = [score]  # Single-part score
    for part in parts:
        inst = part.getInstrument()
        if inst is None:
            inst = instrument.Instrument()
            part.insert(0, inst)
        inst.partName = "Euphonium"
        inst.instrumentName = "Euphonium"
        inst.partAbbreviation = "Euph."
        inst.instrumentAbbreviation = "Euph."


def _normalize_durations(score):
    """Normalize note durations to fix quarter/eighth conversion errors from OMR."""
    _, _, note, chord = _music21()
    for el in score.recurse():
        if isinstance(el, (note.Note, chord.Chord)):
            ql = el.quarterLength
            normalized = _round_to_nearest_standard(ql)
            el.duration.quarterLength = normalized


def musicxml_to_midi(musicxml_path: Path) -> bytes:
    """
    Convert a MusicXML file (.xml or .mxl) to MIDI bytes.

    Args:
        musicxml_path: Path to the MusicXML file

    Returns:
        MIDI file as bytes

    Raises:
        ValueError: If conversion fails
    """
    m21_converter, m21_midi, note, chord = _music21()
    try:
        score = m21_converter.parse(str(musicxml_path))
    except Exception as e:
        raise ValueError(f"Failed to parse MusicXML: {e}") from e

    note_count = sum(1 for el in score.recurse() if isinstance(el, (note.Note, chord.Chord)))
    if note_count == 0:
        raise ValueError(
            "No notes found in the sheet music. The PDF may not have been recognized correctly. "
            "Try a different OMR engine (Audiveris, HOMR, oemer) or use a clearer, higher-resolution image."
        )

    # Keep tuplets, dotted notes, rests and simultaneous voices exactly as
    # encoded. Rounding/squeezing bars silently changed valid rhythms and made
    # backend playback disagree with the MusicXML shown by Verovio.

    # MIDI has no repeat/volta control-flow instructions. Expand the written
    # structure into its performed order before translating, otherwise a
    # backward repeat can be ignored or interpreted differently by clients.
    playback_score = score
    try:
        playback_score = score.expandRepeats()
    except Exception as exc:
        # Recognition validation reports malformed repeat structures. Retain
        # linear playback as a safe fallback instead of losing MIDI entirely.
        log.warning("[converter] Could not expand repeats; using written order: %s", exc)

    try:
        mf = m21_midi.translate.streamToMidiFile(playback_score)
        midi_stream = io.BytesIO()
        mf.openFileLike(midi_stream)
        mf.write()
        result = midi_stream.getvalue()
        mf.close()
        return result
    except Exception as e:
        raise ValueError(f"Failed to convert to MIDI: {e}") from e


def musicxml_to_normalized_musicxml(musicxml_path: Path) -> Tuple[bytes, str]:
    """
    Re-serialize MusicXML without speculative pitch, rhythm or part-name changes.
    Kept under its historical name for callers; this is not an OMR repair step.
    Always outputs uncompressed XML for compatibility.
    """
    m21_converter, _, _, _ = _music21()
    try:
        score = m21_converter.parse(str(musicxml_path))
    except Exception as e:
        raise ValueError(f"Failed to parse MusicXML: {e}") from e

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        score.write("musicxml", fp=str(tmp_path))
        musicxml_bytes = tmp_path.read_bytes()
        musicxml_bytes = _strip_musicxml_ids(musicxml_bytes)
        return musicxml_bytes, "xml"
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
