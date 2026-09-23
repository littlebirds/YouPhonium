# YouPhonium

Upload a PDF or image of sheet music and play it in Euphonium sound (approximated by trombone) in your browser.

## Desktop launch (macOS)

1. If you do not already have Python **3.11+** with `venv`, install it using the
   [Python macOS installer](https://www.python.org/downloads/macos/). Python 3.13
   is the version tested with this project. Also double-click its
   **Install Certificates.command** in the Python folder
   under Applications to enable secure downloads. See the
   [Python macOS installation guide](https://docs.python.org/3/using/mac.html).
2. In Finder, double-click **Start YouPhonium.command** in this project folder.
   A Terminal window opens; you do not need to type any commands.
3. First-time setup starts automatically. It **creates `backend/venv`
   if missing**, installs the backend requirements into it, and downloads missing
   HOMR models. Later launches reuse that environment and the downloaded models.
4. The browser opens automatically when the server is ready.

Keep the Terminal window open while using the app. Press **Ctrl+C** or close the
Terminal window to stop the server. Closing only the browser does not stop it.

The launcher checks an existing project venv, your PATH, and standard Python.org
and Homebrew locations on Apple Silicon and Intel Macs. It does not install
Homebrew, modify Apple's Python, or require `sudo`. If a usable Python is missing,
it prints installation guidance. Advanced users can set `YOUPHONIUM_PYTHON` to a
specific Python executable. Setup errors are saved to `.launcher/youphonium.log`.

If macOS blocks opening a downloaded launcher, review the warning and use Apple's
[instructions for opening trusted apps](https://support.apple.com/en-us/102445);
do not disable security protections globally.

## Desktop launch (Debian / Linux)

1. Open this project folder in your file manager. Right-click **Start YouPhonium.sh**
   and choose **Run as a Program** (or double-click and choose **Run**, depending
   on your file manager). If needed, enable **Allow executing file as program**
   in the file's Properties / Permissions first.
2. On first launch, setup starts automatically. The launcher creates `backend/venv`, installs
   the backend requirements, and downloads missing HOMR models. This can take
   several minutes and requires an internet connection; it does not use `sudo`
   or install Python packages system-wide.
3. Your browser opens when the app is ready.

Keep the launcher terminal open while using the app. Press **Ctrl+C** or close that
terminal to stop the server it started; closing just the browser does not. A second
launch reopens this project's running server without taking ownership of it.
The launcher listens on the local network (`0.0.0.0`) and chooses a free port
from 8000–8009. Other devices on the same trusted network can open
`http://<this-computer's-LAN-IP>:<port>`. It does not stop other apps occupying
those ports. Do not expose the port to the public internet.

Python **3.11+** and venv support must already be installed. On Debian,
install `python3` and `python3-venv` with your package manager
(or ask whoever manages the computer to do so). Subsequent launches reuse the
environment and models; changed requirements trigger setup again. Status and
errors appear in the terminal, with details in `.launcher/youphonium.log`.

## Prerequisites (manual setup)

1. **Python 3.11+** – for the backend and HOMR
2. **HOMR** – `pip install homr`. AI-based recognition; tested with HOMR 0.7.0 on Python 3.13. Included in the backend requirements.

### Recognition engine

The app always uses **HOMR** and displays its recognized MusicXML directly.
There is no engine selector, comparison view, or Original/Recognized toggle.
The score header identifies the engine as **Recognized by HOMR**.

API clients that omit `engine` also use HOMR, without automatic fallback to
another installed engine. Explicit legacy API requests for Audiveris, oemer,
or `engine=compare` remain supported; these are not exposed in the app.

**Playback priorities:** For playback, pitches and rhythm matter most; dynamics and grace notes are optional. See [PLAN.md](PLAN.md) for the full analysis (based on commercial apps like Sheet Music Scanner).

## Manual setup

Skip this section when using the desktop launcher above.

1. Create a virtual environment and install dependencies:

   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Initialize HOMR's model weights before the first upload (internet access is
   needed for this download):

   ```bash
   homr --init
   ```

Audiveris and oemer are not needed to use the app.

## Manual run

From the `backend` directory:

```bash
source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.
Press Ctrl+C in the terminal to stop it. For development, add
`--reload --reload-exclude 'venv*/**'`. Matplotlib settings are applied automatically to
avoid a font-manager hang on macOS.

## Usage

1. Drag and drop a PDF, PNG, or JPEG onto the upload area, or click to browse.
2. Wait for HOMR recognition. Processing can take several minutes.
3. Review the rendered score and its recognition warnings. The original preview
   has been removed; consult your source file separately when checking accuracy.
4. Use **Play**, **Pause**, **Stop**, **Tempo**, and the seek bar in the score's
   header, or **Download MusicXML**. Results are added to the playlist with the
   engine name. A new upload displays its own result.

During playback, sounding notes turn purple, the current measure is shaded,
and a vertical marker follows the latest note onset. The score follows the
current system when it moves offscreen. Pause retains the visual position;
seeking and tempo changes keep it synchronized, and Stop clears it.

The notation and MusicXML download remain available if playback fails. A failed
preview reports an error instead of showing the source as though it were the
recognized result. Previews preserve encoded system breaks when available;
HOMR layout is not verified against the source geometry.

## Limitations

- **OMR accuracy** varies: clean digital PDFs work best; handwritten or low-quality scans may fail.
- **Complex scores** (many staves, dense notation) may have recognition errors.
- **Euphonium sound** is approximated by trombone (General MIDI has no Euphonium). A custom Euphonium soundfont can be added later.
- **Multi-page PDFs** are processed page by page and merged into one score.

### Recognition review

PNG/JPEG uploads are flattened onto white, EXIF-oriented, and enlarged based on
detected staff spacing (not the image's DPI tag). Audiveris runs with its normal
notehead calibration. If structural checks find problems, one alternate
font/scale pass is tried; the structurally more complete result is kept. This
can roughly double recognition time. The comparison is **not an accuracy score**:
a complete measure can still contain wrong pitches or missing chord tones.

The player displays recognized MusicXML and lists suspicious measures.
For Audiveris, physical page/system boundaries and staff counts are read from
the `.omr` source geometry and written into the MusicXML. Rendering uses those
exact boundaries (including unequal numbers of measures per system), retains
empty staves, and does not reflow systems to fit the screen. Page height can grow
to fit the engraving; this preserves grouping, not pixel-identical spacing.
The review details show the system/staff/measure counts. If source measures or
parts cannot be mapped, or visible staves change between systems, a warning is
shown instead of claiming a source-layout match. Other engines retain any
encoded layout they provide, without verified source-geometry matching.
MIDI conversion preserves tuplets and
dotted rhythms instead of rounding them or guessing corrections. Review the
result before relying on playback or practice scoring.

Audiveris 5.11 may omit a whole measure when a hairpin has an untimed endpoint.
For that specific exporter exception, the pipeline retries a copy of the project
without the affected hairpin. Any removed expression mark is disclosed in the
review report; notes are not rewritten. MusicXML, a `.recognition.json` report,
and editable `.omr` projects are kept in `omr_output/` (including `.original.omr`
when export recovery was needed).

The playlist is a shared view of `omr_output/`, so scores remain available to
every client after a refresh or server restart. New uploads are identified by
their original filename plus a SHA-256 content hash. Exact repeats reuse the
existing recognition; different files with the same name receive a short hash
suffix. Deleting a playlist item after confirmation removes its generated score
and report files from the server.

Regression checks:

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=backend backend/venv/bin/python -m unittest discover -s backend/tests -v
node frontend/tests/recognition-ui.test.js
node frontend/tests/comparison.test.js
```

Optional desktop-window checks (requires Tkinter and Xvfb):

```bash
YOUPHONIUM_GUI_TEST=1 xvfb-run -a python3 -m unittest discover -s tests -v
```

## Project Structure

```
YouPhonium/
├── Start YouPhonium.command # macOS Finder entry point
├── Start YouPhonium.sh   # Linux file-manager entry point
├── launch.py            # Desktop launcher, setup, and Applications shortcut
├── tests/               # Launcher regression checks
├── backend/
│   ├── main.py           # FastAPI app, upload endpoint, static file serving
│   ├── requirements.txt
│   └── services/
│       ├── omr.py        # HOMR, oemer, and Audiveris OMR engines
│       └── converter.py  # MusicXML → MIDI via music21
├── frontend/
│   ├── index.html
│   ├── app.js            # Upload, MIDI playback, controls
│   └── styles.css
├── PLAN.md               # Development plan, playback prioritization (see Appendix)
└── README.md
```
