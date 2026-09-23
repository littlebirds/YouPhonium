"""FastAPI backend for PDF sheet music to MIDI conversion."""
import sys
import os

# Force line-buffered output so status shows in IDE terminals (Cursor, VS Code)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)
os.environ["PYTHONUNBUFFERED"] = "1"

sys.stderr.write("[YouPhonium] Booting...\n")
sys.stderr.flush()

# Set before any matplotlib import (avoids font manager hang on macOS)
import tempfile
from pathlib import Path
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib_youphonium"))

import base64
import hashlib
import json
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal, Optional

sys.stderr.write("[YouPhonium] Loading FastAPI...\n")
sys.stderr.flush()
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.stderr.write("[YouPhonium] Loading services (converter, omr)...\n")
sys.stderr.flush()
from services.converter import musicxml_to_midi
from services.recognition_quality import analyze_musicxml, read_musicxml
from services.musicxml_editor import apply_marker_edits
from services.musicxml_layout import (
    get_measure_boundaries,
    get_playback_time_map,
    get_measure_layout_positions,
    get_measures_per_first_system,
    get_measures_per_system_for_layout,
    get_system_regions,
    get_system_time_ranges,
)
from services.omr import (
    find_audiveris,
    find_homr,
    find_oemer,
    find_oemer_fast,
    image_to_musicxml,
    pdf_to_musicxml,
)
sys.stderr.write("[YouPhonium] Ready.\n")
sys.stderr.flush()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Print status banner on startup."""
    audiveris_ok = find_audiveris() is not None
    homr_ok = find_homr()
    oemer_ok = find_oemer_fast()  # Fast check, no heavy import
    banner = f"""
============================================
  YouPhonium server is running
============================================
  Local:   http://localhost:8000
  Network: http://0.0.0.0:8000
  API docs: http://localhost:8000/docs
--------------------------------------------
  OMR engines:
    HOMR:      {"✓" if homr_ok else "✗"}
    oemer:     {"✓" if oemer_ok else "✗"}
    Audiveris: {"✓" if audiveris_ok else "✗"}
--------------------------------------------
  Status: Ready. Waiting for requests...
============================================
"""
    sys.stderr.write(banner)
    sys.stderr.flush()
    yield
    sys.stderr.write("[YouPhonium] Shutting down...\n")
    sys.stderr.flush()

# In-memory job storage: job_id -> { status, message, result?, error? }
_upload_jobs: dict[str, dict] = {}
_library_write_lock = threading.Lock()


def _library_dir() -> Path:
    path = Path(__file__).resolve().parent.parent / "omr_output"
    path.mkdir(exist_ok=True)
    return path


def _library_id(path: Path) -> str:
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:24]


def _library_scores() -> list[Path]:
    directory = _library_dir()
    return sorted(
        (path for path in directory.iterdir()
         if path.is_file() and path.suffix.lower() in (".musicxml", ".mxl", ".xml")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _library_metadata(path: Path) -> dict:
    metadata_path = path.with_suffix(".library.json")
    if metadata_path.exists():
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Ignoring invalid library metadata: %s", metadata_path)
    return {}


def _library_path(item_id: str) -> Path:
    path = next((candidate for candidate in _library_scores()
                 if _library_id(candidate) == item_id), None)
    if path is None:
        raise HTTPException(404, "Music not found")
    return path


def _safe_upload_name(filename: str) -> str:
    return Path(filename.replace("\\", "/")).name or "score"


def _find_duplicate(filename: str, source_sha256: str, engine: str) -> Optional[Path]:
    safe_name = _safe_upload_name(filename)
    for path in _library_scores():
        metadata = _library_metadata(path)
        if (metadata.get("original_filename") == safe_name
                and metadata.get("source_sha256") == source_sha256
                and metadata.get("engine") == engine):
            return path
    return None


def _hashed_output_name(filename: str, source_sha256: str, engine: str) -> str:
    """Return a stable collision-resistant source name for the OMR service."""
    safe_name = _safe_upload_name(filename)
    source = Path(safe_name)
    stem = source.stem or "score"
    suffix = source.suffix
    token = source_sha256[:8]
    engine_suffix = "" if engine == "homr" else f"--{engine}"
    base = f"{stem}--{token}{engine_suffix}"
    candidate = base
    number = 2
    existing_stems = {path.stem for path in _library_scores()}
    while candidate in existing_stems:
        candidate = f"{base}-{number}"
        number += 1
    return f"{candidate}{suffix}"


def _write_library_metadata(path: Path, filename: str, source_sha256: str, engine: str) -> None:
    if not path.exists() or path.parent != _library_dir():
        return
    metadata = {
        "original_filename": _safe_upload_name(filename),
        "source_sha256": source_sha256,
        "engine": engine,
        "created_at": time.time(),
    }
    path.with_suffix(".library.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _result_from_musicxml(path: Path, filename: Optional[str] = None,
                          engine: Optional[str] = None) -> dict:
    metadata = _library_metadata(path)
    filename = filename or metadata.get("original_filename") or path.name
    engine = engine or metadata.get("engine")
    report_path = path.with_suffix(".recognition.json")
    recognition_report = (json.loads(report_path.read_text(encoding="utf-8"))
                          if report_path.exists() else analyze_musicxml(path))
    if recognition_report.get("validator_version") != 2:
        recognition_report = _refresh_recognition_report(path)
    midi_bytes = b""
    playback_error = None
    try:
        midi_bytes = musicxml_to_midi(path)
    except (ValueError, RuntimeError) as exc:
        playback_error = str(exc)
    first_part = read_musicxml(path).find("part")
    measure_numbers = ([measure.get("number", str(index + 1))
                        for index, measure in enumerate(first_part.findall("measure"))]
                       if first_part is not None else [])
    return {
        "success": True,
        "library_id": _library_id(path),
        "engine": engine,
        "midi_base64": base64.b64encode(midi_bytes).decode("ascii"),
        "playback_error": playback_error,
        "musicxml_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "musicxml_format": "mxl" if path.suffix.lower() == ".mxl" else "xml",
        "filename": filename,
        "recognition_report": recognition_report,
        "measures_per_first_system": get_measures_per_first_system(path),
        "measures_per_line": get_measures_per_system_for_layout(path),
        "measure_boundaries": get_measure_boundaries(path),
        "playback_time_map": get_playback_time_map(path),
        "system_time_ranges": get_system_time_ranges(path),
        "system_regions": get_system_regions(path),
        "measure_layout_positions": get_measure_layout_positions(path),
        "measure_note_positions": [],
        "measure_numbers": measure_numbers,
        "musicxml_path": str(path.resolve()),
    }


class MusicXmlMarkerEdit(BaseModel):
    measure: str = Field(min_length=1, max_length=20)
    action: Literal[
        "add_forward_repeat", "add_backward_repeat",
        "remove_forward_repeat", "remove_backward_repeat",
        "add_ending_start", "add_ending_stop", "add_ending_discontinue",
        "remove_endings",
    ]
    ending_number: Optional[str] = Field(default=None, min_length=1, max_length=12)


class MusicXmlEditBatch(BaseModel):
    edits: list[MusicXmlMarkerEdit] = Field(min_length=1, max_length=100)


def _refresh_recognition_report(path: Path) -> dict:
    """Re-run structural checks without losing OMR/layout provenance."""
    report_path = path.with_suffix(".recognition.json")
    previous = {}
    if report_path.exists():
        try:
            previous = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    report = analyze_musicxml(path)
    generated_keys = {
        "validator_version", "status", "measure_count", "pitched_note_count", "has_encoded_breaks",
        "issues", "issue_counts", "notice",
    }
    report.update({key: value for key, value in previous.items() if key not in generated_keys})
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report

app = FastAPI(title="YouPhonium API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_stale_app_shell(request, call_next):
    """Always revalidate the small app shell so frontend updates take effect."""
    response = await call_next(request)
    if request.url.path in ("/", "/index.html", "/app.js", "/styles.css"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/health")
def health():
    """Health check endpoint."""
    audiveris_ok = find_audiveris() is not None
    homr_ok = find_homr()
    oemer_ok = find_oemer_fast()  # Fast check, no heavy import
    return {
        "status": "ok",
        "app": "YouPhonium",
        "project_id": hashlib.sha256(str(Path(__file__).resolve().parent.parent).encode()).hexdigest()[:16],
        "omr_audiveris": audiveris_ok,
        "omr_homr": homr_ok,
        "omr_oemer": oemer_ok,
        "audiveris_installed": audiveris_ok,
    }


def _recognize_upload(
    file_path: Path, filename: str, stored_filename: str, engine: Optional[str],
    is_image: bool, on_progress,
) -> dict:
    """Recognize one engine without owning/deleting the shared upload."""
    engine = engine or "homr"
    recognize = image_to_musicxml if is_image else pdf_to_musicxml
    musicxml_path, omr_layout, omr_note_positions = recognize(
        file_path, original_filename=stored_filename, on_progress=on_progress, engine=engine,
    )
    on_progress("Preparing notation…")
    musicxml_bytes = musicxml_path.read_bytes()
    report_path = musicxml_path.with_suffix(".recognition.json")
    recognition_report = (json.loads(report_path.read_text(encoding="utf-8"))
                          if report_path.exists() else analyze_musicxml(musicxml_path))
    # Rendering still works if MIDI generation fails. Do not lose an otherwise
    # useful recognition result just because its rhythm prevents playback.
    midi_bytes = b""
    playback_error = None
    try:
        on_progress("Converting to MIDI…")
        midi_bytes = musicxml_to_midi(musicxml_path)
    except (ValueError, RuntimeError) as exc:
        playback_error = str(exc)
    return {
        "success": True,
        "engine": engine,
        "midi_base64": base64.b64encode(midi_bytes).decode("ascii"),
        "playback_error": playback_error,
        "musicxml_base64": base64.b64encode(musicxml_bytes).decode("ascii"),
        "musicxml_format": "mxl" if musicxml_path.suffix.lower() == ".mxl" else "xml",
        "filename": filename,
        "recognition_report": recognition_report,
        "measures_per_first_system": get_measures_per_first_system(musicxml_path),
        "measures_per_line": get_measures_per_system_for_layout(musicxml_path),
        "measure_boundaries": get_measure_boundaries(musicxml_path),
        "playback_time_map": get_playback_time_map(musicxml_path),
        "system_time_ranges": get_system_time_ranges(musicxml_path),
        "system_regions": get_system_regions(musicxml_path),
        "measure_layout_positions": omr_layout or get_measure_layout_positions(musicxml_path),
        "measure_note_positions": omr_note_positions or [],
        "musicxml_path": str(musicxml_path.resolve()),
    }


def _run_comparison_job(job_id: str, file_path: Path, filename: str, is_image: bool) -> None:
    job = _upload_jobs[job_id]
    engines = {name: {"status": "queued", "message": "Waiting"} for name in ("audiveris", "homr")}
    job["engines"] = engines
    # Serialized on purpose: both engines are memory-intensive. Each receives
    # the same unchanged upload and an engine/job-specific output name.
    for name in engines:
        entry = engines[name]
        entry.update(status="processing", message="Starting…")

        def progress(message: str) -> None:
            entry["message"] = message
            job["message"] = f"{name.upper()}: {message}"

        try:
            stored = f"{Path(filename).stem}__{job_id}__{name}{Path(filename).suffix}"
            result = _recognize_upload(file_path, filename, stored, name, is_image, progress)
            entry.update(status="complete", message="Ready", result=result)
        except Exception as exc:
            log.exception("%s recognition failed in comparison %s", name, job_id)
            entry.update(status="error", message="Failed", error=str(exc))
    completed = sum(e["status"] == "complete" for e in engines.values())
    job.update(status="complete", message=f"Comparison ready ({completed}/2 engines succeeded)")
    job["result"] = {"mode": "comparison", "success": completed > 0,
                     "filename": filename, "engines": engines}


def _run_upload_job(
    job_id: str, file_path: Path, filename: str, engine: Optional[str] = None, is_image: bool = False
) -> None:
    """Background task: run OMR and conversion, update job status."""
    job = _upload_jobs.get(job_id)
    if not job:
        return
    try:
        engine = engine or "homr"
        if engine == "compare":
            _run_comparison_job(job_id, file_path, filename, is_image)
            return
        def on_progress(msg: str) -> None:
            job["message"] = msg
            job["status"] = "processing"
            log.info("[Upload %s] %s", job_id[:8], msg)

        log.info("[Upload %s] Job started for %s (engine=%s, is_image=%s)", job_id[:8], filename, engine or "auto", is_image)
        source_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        # Keep the duplicate check and publication atomic. OMR is already a
        # serialized workload; this also prevents simultaneous identical
        # uploads from both writing the same hash-derived output name.
        with _library_write_lock:
            duplicate = _find_duplicate(filename, source_sha256, engine)
            if duplicate is not None:
                on_progress("Using existing recognition…")
                result = _result_from_musicxml(duplicate, filename, engine)
                result["deduplicated"] = True
            else:
                on_progress("Starting OMR…")
                stored_filename = _hashed_output_name(filename, source_sha256, engine)
                result = _recognize_upload(
                    file_path, filename, stored_filename, engine, is_image, on_progress)
                result_path = Path(result["musicxml_path"])
                _write_library_metadata(result_path, filename, source_sha256, engine)
                result["library_id"] = _library_id(result_path)
                result["deduplicated"] = False
        job["status"] = "complete"
        job["message"] = "Complete"
        job["result"] = result
        log.info("MusicXML kept at: %s", result["musicxml_path"])
    except FileNotFoundError as e:
        job["status"] = "error"
        job["message"] = "Error"
        job["error"] = str(e)
    except (RuntimeError, ValueError) as e:
        msg = str(e)
        log.error("OMR failed: %s", msg)
        sys.stderr.write(f"\n*** OMR ERROR: {msg} ***\n\n")
        sys.stderr.flush()
        job["status"] = "error"
        job["message"] = "Error"
        job["error"] = msg
    except Exception as e:
        log.exception("Upload job failed")
        job["status"] = "error"
        job["message"] = "Error"
        job["error"] = str(e)
    finally:
        if file_path.exists():
            file_path.unlink(missing_ok=True)


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    engine: Optional[str] = Form("homr"),
    background_tasks: BackgroundTasks = None,
):
    """
    Start PDF or image upload and OMR. Returns job_id; poll GET /upload/status/{job_id} for progress.
    HOMR is the default. Explicit legacy engine/compare requests remain supported for API clients.
    """
    if not file.filename:
        raise HTTPException(400, "Please upload a file")
    ext = file.filename.lower().split(".")[-1] if "." in file.filename else ""
    if ext not in ("pdf", "png", "jpg", "jpeg"):
        raise HTTPException(400, "Please upload a PDF or image file (PNG, JPG)")
    if engine and engine not in ("homr", "oemer", "audiveris", "compare"):
        raise HTTPException(400, "engine must be 'compare', 'homr', 'oemer', or 'audiveris'")
    engine = engine or "homr"

    is_image = ext in ("png", "jpg", "jpeg")

    job_id = str(uuid.uuid4())
    _upload_jobs[job_id] = {
        "status": "processing",
        "message": "Uploading file…",
        "result": None,
        "error": None,
        "started_at": time.time(),
    }

    suffix = f".{ext}"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        file_path = Path(tmp.name)

    _upload_jobs[job_id]["message"] = "Starting OMR…"
    background_tasks.add_task(_run_upload_job, job_id, file_path, file.filename, engine, is_image)

    return JSONResponse(content={"job_id": job_id})


@app.get("/upload/status/{job_id}")
def upload_status(job_id: str):
    """Get upload job status and result when complete."""
    job = _upload_jobs.get(job_id)
    if not job:
        raise HTTPException(
            404,
            "Job not found. The server may have restarted (jobs are in-memory). Please try uploading again.",
        )
    out = {"status": job["status"], "message": job["message"]}
    if job.get("engines"):
        out["engines"] = {name: {k: v for k, v in entry.items() if k != "result"}
                          for name, entry in job["engines"].items()}
    if job.get("started_at"):
        out["started_at"] = job["started_at"]
    if job["status"] == "complete" and job.get("result"):
        out["result"] = job["result"]
    if job["status"] == "error" and job.get("error"):
        out["error"] = job["error"]
    return out


@app.get("/library")
def list_library():
    """List the server's shared recognized-score library."""
    items = []
    for path in _library_scores():
        metadata = _library_metadata(path)
        original = metadata.get("original_filename") or path.name
        items.append({
            "id": _library_id(path),
            "filename": original,
            "engine": metadata.get("engine"),
            "updated_at": path.stat().st_mtime,
        })
    return {"items": items}


@app.get("/library/{item_id}")
def get_library_item(item_id: str):
    """Load notation, playback, and review data for one shared score."""
    return _result_from_musicxml(_library_path(item_id))


@app.post("/library/{item_id}/validate")
def validate_library_item(item_id: str):
    """Run semantic validation on demand without changing the MusicXML."""
    path = _library_path(item_id)
    with _library_write_lock:
        report = _refresh_recognition_report(path)
    return {"success": True, "recognition_report": report}


@app.post("/library/{item_id}/edit-preview")
def preview_library_edits(item_id: str, batch: MusicXmlEditBatch):
    """Render staged marker edits without writing the shared score."""
    path = _library_path(item_id)
    try:
        changed, xml = apply_marker_edits(
            path, [edit.model_dump() for edit in batch.edits], save=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "success": True,
        "edit_changed": changed,
        "musicxml_base64": base64.b64encode(xml).decode("ascii"),
        "musicxml_format": "xml",
    }


@app.post("/library/{item_id}/edit-batch")
def save_library_edits(item_id: str, batch: MusicXmlEditBatch):
    """Commit a set of previewed marker edits in one explicit save."""
    path = _library_path(item_id)
    try:
        with _library_write_lock:
            changed, _ = apply_marker_edits(
                path, [edit.model_dump() for edit in batch.edits], save=True)
            _refresh_recognition_report(path)
            result = _result_from_musicxml(path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result["edit_changed"] = changed
    return result


@app.delete("/library/{item_id}")
def delete_library_item(item_id: str):
    """Permanently delete a recognized score and its generated sidecars."""
    path = _library_path(item_id)
    removed = []
    companions = [
        path,
        path.with_suffix(".recognition.json"),
        path.with_suffix(".library.json"),
        path.with_suffix(".omr"),
        path.with_suffix(".original.omr"),
        path.with_suffix(".audiveris.log"),
    ]
    for companion in companions:
        if companion.exists() and companion.parent == _library_dir():
            companion.unlink()
            removed.append(companion.name)
    return {"success": True, "removed": removed}


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Transcribe recorded audio to notes for practice comparison.

    Accepts WAV, MP3, or WebM audio. Returns JSON with note list.
    """
    if not file.filename:
        raise HTTPException(400, "No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".wav", ".mp3", ".webm", ".ogg", ".m4a"):
        raise HTTPException(400, "Unsupported format. Use WAV, MP3, or WebM.")

    audio_path = None
    try:
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(400, "Empty audio file")

        suffix = ext if ext else ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            audio_path = Path(tmp.name)

        try:
            from services.transcribe import transcribe_audio_to_notes
        except ImportError as e:
            raise HTTPException(
                503,
                "Audio transcription requires basic-pitch (pip install basic-pitch[onnx]), "
                "which needs Python 3.10–3.12. Not available on this Python version.",
            ) from e
        notes = transcribe_audio_to_notes(audio_path)
        return JSONResponse(content={"notes": notes})
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}") from None
    finally:
        if audio_path and audio_path.exists():
            audio_path.unlink(missing_ok=True)


# Serve frontend static files (must be after API routes)
_frontend_path = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_path), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
