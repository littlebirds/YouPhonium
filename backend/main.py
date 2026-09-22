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
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

sys.stderr.write("[YouPhonium] Loading FastAPI...\n")
sys.stderr.flush()
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

sys.stderr.write("[YouPhonium] Loading services (converter, omr)...\n")
sys.stderr.flush()
from services.converter import musicxml_to_midi
from services.recognition_quality import analyze_musicxml
from services.musicxml_layout import (
    get_measure_boundaries,
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

app = FastAPI(title="YouPhonium API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        on_progress("Starting OMR…")
        result = _recognize_upload(file_path, filename, filename, engine, is_image, on_progress)
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
