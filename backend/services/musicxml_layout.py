"""Parse MusicXML to extract layout information (e.g. measures per system)."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import collections


def get_measures_per_first_system(musicxml_path: Path) -> Optional[int]:
    """
    Parse MusicXML and return the number of measures on the first system.

    Uses music21's layout module. Returns None if layout cannot be determined.
    """
    result = _get_system_measure_counts(musicxml_path)
    if not result:
        return None
    return result[0]  # first system


def get_measures_per_system_for_layout(musicxml_path: Path) -> Optional[int]:
    """
    Return the typical measures-per-line for layout matching the original sheet.

    Prefers the most common system size (e.g. 4) over the first system which may
    have 1 measure (pickup/anacrusis). Use this for proportional Verovio layout.
    """
    counts = _get_all_system_measure_counts(musicxml_path)
    if not counts:
        return None
    # Use most common count (typical system), but ignore single-measure systems
    # which are often pickups
    filtered = [c for c in counts if c > 1 and c <= 12]
    if filtered:
        most_common = collections.Counter(filtered).most_common(1)[0][0]
        return most_common
    # Fallback: first system or first count > 1
    for c in counts:
        if 1 < c <= 12:
            return c
    return counts[0] if counts[0] <= 12 else None


def _get_system_measure_counts(musicxml_path: Path) -> Optional[list]:
    """Return list of measure counts per system, or None."""
    path = Path(musicxml_path)
    if not path.exists():
        return None

    try:
        from music21 import converter, layout

        score = converter.parse(str(path))
        if not score.parts:
            return None

        system_regions = layout.getSystemRegionMeasureNumbers(score)
        if not system_regions:
            return None

        return [end - start + 1 for start, end in system_regions]
    except Exception:
        return None


def _get_all_system_measure_counts(musicxml_path: Path) -> list:
    """Return list of measure counts per system (empty if parse fails)."""
    result = _get_system_measure_counts(musicxml_path)
    return result if result else []


def get_system_regions(musicxml_path: Path) -> List[Tuple[int, int]]:
    """
    Return (startMeasure, endMeasure) 1-based for each system.
    Used for PDF overlay: map measure index to system and position within system.
    """
    path = Path(musicxml_path)
    if not path.exists():
        return []

    try:
        from music21 import converter, layout

        score = converter.parse(str(path))
        if not score.parts:
            return []

        regions = layout.getSystemRegionMeasureNumbers(score)
        return list(regions) if regions else []
    except Exception:
        return []


def get_system_time_ranges(
    musicxml_path: Path, bpm: float = 120.0
) -> List[Tuple[float, float]]:
    """
    Return time ranges (start_sec, end_sec) for each system (line of music).
    Used for PDF overlay: systems map to vertical positions on the page.
    """
    path = Path(musicxml_path)
    if not path.exists():
        return []

    try:
        from music21 import converter, layout

        score = converter.parse(str(path))
        if not score.parts:
            return []

        system_regions = layout.getSystemRegionMeasureNumbers(score)
        if not system_regions:
            return []

        # Get measure boundaries (0-indexed)
        boundaries = get_measure_boundaries(path, bpm)
        if not boundaries:
            return []

        result = []
        for start_m, end_m in system_regions:
            # music21 uses 1-based measure numbers
            i_start = start_m - 1
            i_end = end_m - 1
            if i_start < 0 or i_end >= len(boundaries):
                continue
            t_start = boundaries[i_start][0]
            t_end = boundaries[i_end][1]
            result.append((t_start, t_end))
        return result
    except Exception:
        return []


def get_measure_boundaries(musicxml_path: Path, bpm: float = 120.0) -> List[Tuple[float, float]]:
    """
    Return measure boundaries as (start_sec, end_sec) for each measure.
    Uses quarter-note offsets from music21; converts to seconds via bpm.
    """
    path = Path(musicxml_path)
    if not path.exists():
        return []

    try:
        from music21 import converter, tempo

        score = converter.parse(str(path))
        if not score.parts:
            return []

        # Get BPM from first MetronomeMark if present
        for el in score.recurse():
            if isinstance(el, tempo.MetronomeMark):
                bpm = el.getQuarterBPM() or 120.0
                break
        else:
            bpm = 120.0

        # Get all measures with their quarter-note offsets (first part only)
        measures = []
        for m in score.parts[0].getElementsByClass("Measure"):
            offset = m.offset
            ql = m.quarterLength
            measures.append((offset, offset + ql))

        if not measures:
            return []

        # Sort by offset
        measures.sort(key=lambda x: x[0])

        # Convert quarter notes to seconds: 1 beat = 60/bpm seconds
        sec_per_beat = 60.0 / bpm
        return [(s * sec_per_beat, e * sec_per_beat) for s, e in measures]
    except Exception:
        return []


def get_playback_time_map(musicxml_path: Path) -> List[Dict[str, float]]:
    """Map expanded playback seconds back onto the written score timeline.

    MIDI contains repeated measures as ordinary sequential events, while the
    displayed MusicXML retains repeat and volta marks.  Each returned segment
    identifies where a performed measure lives in the written score so clients
    can visibly jump back at repeats.
    """
    path = Path(musicxml_path)
    if not path.exists():
        return []

    try:
        from music21 import converter, stream

        score = converter.parse(str(path))
        if not score.parts:
            return []
        written_part = score.parts[0]
        written_measures = list(written_part.getElementsByClass(stream.Measure))
        if not written_measures:
            return []

        # Editorial metadata survives music21's repeat-expansion deep copies,
        # avoiding ambiguity when recognized scores contain duplicate labels.
        for index, measure in enumerate(written_measures):
            measure.editorial.playbackSourceIndex = index
        try:
            playback_score = score.expandRepeats()
        except Exception:
            playback_score = score
        playback_part = playback_score.parts[0]
        playback_measures = list(playback_part.getElementsByClass(stream.Measure))

        def timings(part):
            return {
                id(item["element"]): item
                for item in part.secondsMap
                if isinstance(item["element"], stream.Measure)
            }

        written_times = timings(written_part)
        playback_times = timings(playback_part)
        result = []
        for playback_measure in playback_measures:
            source_index = getattr(playback_measure.editorial, "playbackSourceIndex", None)
            if source_index is None or not 0 <= source_index < len(written_measures):
                continue
            source = written_times.get(id(written_measures[source_index]))
            performed = playback_times.get(id(playback_measure))
            if not source or not performed:
                continue
            result.append({
                "playback_start": float(performed["offsetSeconds"]),
                "playback_end": float(performed["endTimeSeconds"]),
                "score_start": float(source["offsetSeconds"]),
                "score_end": float(source["endTimeSeconds"]),
                "measure_index": source_index,
            })
        return result
    except Exception:
        return []


def get_measure_layout_positions(musicxml_path: Path) -> List[Dict[str, Any]]:
    """
    Return layout positions for each measure using music21 divideByPages.
    Each entry: { "page": 0-based page, "top": 0-1, "left": 0-1, "bottom": 0-1, "right": 0-1 }
    Coordinates are ratios of page dimensions for scaling to PDF canvas.
    Returns [] if layout cannot be computed.
    """
    path = Path(musicxml_path)
    if not path.exists():
        return []

    try:
        from music21 import converter, layout, stream

        score = converter.parse(str(path))
        if not score.parts:
            return []

        layout_score = layout.divideByPages(score, fastMeasures=True)
        if not layout_score or not layout_score.pages:
            return []

        first_part = score.parts[0]
        measures = list(first_part.getElementsByClass(stream.Measure))
        if not measures:
            return []

        result = []
        for m in measures:
            m_num = m.measureNumber
            if m_num is None:
                continue
            try:
                pos = layout_score.getPositionForStaffMeasure(0, m_num, returnFormat="float")
            except Exception:
                continue
            (top_r, left_r), (bottom_r, right_r) = pos[0], pos[1]
            page_id = pos[2]
            result.append({
                "page": page_id,
                "top": float(top_r),
                "left": float(left_r),
                "bottom": float(bottom_r),
                "right": float(right_r),
            })
        return result
    except Exception:
        return []
