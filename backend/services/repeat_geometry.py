"""Conservative repeat and volta recovery from scanned score geometry."""

from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess
from pathlib import Path
from statistics import median
from typing import Any, Optional

import numpy as np

from .musicxml_editor import apply_marker_edits
from .recognition_quality import read_musicxml


@dataclass
class ScoreSystem:
    page: int
    measures: list[str]


@dataclass
class StaffGeometry:
    top: int
    spacing: float
    strength: float

    @property
    def bottom(self) -> int:
        return round(self.top + 4 * self.spacing)


@dataclass
class VerticalStroke:
    left: int
    right: int
    height: int

    @property
    def x(self) -> float:
        return (self.left + self.right) / 2


def normalize_ending_label(text: str) -> Optional[str]:
    """Normalize OCR text such as ``1, 2.`` or ``1-3`` for MusicXML."""
    cleaned = (text or "").replace("–", "-").replace("—", "-")
    cleaned = cleaned.replace(";", ",").replace("&", ",")
    cleaned = re.sub(r"(?i)ending|volta", "", cleaned)
    values: list[int] = []
    for start, end in re.findall(r"(\d+)\s*-\s*(\d+)", cleaned):
        lo, hi = int(start), int(end)
        if 0 < lo <= hi <= 12:
            values.extend(range(lo, hi + 1))
    cleaned = re.sub(r"\d+\s*-\s*\d+", " ", cleaned)
    for value in re.findall(r"\d+", cleaned):
        number = int(value)
        if 0 < number <= 12:
            values.append(number)
    unique = sorted(set(values))
    return ",".join(str(value) for value in unique) if unique else None


def parse_repeat_times(text: str) -> Optional[int]:
    """Return an explicit repeat count from ``3x`` or ``play 3 times`` text."""
    cleaned = (text or "").replace("×", "x")
    match = re.search(r"(?i)\b(\d{1,2})\s*x\b", cleaned)
    if not match:
        match = re.search(r"(?i)\b(?:play\s*)?(\d{1,2})\s*times?\b", cleaned)
    if not match:
        return None
    value = int(match.group(1))
    return value if 2 < value <= 12 else None


def _score_systems(path: Path) -> list[ScoreSystem]:
    root = read_musicxml(path)
    first_part = root.find("part")
    if first_part is None:
        return []
    systems: list[ScoreSystem] = []
    page = 0
    current: list[str] = []
    for index, measure in enumerate(first_part.findall("measure")):
        printed = measure.find("print")
        new_page = printed is not None and printed.get("new-page") == "yes"
        new_system = printed is not None and printed.get("new-system") == "yes"
        if current and (new_page or new_system):
            systems.append(ScoreSystem(page, current))
            current = []
            if new_page:
                page += 1
        current.append(measure.get("number", str(index + 1)))
    if current:
        systems.append(ScoreSystem(page, current))
    return systems


def _load_gray(path: Path, max_width: int = 3500):
    import cv2

    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return None
    if gray.shape[1] > max_width:
        scale = max_width / gray.shape[1]
        gray = cv2.resize(gray, (max_width, round(gray.shape[0] * scale)),
                          interpolation=cv2.INTER_AREA)
    return gray


def _staff_geometries(gray, expected: int) -> list[StaffGeometry]:
    """Find the strongest non-overlapping five-line combs on one page."""
    import cv2

    if expected <= 0:
        return []
    height, width = gray.shape
    binary = cv2.threshold(gray, 190, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, width // 60), 1)),
    )
    strength = (horizontal > 0).mean(axis=1)
    min_spacing = max(7, round(width / 220))
    max_spacing = max(min_spacing + 1, round(width / 55))
    candidates: list[StaffGeometry] = []
    for spacing in range(min_spacing, max_spacing + 1):
        margin = max(3, round(spacing * 0.14))
        for top in range(10, height - 4 * spacing - 10):
            score = sum(
                float(np.max(strength[max(0, top + line * spacing - margin):
                                      min(height, top + line * spacing + margin + 1)]))
                for line in range(5)
            ) / 5
            if score >= 0.055:
                candidates.append(StaffGeometry(top, float(spacing), score))
    candidates.sort(key=lambda item: item.strength, reverse=True)
    chosen: list[StaffGeometry] = []
    separation = height / max(2.2 * expected, 1)
    for candidate in candidates:
        center = candidate.top + 2 * candidate.spacing
        if all(abs(center - (item.top + 2 * item.spacing)) > separation for item in chosen):
            chosen.append(candidate)
            if len(chosen) == expected:
                break
    return sorted(chosen, key=lambda item: item.top)


def _vertical_strokes(gray, staff: StaffGeometry) -> list[VerticalStroke]:
    import cv2

    spacing = staff.spacing
    page_width = gray.shape[1]
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 101, 12,
    )
    y0 = max(0, round(staff.top - 0.25 * spacing))
    y1 = min(gray.shape[0], round(staff.bottom + 0.25 * spacing))
    crop = binary[y0:y1]
    vertical = cv2.morphologyEx(
        crop, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(3, round(3.0 * spacing)))),
    )
    contours, _ = cv2.findContours(vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw = []
    for contour in contours:
        x, _, width, height = cv2.boundingRect(contour)
        if height >= 2.9 * spacing and width <= 1.25 * spacing:
            if 0.035 * page_width < x < 0.965 * page_width:
                raw.append(VerticalStroke(x, x + width - 1, height))
    raw.sort(key=lambda item: item.left)
    grouped: list[list[VerticalStroke]] = []
    for stroke in raw:
        if not grouped or stroke.left - grouped[-1][-1].right > 1.6 * spacing:
            grouped.append([stroke])
        else:
            grouped[-1].append(stroke)
    result = []
    for group in grouped:
        strongest = max(group, key=lambda item: item.height)
        result.append(VerticalStroke(
            min(item.left for item in group),
            max(item.right for item in group),
            strongest.height,
        ))
    return result


def _measure_boundaries(strokes: list[VerticalStroke], count: int,
                        spacing: float) -> tuple[list[VerticalStroke], Optional[float]]:
    """Choose the most barline-like strokes for the expected measure count."""
    if count <= 0 or not strokes:
        return [], None
    end = strokes[-1]
    interior = [item for item in strokes[:-1] if item.right < end.left - 1.4 * spacing]
    selected = sorted(
        sorted(interior, key=lambda item: item.height, reverse=True)[:max(0, count - 1)],
        key=lambda item: item.x,
    )
    if len(selected) != max(0, count - 1):
        return [], None
    selected.append(end)
    distances = [selected[i + 1].x - selected[i].x for i in range(len(selected) - 1)]
    typical = median(distances) if distances else max(6 * spacing, end.x * 0.15)
    left = max(0.0, selected[0].x - typical)
    return selected, left


def _has_repeat_dots(gray, staff: StaffGeometry, stroke: VerticalStroke,
                     side: str) -> bool:
    import cv2

    spacing = staff.spacing
    if side == "right":
        x0, x1 = stroke.right + round(0.08 * spacing), stroke.right + round(1.8 * spacing)
    else:
        x0, x1 = stroke.left - round(1.8 * spacing), stroke.left - round(0.08 * spacing)
    x0, x1 = max(0, x0), min(gray.shape[1], x1)
    y0, y1 = max(0, staff.top), min(gray.shape[0], staff.bottom + 1)
    if x1 <= x0 or y1 <= y0:
        return False
    crop = cv2.threshold(gray[y0:y1, x0:x1], 180, 255, cv2.THRESH_BINARY_INV)[1]
    count, _, stats, centers = cv2.connectedComponentsWithStats(crop, 8)
    dots = []
    for index in range(1, count):
        _, _, width, height, area = stats[index]
        cx, cy = centers[index]
        if (0.15 * spacing <= width <= 0.72 * spacing
                and 0.15 * spacing <= height <= 0.72 * spacing
                and 0.035 * spacing * spacing <= area <= 0.6 * spacing * spacing
                and 0.65 * spacing <= cy <= 3.35 * spacing):
            dots.append((cx, cy))
    return any(
        abs(first[0] - second[0]) <= 0.5 * spacing
        and 0.65 * spacing <= abs(first[1] - second[1]) <= 1.35 * spacing
        for i, first in enumerate(dots) for second in dots[i + 1:]
    )


def _ocr_text(gray_crop, whitelist: str = "0123456789,.-xX") -> str:
    import cv2

    executable = shutil.which("tesseract")
    if executable is None or gray_crop is None or not gray_crop.size:
        return ""
    enlarged = cv2.resize(gray_crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    prepared = cv2.threshold(enlarged, 190, 255, cv2.THRESH_BINARY)[1]
    ok, encoded = cv2.imencode(".png", prepared)
    if not ok:
        return ""
    try:
        result = subprocess.run(
            [executable, "stdin", "stdout", "--psm", "7",
             "-c", f"tessedit_char_whitelist={whitelist}"],
            input=encoded.tobytes(), capture_output=True, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.decode("utf-8", errors="ignore").strip()


def _repeat_times_near(gray, staff: StaffGeometry, x: float) -> Optional[int]:
    spacing = staff.spacing
    x0, x1 = max(0, round(x - 5 * spacing)), min(gray.shape[1], round(x + 5 * spacing))
    y0, y1 = max(0, round(staff.top - 4 * spacing)), max(0, round(staff.top - 0.2 * spacing))
    return parse_repeat_times(_ocr_text(gray[y0:y1, x0:x1], "0123456789xXtimesplay"))


def _has_end_hook(gray, x: int, y: int, spacing: float) -> bool:
    import cv2

    x0, x1 = max(0, round(x - 0.45 * spacing)), min(gray.shape[1], round(x + 0.45 * spacing))
    y0, y1 = max(0, y), min(gray.shape[0], round(y + 2.5 * spacing))
    binary = cv2.threshold(gray[y0:y1, x0:x1], 190, 255, cv2.THRESH_BINARY_INV)[1]
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(2, round(0.75 * spacing)))),
    )
    contours, _ = cv2.findContours(vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.boundingRect(contour)[3] >= 0.75 * spacing for contour in contours)


def _ending_brackets(gray, staff: StaffGeometry, boundary_x: list[float]) -> list[dict[str, Any]]:
    import cv2

    if len(boundary_x) < 2:
        return []
    spacing = staff.spacing
    average_measure = median(
        boundary_x[i + 1] - boundary_x[i] for i in range(len(boundary_x) - 1)
    )
    y0 = max(0, round(staff.top - 5.0 * spacing))
    y1 = max(y0 + 1, round(staff.top - 0.3 * spacing))
    crop = gray[y0:y1]
    binary = cv2.adaptiveThreshold(
        crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 101, 10,
    )
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, round(0.22 * average_measure)), 1)),
    )
    contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, local_y, width, height = cv2.boundingRect(contour)
        if width < 0.42 * average_measure or height > 0.7 * spacing:
            continue
        absolute_y = y0 + local_y
        candidates.append((x, absolute_y, width, height))
    candidates.sort(key=lambda item: (item[1], item[0]))
    brackets = []
    for x, y, width, _ in candidates:
        end_x = x + width
        start_index = min(range(len(boundary_x)), key=lambda i: abs(boundary_x[i] - x))
        end_index = min(range(len(boundary_x)), key=lambda i: abs(boundary_x[i] - end_x))
        tolerance = max(2 * spacing, 0.18 * average_measure)
        if (abs(boundary_x[start_index] - x) > tolerance
                or abs(boundary_x[end_index] - end_x) > tolerance
                or end_index <= start_index):
            continue
        label_crop = gray[
            max(0, round(y + 0.25 * spacing)):min(gray.shape[0], round(y + 3.0 * spacing)),
            max(0, round(x + 0.25 * spacing)):min(gray.shape[1], round(x + 4.5 * spacing)),
        ]
        label = normalize_ending_label(_ocr_text(label_crop))
        if not label:
            continue
        brackets.append({
            "start_index": start_index,
            "end_index": end_index - 1,
            "number": label,
            "close": "stop" if _has_end_hook(gray, end_x, y, spacing) else "discontinue",
            "x": x,
            "end_x": end_x,
        })
    return brackets


def recognize_repeat_geometry(musicxml_path: Path, page_images: list[Path]) -> dict[str, Any]:
    """Augment MusicXML with high-confidence repeat and ending geometry."""
    systems = _score_systems(musicxml_path)
    by_page: dict[int, list[ScoreSystem]] = {}
    for system in systems:
        by_page.setdefault(system.page, []).append(system)
    edits: list[dict[str, Any]] = []
    detections: list[dict[str, Any]] = []
    warnings: list[str] = []
    if shutil.which("tesseract") is None:
        warnings.append(
            "Tesseract is unavailable; repeat signs can be recovered, but volta labels are left unchanged."
        )

    for page_index, image_path in enumerate(page_images):
        page_systems = by_page.get(page_index, [])
        if not page_systems:
            continue
        gray = _load_gray(image_path)
        if gray is None:
            warnings.append(f"Repeat geometry could not read source page {page_index + 1}.")
            continue
        staffs = _staff_geometries(gray, len(page_systems))
        if len(staffs) != len(page_systems):
            warnings.append(
                f"Repeat geometry found {len(staffs)} of {len(page_systems)} staff systems "
                f"on source page {page_index + 1}; that page was not modified."
            )
            continue

        for system, staff in zip(page_systems, staffs):
            strokes = _vertical_strokes(gray, staff)
            selected, left = _measure_boundaries(strokes, len(system.measures), staff.spacing)
            if not selected or left is None:
                continue
            boundary_x = [left] + [item.x for item in selected]
            system_backward: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for boundary_index, stroke in enumerate(selected, start=1):
                if boundary_index < len(system.measures) and _has_repeat_dots(gray, staff, stroke, "right"):
                    measure = system.measures[boundary_index]
                    edits.append({"measure": measure, "action": "add_forward_repeat"})
                    detections.append({"type": "forward_repeat", "measure": measure,
                                       "page": page_index + 1})
                if _has_repeat_dots(gray, staff, stroke, "left"):
                    measure = system.measures[boundary_index - 1]
                    repeat_times = _repeat_times_near(gray, staff, stroke.x)
                    edit: dict[str, Any] = {"measure": measure, "action": "add_backward_repeat"}
                    if repeat_times:
                        edit["repeat_times"] = repeat_times
                    edits.append(edit)
                    detection: dict[str, Any] = {"type": "backward_repeat", "measure": measure,
                                                 "page": page_index + 1}
                    if repeat_times:
                        detection["times"] = repeat_times
                    detections.append(detection)
                    system_backward.append((edit, detection))

            brackets = _ending_brackets(gray, staff, boundary_x)
            for bracket in brackets:
                start_measure = system.measures[bracket["start_index"]]
                end_measure = system.measures[bracket["end_index"]]
                number = bracket["number"]
                edits.extend([
                    {"measure": start_measure, "action": "add_ending_start",
                     "ending_number": number},
                    {"measure": end_measure, "action": f"add_ending_{bracket['close']}",
                     "ending_number": number},
                ])
                detections.append({
                    "type": "ending", "start_measure": start_measure,
                    "end_measure": end_measure, "passes": number,
                    "close": bracket["close"], "page": page_index + 1,
                })
            ending_passes = [
                int(value)
                for bracket in brackets
                for value in bracket["number"].split(",")
                if value.isdigit()
            ]
            # Multiple endings are themselves an explicit performance count.
            # Infer only when one backward repeat makes the association clear;
            # printed "3x" text, when present, already takes precedence.
            if len(system_backward) == 1 and ending_passes and max(ending_passes) > 2:
                edit, detection = system_backward[0]
                if "repeat_times" not in edit:
                    edit["repeat_times"] = max(ending_passes)
                    detection["times"] = max(ending_passes)

    all_passes = [
        int(value)
        for detection in detections if detection.get("type") == "ending"
        for value in str(detection.get("passes", "")).split(",")
        if value.isdigit()
    ]
    backward_edits = [edit for edit in edits if edit.get("action") == "add_backward_repeat"]
    if len(backward_edits) == 1 and all_passes and max(all_passes) > 2:
        backward_edits[0].setdefault("repeat_times", max(all_passes))
        for detection in detections:
            if (detection.get("type") == "backward_repeat"
                    and detection.get("measure") == backward_edits[0].get("measure")):
                detection.setdefault("times", max(all_passes))

    unique_edits = []
    seen = set()
    for edit in edits:
        key = tuple(sorted(edit.items()))
        if key not in seen:
            seen.add(key)
            unique_edits.append(edit)
    changed = 0
    if unique_edits:
        changed, _ = apply_marker_edits(musicxml_path, unique_edits, save=True)
    return {
        "repeat_geometry": {
            "detections": detections,
            "warnings": warnings,
            "edit_count": changed,
        }
    }
