"""Small, explicit MusicXML corrections for symbols OMR commonly misses."""

from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from services.recognition_quality import read_musicxml


REPEAT_ACTIONS = {
    "add_forward_repeat", "add_backward_repeat", "remove_forward_repeat",
    "remove_backward_repeat",
}
ENDING_ACTIONS = {
    "add_ending_start", "add_ending_stop", "add_ending_discontinue",
    "remove_endings",
}
EDIT_ACTIONS = REPEAT_ACTIONS | ENDING_ACTIONS


def _barline(measure: ET.Element, location: str) -> ET.Element:
    existing = next(
        (bar for bar in measure.findall("barline") if bar.get("location", "right") == location),
        None,
    )
    if existing is not None:
        return existing
    barline = ET.Element("barline", {"location": location})
    if location == "left":
        # Keep a left boundary before musical events but after layout and
        # attributes, which yields stable Verovio and Music21 imports.
        children = list(measure)
        index = next(
            (i for i, child in enumerate(children)
             if child.tag not in ("print", "attributes", "direction")),
            len(children),
        )
        measure.insert(index, barline)
    else:
        measure.append(barline)
    return barline


def _remove_empty_barline(measure: ET.Element, barline: ET.Element) -> None:
    if not list(barline) and not (barline.text or "").strip():
        measure.remove(barline)


def _edit_measure(measure: ET.Element, action: str, ending_number: str | None) -> bool:
    if action.startswith("add_forward"):
        location, direction = "left", "forward"
    elif action.startswith("add_backward"):
        location, direction = "right", "backward"
    else:
        location = direction = None

    if direction:
        barline = _barline(measure, location)
        repeat = barline.find("repeat")
        if repeat is not None and repeat.get("direction") == direction:
            return False
        if repeat is None:
            repeat = ET.SubElement(barline, "repeat")
        repeat.set("direction", direction)
        return True

    if action.startswith("remove_") and action.endswith("_repeat"):
        direction = "forward" if "forward" in action else "backward"
        changed = False
        for barline in list(measure.findall("barline")):
            for repeat in list(barline.findall("repeat")):
                if repeat.get("direction") == direction:
                    barline.remove(repeat)
                    changed = True
            _remove_empty_barline(measure, barline)
        return changed

    if action == "remove_endings":
        changed = False
        for barline in list(measure.findall("barline")):
            for ending in list(barline.findall("ending")):
                barline.remove(ending)
                changed = True
            _remove_empty_barline(measure, barline)
        return changed


    ending_type = action.removeprefix("add_ending_")
    location = "left" if ending_type == "start" else "right"
    barline = _barline(measure, location)
    ending = barline.find("ending")
    repeat = barline.find("repeat")
    reordered = False
    if ending is not None and repeat is not None:
        children = list(barline)
        if children.index(ending) > children.index(repeat):
            barline.remove(ending)
            barline.insert(list(barline).index(repeat), ending)
            reordered = True
    if ending is not None and ending.get("number") == ending_number and ending.get("type") == ending_type:
        return reordered
    if ending is None:
        ending = ET.Element("ending")
        repeat = barline.find("repeat")
        if repeat is None:
            barline.append(ending)
        else:
            barline.insert(list(barline).index(repeat), ending)
    ending.set("number", ending_number or "1")
    ending.set("type", ending_type)
    return True


def _serialize_musicxml(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _write_musicxml(path: Path, root: ET.Element) -> None:
    xml = _serialize_musicxml(root)
    if path.suffix.lower() != ".mxl":
        path.write_bytes(xml)
        return

    with zipfile.ZipFile(path, "r") as source:
        container = ET.fromstring(source.read("META-INF/container.xml"))
        rootfile = next(
            (node.get("full-path") for node in container.iter()
             if node.tag.rsplit("}", 1)[-1] == "rootfile" and node.get("full-path")),
            None,
        )
        if not rootfile:
            raise ValueError("Compressed MusicXML has no root score")
        members = [(info, source.read(info.filename)) for info in source.infolist()]
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".mxl", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        with zipfile.ZipFile(temp_path, "w") as destination:
            for info, data in members:
                destination.writestr(info, xml if info.filename == rootfile else data)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def edit_marker(path: Path, measure_number: str, action: str,
                ending_number: str | None = None) -> int:
    """Apply an idempotent marker edit to matching measures in every part."""
    if action not in EDIT_ACTIONS:
        raise ValueError(f"Unsupported MusicXML edit: {action}")
    if action.startswith("add_ending_") and not ending_number:
        raise ValueError("An ending number is required")

    root = read_musicxml(path)
    matches = [
        measure for part in root.findall("part") for measure in part.findall("measure")
        if measure.get("number") == str(measure_number)
    ]
    if not matches:
        raise ValueError(f"Measure {measure_number} was not found")
    changed = sum(_edit_measure(measure, action, ending_number) for measure in matches)
    if changed:
        _write_musicxml(path, root)
    return changed


def apply_marker_edits(path: Path, edits: list[dict], save: bool = False) -> tuple[int, bytes]:
    """Apply ordered edits in memory, optionally committing once at the end."""
    root = read_musicxml(path)
    changed = 0
    for edit in edits:
        action = edit["action"]
        ending_number = edit.get("ending_number")
        if action not in EDIT_ACTIONS:
            raise ValueError(f"Unsupported MusicXML edit: {action}")
        if action.startswith("add_ending_") and not ending_number:
            raise ValueError("An ending number is required")
        measure_number = str(edit["measure"])
        matches = [
            measure for part in root.findall("part") for measure in part.findall("measure")
            if measure.get("number") == measure_number
        ]
        if not matches:
            raise ValueError(f"Measure {measure_number} was not found")
        changed += sum(_edit_measure(measure, action, ending_number) for measure in matches)
    xml = _serialize_musicxml(root)
    if save and changed:
        _write_musicxml(path, root)
    return changed, xml
