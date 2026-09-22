"""Narrow workaround for Audiveris's null-time WedgeIterators export crash."""

from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


def prepare_wedge_recovery(source: Path, destination: Path) -> int:
    """Copy a project, omitting only wedges attached to unscheduled chords.

    Call only after the matching exporter exception. Notes and rhythm are never
    changed. Keep the original project for correction in Audiveris's editor.
    Returns the number of omitted expression marks (must be disclosed).
    """
    entries = []
    removed = 0
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            data = archive.read(info)
            if info.filename.startswith("sheet#") and info.filename.endswith(".xml"):
                root = ET.fromstring(data)
                timed = {e.get("chord") for e in root.findall(".//voice/slots/entry/value")}
                timed.update(e.get("whole-chord") for e in root.iter("voice"))
                for sig in root.iter("sig"):
                    relations = sig.find("relations")
                    inters = sig.find("inters")
                    if relations is None or inters is None:
                        continue
                    bad = {r.get("target") for r in relations
                           if r.find("chord-wedge") is not None and r.get("source") not in timed}
                    wedges = [e for e in inters if e.tag == "wedge" and e.get("id") in bad]
                    for wedge in wedges:
                        inters.remove(wedge)
                    for relation in list(relations):
                        if relation.get("target") in bad or relation.get("source") in bad:
                            relations.remove(relation)
                    removed += len(wedges)
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            entries.append((info, data))
    if removed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for info, data in entries:
                archive.writestr(info, data)
    return removed
