(function () {
  "use strict";

  const API_URL = (window.location.protocol === "file:" || !window.location.origin || window.location.origin === "null")
    ? "http://localhost:8000"
    : "";

  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  const browseBtn = document.getElementById("browseBtn");
  const statusSection = document.getElementById("statusSection");
  const statusEl = document.getElementById("status");
  const playerSection = document.getElementById("playerSection");
  const playBtn = document.getElementById("playBtn");
  const pauseBtn = document.getElementById("pauseBtn");
  const stopBtn = document.getElementById("stopBtn");
  const reseekBtn = document.getElementById("reseekBtn");
  const reseekHint = document.getElementById("reseekHint");
  const tempoSlider = document.getElementById("tempoSlider");
  const tempoValueEl = document.getElementById("tempoValue");
  const progressBar = document.getElementById("progressBar");
  const progressText = document.getElementById("progressText");
  const errorSection = document.getElementById("errorSection");
  const errorBox = document.getElementById("errorBox");
  const notationSection = document.getElementById("notationSection");
  const verovioNotation = document.getElementById("verovioNotation");
  const notationTitle = document.getElementById("notationTitle");
  const playlistSection = document.getElementById("playlistSection");
  const playlistEl = document.getElementById("playlist");
  const mainPlaceholder = document.getElementById("mainPlaceholder");
  const practiceHint = document.getElementById("practiceHint");
  const recordBtn = document.getElementById("recordBtn");
  const stopRecordBtn = document.getElementById("stopRecordBtn");
  const practiceResults = document.getElementById("practiceResults");
  const accuracyValue = document.getElementById("accuracyValue");
  const correctCount = document.getElementById("correctCount");
  const wrongCount = document.getElementById("wrongCount");
  const missedCount = document.getElementById("missedCount");
  const pageNavSection = document.getElementById("pageNavSection");
  const prevPageBtn = document.getElementById("prevPageBtn");
  const nextPageBtn = document.getElementById("nextPageBtn");
  const pageNavText = document.getElementById("pageNavText");
  const measureHighlight = document.getElementById("measureHighlight");
  const playbackCursor = document.getElementById("playbackCursor");
  const notationViewport = document.getElementById("notationViewport");
  const recognitionReview = document.getElementById("recognitionReview");
  let musicxmlBlobUrl = null;
  let midiBlobUrl = null;
  const musicxmlDownload = document.getElementById("musicxmlDownload");
  const midiDownload = document.getElementById("midiDownload");
  const notationMessage = document.getElementById("notationMessage");
  const scoreValidateBtn = document.getElementById("scoreValidateBtn");
  const validationStatus = document.getElementById("validationStatus");
  const scoreEditBtn = document.getElementById("scoreEditBtn");
  const scoreEditor = document.getElementById("scoreEditor");
  const scoreEditorDragHandle = document.getElementById("scoreEditorDragHandle");
  const scoreEditorClose = document.getElementById("scoreEditorClose");
  const editMeasure = document.getElementById("editMeasure");
  const editEndingNumber = document.getElementById("editEndingNumber");
  const scoreEditorStatus = document.getElementById("scoreEditorStatus");
  const scoreEditorCancel = document.getElementById("scoreEditorCancel");
  const scoreEditorSave = document.getElementById("scoreEditorSave");
  const editMeasureSelection = document.getElementById("editMeasureSelection");
  const endingStartPreview = document.getElementById("endingStartPreview");

  let playlist = [];
  let currentTrackId = null;
  let playlistIdCounter = 0;
  let pendingMusicXmlEdits = [];
  let selectedEditMeasure = null;
  let pendingEndingStart = null;
  let scoreEditorManuallyPositioned = false;

  let audioContext = null;
  let instrument = null;
  let midiData = null;
  let notes = [];
  let totalDuration = 0;
  let scoreDuration = 0;  /* when set, use for playback end (matches sheet); else use totalDuration */
  let playhead = 0;
  let lastPlayedIndex = 0;
  let tempo = 1;
  let startRealTime = 0;
  let rafId = null;
  let isPlaying = false;
  let playbackStartPending = false;
  let playRequestId = 0;
  let audioNeedsWakeRecovery = false;
  let reseekActive = false;
  let verovioTk = null;
  let verovioReady = null;
  let currentNotationPage = 1;
  let notationPageManuallySelected = false;
  let hasVerovioScore = false;
  let playbackHighlightVisible = false;
  let followedSystem = null;
  let practiceComparison = null;
  let noteIdMap = null;
  let mediaRecorder = null;
  let mediaStream = null;
  let recordedChunks = [];

  verovioReady =
    typeof verovio !== "undefined"
      ? new Promise(function (resolve) {
          verovio.module.onRuntimeInitialized = function () {
            verovioTk = new verovio.toolkit();
            verovioTk.setOptions({ pageWidth: 800, scale: 50 });
            resolve();
          };
        })
      : Promise.resolve();

  let uploadBusy = false;
  let trackLoading = false;
  function setUploadControlsDisabled(disabled) {
    [browseBtn, fileInput].forEach(function (control) {
      if (control) control.disabled = disabled;
    });
  }

  var currentTrackForLayout = null;

  function applyVerovioLayout(relayout) {
    if (!verovioTk) return;
    var track = currentTrackForLayout;
    var sourceLayout = track && track.recognitionReport && track.recognitionReport.source_layout;
    var pageW = 2100;
    var pageH = 2970;
    if (sourceLayout && sourceLayout.pages && sourceLayout.pages.length) {
      var sourcePage = sourceLayout.pages[0];
      if (sourcePage.width > 0 && sourcePage.height > 0) {
        pageH = Math.max(100, Math.min(60000, Math.round(pageW * sourcePage.height / sourcePage.width)));
      }
    }
    var encodedBreaks = track && track.recognitionReport
      ? (sourceLayout && sourceLayout.preserved) || track.recognitionReport.has_encoded_breaks === true
      : !!(track && track.systemRegions && track.systemRegions.length > 1);
    // Set BEFORE importing as well as before redoLayout: an import must not
    // start with the previous track's condensation or automatic line breaks.
    verovioTk.setOptions({
        pageWidth: pageW,
        pageHeight: pageH,
        scale: 100,
        adjustPageWidth: false,
        adjustPageHeight: true,
        // Keep corresponding measures on the same system as the source.
        // Without encoded breaks, row 2 can end with a different measure.
        breaks: encodedBreaks ? "encoded" : "auto",
        breaksNoWidow: false,
        systemMaxPerPage: 0,
        condense: "none",
        spacingNonLinear: 0.6,
        spacingLinear: 0.25,
      });
    if (relayout !== false && typeof verovioTk.redoLayout === "function") {
      verovioTk.redoLayout();
    }
  }

  function updatePageNav() {
    if (!pageNavSection) return;
    var total = 1;
    if (verovioTk && hasVerovioScore) total = verovioTk.getPageCount ? verovioTk.getPageCount() : 1;
    if (total <= 1) {
      pageNavSection.hidden = true;
      return;
    }
    pageNavSection.hidden = false;
    if (pageNavText) pageNavText.textContent = "Page " + currentNotationPage + " of " + total;
    if (prevPageBtn) prevPageBtn.disabled = currentNotationPage <= 1;
    if (nextPageBtn) nextPageBtn.disabled = currentNotationPage >= total;
  }

  function goToPage(pageNum) {
    if (!verovioTk || !hasVerovioScore) return;
    var total = verovioTk.getPageCount();
    currentNotationPage = Math.max(1, Math.min(total, pageNum));
    notationPageManuallySelected = true;
    clearVerovioHighlights();
    verovioNotation.innerHTML = verovioTk.renderToSVG(currentNotationPage);
    restoreEditMeasureSelection();
    updatePageNav();
  }

  function getVerovioScoreDuration() {
    if (!verovioTk || totalDuration <= 0) return totalDuration;
    var pageCount = verovioTk.getPageCount ? verovioTk.getPageCount() : 1;
    if (pageCount > 1) return totalDuration;
    var stepSec = 0.5;
    var lastValidSec = 0;
    for (var t = 0; t <= totalDuration; t += stepSec) {
      var el = verovioTk.getElementsAtTime(t * 1000);
      if (el && el.page && el.page !== 0) lastValidSec = t;
    }
    if (lastValidSec <= 0) return totalDuration;
    return Math.min(totalDuration, lastValidSec + stepSec);
  }

  function notationTimeForPlayback(playbackSeconds) {
    var track = currentTrackForLayout;
    var timeline = track && track.playbackTimeMap;
    if (!timeline || !timeline.length) return playbackSeconds;
    for (var i = 0; i < timeline.length; i++) {
      var segment = timeline[i];
      var isLast = i === timeline.length - 1;
      if (playbackSeconds < segment.playback_start) break;
      if (playbackSeconds < segment.playback_end || isLast) {
        var playbackLength = segment.playback_end - segment.playback_start;
        var scoreLength = segment.score_end - segment.score_start;
        var progress = playbackLength > 0
          ? Math.max(0, Math.min(1, (playbackSeconds - segment.playback_start) / playbackLength))
          : 0;
        return segment.score_start + progress * scoreLength;
      }
    }
    return playbackSeconds;
  }

  function playbackTimeForNotation(notationSeconds, currentSeconds) {
    var track = currentTrackForLayout;
    var timeline = track && track.playbackTimeMap;
    if (!timeline || !timeline.length) return notationSeconds;
    var candidates = [];
    for (var i = 0; i < timeline.length; i++) {
      var segment = timeline[i];
      var scoreLength = segment.score_end - segment.score_start;
      if (scoreLength <= 0 || notationSeconds < segment.score_start
          || notationSeconds >= segment.score_end) continue;
      var progress = (notationSeconds - segment.score_start) / scoreLength;
      candidates.push(segment.playback_start
        + progress * (segment.playback_end - segment.playback_start));
    }
    if (!candidates.length) return notationSeconds;

    // A written measure can occur more than once in expanded repeat playback.
    // If the target could mean either direction, reseek means rewind: choose
    // the latest strictly earlier occurrence before considering a future one.
    var earlier = candidates.filter(function (candidate) {
      return candidate < currentSeconds - 0.001;
    });
    if (earlier.length) return Math.max.apply(Math, earlier);
    return Math.min.apply(Math, candidates);
  }

  function updateNotationView(followPlayback) {
    if (!hasVerovioScore || !verovioTk) return;
    playbackHighlightVisible = true;
    var track = currentTrackForLayout;
    var notationTime = notationTimeForPlayback(playhead);
    var timeMs = notationTime * 1000;
    var currentElements = verovioTk.getElementsAtTime(timeMs);
    var targetPage = currentNotationPage;

    if (!notationPageManuallySelected && currentElements && currentElements.page && currentElements.page !== 0) {
      targetPage = currentElements.page;
    }

    if (targetPage !== currentNotationPage) {
      currentNotationPage = targetPage;
      if (verovioNotation) {
        verovioNotation.innerHTML = verovioTk.renderToSVG(currentNotationPage);
      }
      updatePageNav();
    }
    var cursorPageVisible = !(notationPageManuallySelected && currentElements
      && currentElements.page && currentElements.page !== currentNotationPage);

    if (verovioNotation) {
      var playingNotes = verovioNotation.querySelectorAll("g.note.playing, [data-playing]");
      for (var i = 0; i < playingNotes.length; i++) {
        var p = playingNotes[i];
        p.classList.remove("playing");
        p.removeAttribute("data-playing");
        p.style.fill = "";
        p.style.stroke = "";
        var kids = p.querySelectorAll("*");
        for (var k = 0; k < kids.length; k++) {
          kids[k].style.fill = "";
          kids[k].style.stroke = "";
        }
      }
      var measureEl = null;
      var cursorNote = null;
      var measureProgress = null;
      var latestOnset = -Infinity;
      var hasPlayingNotes = cursorPageVisible && currentElements
        && currentElements.notes && currentElements.notes.length;
      if (hasPlayingNotes) {
        currentElements.notes.forEach(function (id) {
          var note = verovioNotation.querySelector("#" + CSS.escape(id));
          if (!note) return;
          note.classList.add("playing");
          note.setAttribute("data-playing", "1");
          // A sustained chord in one voice must not pin the marker to the
          // beginning of the bar while the other voice moves forward.
          var onset = typeof verovioTk.getTimeForElement === "function" ? verovioTk.getTimeForElement(id) : 0;
          if (onset >= latestOnset) {
            latestOnset = onset;
            cursorNote = note;
          }
        });
        measureEl = cursorNote && cursorNote.closest("g.measure");
      }
      if (!measureEl && cursorPageVisible && currentElements && track && track.measureBoundaries) {
        var boundaries = track.measureBoundaries;
        for (var b = 0; b < boundaries.length; b++) {
          var atFinalBoundary = b === boundaries.length - 1 && notationTime === boundaries[b][1];
          if (notationTime >= boundaries[b][0]
              && (notationTime < boundaries[b][1] || atFinalBoundary)) {
            var boundaryDuration = boundaries[b][1] - boundaries[b][0];
            measureProgress = boundaryDuration > 0
              ? Math.max(0, Math.min(1, (notationTime - boundaries[b][0]) / boundaryDuration))
              : 0;
            var sampleMs = (boundaries[b][0] + 0.05) * 1000;
            var sampleEl = verovioTk.getElementsAtTime(sampleMs);
            if (sampleEl && sampleEl.notes && sampleEl.notes.length) {
              var sampleNoteEl = verovioNotation.querySelector("#" + CSS.escape(sampleEl.notes[0]));
              measureEl = sampleNoteEl && sampleNoteEl.closest ? (sampleNoteEl.closest("g.measure") || sampleNoteEl.closest("[class*='measure']")) : null;
            }
            break;
          }
        }
      }
      if (measureEl || hasPlayingNotes) {
        if (measureHighlight && notationViewport && measureEl) {
          var vpRect = notationViewport.getBoundingClientRect();
          var mRect = measureEl.getBoundingClientRect();
          measureHighlight.style.display = "block";
          measureHighlight.style.left = (mRect.left - vpRect.left) + "px";
          measureHighlight.style.top = (mRect.top - vpRect.top) + "px";
          measureHighlight.style.width = mRect.width + "px";
          measureHighlight.style.height = mRect.height + "px";
          if (cursorNote && playbackCursor) {
            var notehead = cursorNote.querySelector(".notehead") || cursorNote;
            var nRect = notehead.getBoundingClientRect();
            playbackCursor.hidden = false;
            playbackCursor.style.left = (nRect.left + nRect.width / 2 - vpRect.left) + "px";
            playbackCursor.style.top = (mRect.top - vpRect.top) + "px";
            playbackCursor.style.height = mRect.height + "px";
          } else if (playbackCursor && measureProgress != null) {
            playbackCursor.hidden = false;
            playbackCursor.style.left = (mRect.left + mRect.width * measureProgress - vpRect.left) + "px";
            playbackCursor.style.top = (mRect.top - vpRect.top) + "px";
            playbackCursor.style.height = mRect.height + "px";
          } else if (playbackCursor) {
            playbackCursor.hidden = true;
          }
          var system = measureEl.closest("g.system") || measureEl;
          if (isPlaying && followPlayback !== false && system !== followedSystem) {
            followedSystem = system;
            var sRect = system.getBoundingClientRect();
            if ((sRect.top < 0 || sRect.bottom > window.innerHeight) && system.scrollIntoView) {
              system.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
          }
        } else if (measureHighlight) {
          measureHighlight.style.display = "none";
          if (playbackCursor) playbackCursor.hidden = true;
        }
      } else if (measureHighlight) {
        measureHighlight.style.display = "none";
        if (playbackCursor) playbackCursor.hidden = true;
      }
    }
  }

  function clearVerovioHighlights() {
    playbackHighlightVisible = false;
    followedSystem = null;
    if (playbackCursor) playbackCursor.hidden = true;
    if (measureHighlight) {
      measureHighlight.style.display = "none";
    }
    if (verovioNotation) {
      var playingNotes = verovioNotation.querySelectorAll("g.note.playing, [data-playing]");
      for (var i = 0; i < playingNotes.length; i++) {
        var p = playingNotes[i];
        p.classList.remove("playing");
        p.removeAttribute("data-playing");
        p.style.fill = "";
        p.style.stroke = "";
        var kids = p.querySelectorAll("*");
        for (var k = 0; k < kids.length; k++) {
          kids[k].style.fill = "";
          kids[k].style.stroke = "";
        }
      }
    }
  }

  function clearPracticeFeedback() {
    practiceComparison = null;
    noteIdMap = null;
    if (verovioNotation) {
      var els = verovioNotation.querySelectorAll("[data-practice]");
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        el.removeAttribute("data-practice");
        el.style.fill = "";
        el.style.stroke = "";
        var kids = el.querySelectorAll("*");
        for (var k = 0; k < kids.length; k++) {
          kids[k].style.fill = "";
          kids[k].style.stroke = "";
        }
      }
    }
  }

  function compareNotes(expectedNotes, recordedNotes) {
    var TIME_WINDOW = 0.2;
    var PITCH_TOLERANCE = 1;
    var correct = [];
    var wrong = [];
    var missed = [];
    var used = {};
    for (var i = 0; i < expectedNotes.length; i++) {
      var exp = expectedNotes[i];
      var expTime = exp.time;
      var expMidi = exp.midi;
      var best = null;
      var bestDist = Infinity;
      for (var j = 0; j < recordedNotes.length; j++) {
        if (used[j]) continue;
        var rec = recordedNotes[j];
        var recTime = rec.start !== undefined ? rec.start : rec.time;
        var recMidi = rec.midi;
        var timeDist = Math.abs(recTime - expTime);
        var pitchDist = Math.abs(recMidi - expMidi);
        if (timeDist <= TIME_WINDOW && pitchDist < bestDist) {
          bestDist = pitchDist;
          best = { j: j, rec: rec, pitchDist: pitchDist };
        }
      }
      if (best && best.pitchDist <= PITCH_TOLERANCE) {
        correct.push({ expIdx: i, recIdx: best.j });
        used[best.j] = true;
      } else if (best) {
        wrong.push({ expIdx: i, recIdx: best.j });
        used[best.j] = true;
      } else {
        missed.push({ expIdx: i });
      }
    }
    var accuracy = expectedNotes.length > 0
      ? Math.round((correct.length / expectedNotes.length) * 100)
      : 0;
    return { correct: correct, wrong: wrong, missed: missed, accuracy: accuracy };
  }

  function applyPracticeFeedback(comparison) {
    if (!verovioTk || !verovioNotation || !comparison) return;
    clearPracticeFeedback();
    var correctColor = "rgb(34, 197, 94)";  /* green for agreed notes */
    var errorColor = "rgb(239, 68, 68)";    /* red for wrong or missed notes */
    for (var i = 0; i < notes.length; i++) {
      var timeMs = notationTimeForPlayback(notes[i].time) * 1000;
      var el = verovioTk.getElementsAtTime(timeMs);
      if (!el || !el.notes) continue;
      var noteIds = el.notes;
      var color = null;
      if (comparison.correct.some(function (c) { return c.expIdx === i; })) {
        color = correctColor;
      } else if (comparison.wrong.some(function (w) { return w.expIdx === i; }) ||
                 comparison.missed.some(function (m) { return m.expIdx === i; })) {
        color = errorColor;
      }
      if (color) {
        for (var j = 0; j < noteIds.length; j++) {
          var noteEl = verovioNotation.querySelector("#" + CSS.escape(noteIds[j]));
          if (noteEl) {
            noteEl.setAttribute("data-practice", "1");
            noteEl.style.fill = color;
            noteEl.style.stroke = color;
            var kids = noteEl.querySelectorAll("*");
            for (var k = 0; k < kids.length; k++) {
              kids[k].style.fill = color;
              kids[k].style.stroke = color;
            }
          }
        }
      }
    }
  }

  function showStatus(msg, type = "") {
    statusSection.hidden = false;
    statusEl.textContent = msg;
    statusEl.className = "status " + type;
  }

  function hideStatus() {
    statusSection.hidden = true;
  }

  function showError(msg) {
    errorSection.hidden = false;
    errorBox.textContent = msg;
  }

  function hideError() {
    errorSection.hidden = true;
  }

  function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showError("Microphone access is not supported in this browser.");
      return;
    }
    if (!hasVerovioScore || notes.length === 0) {
      showError("Load a track with sheet music first.");
      return;
    }
    recordedChunks = [];
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(function (stream) {
        mediaStream = stream;
        var mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/ogg";
        try {
          mediaRecorder = new MediaRecorder(stream, { mimeType: mimeType });
        } catch (e) {
          mediaRecorder = new MediaRecorder(stream);
        }
        mediaRecorder.ondataavailable = function (e) {
          if (e.data && e.data.size > 0) recordedChunks.push(e.data);
        };
        mediaRecorder.start();
        recordBtn.disabled = true;
        stopRecordBtn.disabled = false;
        practiceResults.hidden = true;
        if (recordBtn.classList) recordBtn.classList.add("recording");
      })
      .catch(function (err) {
        showError("Could not access microphone: " + (err.message || "Permission denied"));
      });
  }

  function stopRecording() {
    if (!mediaRecorder || mediaRecorder.state === "inactive") return;
    mediaRecorder.stop();
    stopRecordBtn.disabled = true;
    if (recordBtn.classList) recordBtn.classList.remove("recording");
    mediaRecorder.onstop = function () {
      if (mediaStream) {
        mediaStream.getTracks().forEach(function (t) { t.stop(); });
        mediaStream = null;
      }
      mediaRecorder = null;
      if (recordedChunks.length === 0) {
        showError("Recording was empty. Try again.");
        recordBtn.disabled = false;
        return;
      }
      var blob = new Blob(recordedChunks, { type: "audio/webm" });
      recordedChunks = [];
      showStatus("Transcribing…", "loading");
      var formData = new FormData();
      formData.append("file", blob, "recording.webm");
      fetch((API_URL || window.location.origin) + "/transcribe", {
        method: "POST",
        body: formData,
      })
        .then(function (res) {
          return res.text().then(function (t) {
            if (!res.ok) {
              var msg = res.statusText || "Transcription failed";
              try {
                var body = JSON.parse(t);
                if (body.detail) msg = body.detail;
              } catch (e) { if (t) msg = t; }
              throw new Error(msg);
            }
            return JSON.parse(t);
          });
        })
        .then(function (data) {
          hideStatus();
          var recordedNotes = data.notes || [];
          var comparison = compareNotes(notes, recordedNotes);
          practiceComparison = comparison;
          accuracyValue.textContent = String(comparison.accuracy);
          correctCount.textContent = String(comparison.correct.length);
          wrongCount.textContent = String(comparison.wrong.length);
          missedCount.textContent = String(comparison.missed.length);
          practiceResults.hidden = false;
          applyPracticeFeedback(comparison);
          recordBtn.disabled = false;
        })
        .catch(function (err) {
          hideStatus();
          showError(err.message || "Transcription failed");
          recordBtn.disabled = false;
        });
    };
  }

  function formatTime(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function ensureAudioContext() {
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioContext;
  }

  function wait(milliseconds) {
    return new Promise(function (resolve) { setTimeout(resolve, milliseconds); });
  }

  function resumeWithTimeout(ac) {
    return new Promise(function (resolve, reject) {
      var timeoutId = setTimeout(function () {
        reject(new Error("Audio did not resume"));
      }, 2500);
      Promise.resolve(ac.resume()).then(function (value) {
        clearTimeout(timeoutId);
        resolve(value);
      }, function (error) {
        clearTimeout(timeoutId);
        reject(error);
      });
    });
  }

  function recoverAudioForPlayback() {
    if (audioContext && audioContext.state === "closed") {
      audioContext = null;
      instrument = null;
    }
    return loadInstrument().then(function () {
      var ac = ensureAudioContext();
      if (!audioNeedsWakeRecovery && ac.state === "running") return ac;

      // iOS can leave a context "interrupted", or even report "running"
      // while its output remains silent after screen lock. A short
      // suspend/resume cycle from the user's Play gesture recovers both cases.
      var prepare = Promise.resolve();
      if (audioNeedsWakeRecovery && typeof ac.suspend === "function") {
        // Do not await suspend(): affected WebKit versions can leave audio
        // state promises unresolved while the hardware session reconnects.
        try {
          var suspension = ac.suspend();
          if (suspension && suspension.catch) suspension.catch(function () {});
        } catch (error) { /* resume below remains the recovery attempt */ }
        prepare = wait(250);
      }
      return prepare.then(function () { return resumeWithTimeout(ac); }).then(function () {
        if (ac.state !== "running") throw new Error("Audio is still unavailable");
        audioNeedsWakeRecovery = false;
        return ac;
      });
    });
  }

  function loadInstrument() {
    if (instrument) return Promise.resolve(instrument);
    const ac = ensureAudioContext();
    return Soundfont.instrument(ac, "trombone", {
      soundfont: "FluidR3_GM",
    }).then(function (inst) {
      instrument = inst;
      return inst;
    });
  }

  function collectNotes(midi) {
    const all = [];
    midi.tracks.forEach(function (track) {
      if (track.notes && track.notes.length) {
        track.notes.forEach(function (note) {
          all.push({
            time: note.time,
            midi: note.midi,
            duration: note.duration,
            velocity: note.velocity != null ? note.velocity : 0.8,
          });
        });
      }
    });
    all.sort(function (a, b) {
      return a.time - b.time;
    });
    return all;
  }

  function tick() {
    if (!isPlaying || !instrument) return;
    var endTime = scoreDuration > 0 ? scoreDuration : totalDuration;
    const elapsed = (performance.now() - startRealTime) / 1000 * tempo;
    playhead = Math.min(elapsed, endTime);

    while (lastPlayedIndex < notes.length && notes[lastPlayedIndex].time <= playhead) {
      const note = notes[lastPlayedIndex];
      const scaledDuration = note.duration / tempo;
      instrument.play(note.midi, 0, {
        duration: scaledDuration,
        gain: note.velocity,
      });
      lastPlayedIndex++;
    }

    var progressPct = endTime > 0 ? Math.min(100, (playhead / endTime) * 100) : 0;
    progressBar.style.width = progressPct + "%";
    progressText.textContent = formatTime(playhead) + " / " + formatTime(endTime);
    updateNotationView();

    // Let the last note finish; scheduling it is not the end of playback.
    var reachedEnd = playhead >= endTime;
    if (!reachedEnd) {
      rafId = requestAnimationFrame(tick);
    } else {
      isPlaying = false;
      playhead = 0;
      lastPlayedIndex = 0;
      progressBar.style.width = "0%";
      progressText.textContent = "0:00 / " + formatTime(endTime);
      playBtn.disabled = false;
      pauseBtn.disabled = true;
      stopBtn.disabled = true;
      updateReseekControl();
      clearVerovioHighlights();
    }
  }

  function beginPlayback() {
    if (playhead >= (scoreDuration > 0 ? scoreDuration : totalDuration)) {
      playhead = 0;
      lastPlayedIndex = 0;
    }
    clearPracticeFeedback();
    hideError();
    isPlaying = true;
    setReseekMode(false);
    playBtn.disabled = true;
    pauseBtn.disabled = false;
    stopBtn.disabled = false;
    updateReseekControl();
    startRealTime = performance.now() - (playhead / tempo) * 1000;
    updateNotationView();
    rafId = requestAnimationFrame(tick);
  }

  function play() {
    if (isPlaying || playbackStartPending || trackLoading || !notes.length) {
      return Promise.resolve(false);
    }
    var ac = ensureAudioContext();
    if (instrument && ac.state === "running" && !audioNeedsWakeRecovery) {
      beginPlayback();
      return Promise.resolve(true);
    }

    var requestId = ++playRequestId;
    playbackStartPending = true;
    playBtn.disabled = true;
    updateReseekControl();
    return recoverAudioForPlayback().then(function () {
      if (requestId !== playRequestId) return false;
      beginPlayback();
      return true;
    }).catch(function () {
      if (requestId !== playRequestId) return false;
      showError("Audio could not resume after the interruption. Tap Play to try again.");
      playBtn.disabled = false;
      pauseBtn.disabled = true;
      return false;
    }).finally(function () {
      if (requestId === playRequestId) {
        playbackStartPending = false;
        updateReseekControl();
      }
    });
  }

  function pause() {
    playRequestId++;
    playbackStartPending = false;
    if (isPlaying) {
      var endTime = scoreDuration > 0 ? scoreDuration : totalDuration;
      playhead = Math.min(endTime, (performance.now() - startRealTime) / 1000 * tempo);
      updateNotationView(false);
    }
    isPlaying = false;
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (instrument) instrument.stop();
    playBtn.disabled = false;
    pauseBtn.disabled = true;
    stopBtn.disabled = false;
    updateReseekControl();
  }

  function handlePlaybackInterruption() {
    if (!audioContext) return;
    audioNeedsWakeRecovery = true;
    if (isPlaying || playbackStartPending) pause();
  }

  function stop() {
    pause();
    playhead = 0;
    lastPlayedIndex = 0;
    progressBar.style.width = "0%";
    var endTime = scoreDuration > 0 ? scoreDuration : totalDuration;
    progressText.textContent = "0:00 / " + formatTime(endTime);
    clearVerovioHighlights();
    stopBtn.disabled = true;
  }

  function onTempoChange() {
    var now = performance.now();
    if (isPlaying) {
      var endTime = scoreDuration > 0 ? scoreDuration : totalDuration;
      playhead = Math.min(endTime, (now - startRealTime) / 1000 * tempo);
    }
    tempo = parseFloat(tempoSlider.value);
    if (isPlaying) startRealTime = now - (playhead / tempo) * 1000;
    tempoValueEl.textContent = tempo.toFixed(1) + "×";
    if (playbackHighlightVisible) updateNotationView(false);
  }

  function seekTo(seconds) {
    if (!notes.length || totalDuration <= 0) return;
    var endTime = scoreDuration > 0 ? scoreDuration : totalDuration;
    var t = Math.max(0, Math.min(endTime, seconds));
    playhead = t;
    lastPlayedIndex = 0;
    while (lastPlayedIndex < notes.length && notes[lastPlayedIndex].time < playhead) {
      lastPlayedIndex++;
    }
    if (instrument) instrument.stop();
    progressBar.style.width = (playhead / endTime) * 100 + "%";
    progressText.textContent = formatTime(playhead) + " / " + formatTime(endTime);
    notationPageManuallySelected = false;
    updateNotationView();
    if (isPlaying) {
      startRealTime = performance.now() - (playhead / tempo) * 1000;
    }
  }

  function handleProgressSeek(e) {
    var trackEl = document.getElementById("progressTrack");
    if (!trackEl || !notes.length) return;
    var endTime = scoreDuration > 0 ? scoreDuration : totalDuration;
    var rect = trackEl.getBoundingClientRect();
    var x = e.clientX - rect.left;
    var ratio = Math.max(0, Math.min(1, x / rect.width));
    seekTo(ratio * endTime);
  }

  function updateReseekControl() {
    if (!reseekBtn) return;
    var available = hasVerovioScore && notes.length > 0 && !!instrument
      && !playbackStartPending && !trackLoading;
    reseekBtn.disabled = !available;
    if (!available && reseekActive) setReseekMode(false);
  }

  function setReseekMode(active) {
    var available = hasVerovioScore && notes.length > 0 && !!instrument && !isPlaying
      && !playbackStartPending && !trackLoading;
    reseekActive = !!active && available;
    if (reseekBtn) reseekBtn.setAttribute("aria-pressed", reseekActive ? "true" : "false");
    if (verovioNotation) verovioNotation.classList.toggle("reseek-active", reseekActive);
    if (reseekHint) reseekHint.textContent = reseekActive
      ? "Tap a note or position in the score. Playback stays paused."
      : "Tap Reseek, then tap the score. Playback pauses automatically.";
    if (playbackCursor) {
      if (reseekActive) {
        notationPageManuallySelected = false;
        updateNotationView(false);
        playbackCursor.classList.add("reseek-awaiting");
      } else {
        playbackCursor.classList.remove("reseek-awaiting");
      }
    }
  }

  function toggleReseekMode() {
    if (!reseekBtn || reseekBtn.disabled) return;
    if (reseekActive) {
      setReseekMode(false);
      return;
    }
    if (isPlaying) pause();
    setReseekMode(true);
  }

  function reseekFromNotation(event) {
    if (!reseekActive || isPlaying || !event.target
        || typeof event.target.closest !== "function") return false;
    var measure = event.target.closest("g.measure");
    if (!measure) return false;
    var track = currentTrackForLayout;
    var measureNumber = measureNumberForGroup(measure);
    var measureIndex = track && track.measureNumbers
      ? track.measureNumbers.map(String).indexOf(String(measureNumber)) : -1;
    var boundary = measureIndex >= 0 && track.measureBoundaries
      ? track.measureBoundaries[measureIndex] : null;
    if (!boundary) {
      if (reseekHint) reseekHint.textContent = "That score position could not be mapped to playback.";
      return true;
    }

    var notationSeconds = null;
    var note = event.target.closest("g.note");
    if (note && note.id && verovioTk && typeof verovioTk.getTimeForElement === "function") {
      var noteMilliseconds = Number(verovioTk.getTimeForElement(note.id));
      if (Number.isFinite(noteMilliseconds)) notationSeconds = noteMilliseconds / 1000;
    }
    if (notationSeconds == null) {
      var rect = measure.getBoundingClientRect();
      var pointerX = Number.isFinite(event.clientX) ? event.clientX : rect.left;
      var ratio = rect.width > 0 ? (pointerX - rect.left) / rect.width : 0;
      ratio = Math.max(0, Math.min(0.999999, ratio));
      notationSeconds = boundary[0] + ratio * (boundary[1] - boundary[0]);
    }

    seekTo(playbackTimeForNotation(notationSeconds, playhead));
    setReseekMode(false);
    return true;
  }

  function handleNotationClick(event) {
    if (reseekFromNotation(event)) return;
    selectMeasureForEdit(event);
  }

  function renderRecognitionReview(track, openWhenIssues) {
    if (!recognitionReview) return;
    var report = track && track.recognitionReport;
    recognitionReview.hidden = !report;
    recognitionReview.open = !!(openWhenIssues && report && (report.issues || []).length);
    if (!report) return;
    var issues = report.issues || [];
    var sourceLayout = report.source_layout;
    var warnings = (report.export_warnings || []).concat(sourceLayout ? (sourceLayout.warnings || []) : []);
    var numbers = Array.from(new Set(issues.map(function (i) { return i.measure; })));
    var hasRepeatIssue = issues.some(function (item) {
      return item.code && (item.code.indexOf("repeat_") === 0 || item.code.indexOf("ending_") === 0);
    });
    document.getElementById("recognitionSummary").textContent = numbers.length
      ? "Check measures " + numbers.join(", ") + (hasRepeatIssue
        ? " — repeat structure may be incomplete"
        : " — recognition may be incomplete")
      : (warnings.length ? "Recognition export required recovery — review details" : "Semantic validation passed — continue visual review");
    document.getElementById("recognitionNotice").textContent = report.notice || "Review the automatic transcription against your source file.";
    if (sourceLayout && sourceLayout.preserved) {
      document.getElementById("recognitionNotice").textContent += " Source layout preserved: "
        + sourceLayout.systems.length + " systems; staves per system: "
        + sourceLayout.systems.map(function (s) { return s.staff_count; }).join(", ")
        + "; measures per system: " + sourceLayout.systems.map(function (s) { return s.measure_count; }).join(", ") + ".";
    }
    var issueList = document.getElementById("recognitionIssues");
    issueList.replaceChildren();
    warnings.concat(issues.map(function (i) { return i.message; })).forEach(function (message) {
      var item = document.createElement("li");
      item.textContent = message;
      issueList.appendChild(item);
    });
  }

  function setupTrackFromStoredData(track) {
    setReseekMode(false);
    pendingMusicXmlEdits = [];
    selectedEditMeasure = null;
    pendingEndingStart = null;
    if (editMeasureSelection) editMeasureSelection.hidden = true;
    if (endingStartPreview) endingStartPreview.hidden = true;
    if (musicxmlBlobUrl) URL.revokeObjectURL(musicxmlBlobUrl);
    if (midiBlobUrl) URL.revokeObjectURL(midiBlobUrl);
    musicxmlBlobUrl = null;
    midiBlobUrl = null;
    musicxmlDownload.hidden = true;
    musicxmlDownload.removeAttribute("href");
    midiDownload.hidden = true;
    midiDownload.removeAttribute("href");
    if (scoreEditBtn) {
      scoreEditBtn.hidden = !track.libraryId;
      scoreEditBtn.setAttribute("aria-expanded", "false");
    }
    if (scoreValidateBtn) scoreValidateBtn.hidden = !track.libraryId;
    if (validationStatus) validationStatus.textContent = "";
    if (scoreEditor) scoreEditor.hidden = true;
    if (scoreEditorStatus) scoreEditorStatus.textContent = "";
    notationMessage.hidden = true;
    notationViewport.hidden = true;
    verovioNotation.hidden = true;
    verovioNotation.innerHTML = "";
    clearVerovioHighlights();
    hasVerovioScore = false;
    notes = [];
    midiData = null;
    totalDuration = 0;
    scoreDuration = 0;
    currentNotationPage = 1;
    notationPageManuallySelected = false;
    currentTrackForLayout = track;
    renderRecognitionReview(track, false);
    return verovioReady.then(function () {
      var xmlBytes = null;
      if (track.musicxmlBase64) {
        xmlBytes = Uint8Array.from(atob(track.musicxmlBase64), function (c) { return c.charCodeAt(0); });
        musicxmlBlobUrl = URL.createObjectURL(new Blob([xmlBytes], {
          type: track.musicxmlFormat === "mxl" ? "application/vnd.recordare.musicxml" : "application/vnd.recordare.musicxml+xml",
        }));
        musicxmlDownload.href = musicxmlBlobUrl;
        musicxmlDownload.download = (track.filename || "score") + (track.musicxmlFormat === "mxl" ? ".mxl" : ".musicxml");
        musicxmlDownload.hidden = false;
      }
      var renderedMidi = null;
      try {
        if (!xmlBytes) throw new Error("No MusicXML was returned.");
        if (!verovioTk) throw new Error("The notation renderer could not load. Refresh and try again.");
        applyVerovioLayout(false);
        var loaded = track.musicxmlFormat === "mxl"
          ? verovioTk.loadZipDataBuffer(xmlBytes.buffer)
          : verovioTk.loadData(new TextDecoder().decode(xmlBytes));
        if (loaded === false) throw new Error("The returned MusicXML could not be read.");
        applyVerovioLayout();
        verovioNotation.innerHTML = verovioTk.renderToSVG(1);
        if (!verovioNotation.querySelector("svg")) throw new Error("No score preview was generated.");
        hasVerovioScore = true;
        notationViewport.hidden = false;
        verovioNotation.hidden = false;
      } catch (err) {
        notationMessage.textContent = "Recognition preview unavailable. " + err.message;
        notationMessage.hidden = false;
      }
      // A playback/export error must not replace the score with the source image.
      if (hasVerovioScore) {
        try { renderedMidi = verovioTk.renderToMIDI(); } catch (err) { /* use backend MIDI */ }
      }
      // The backend expands repeats and volta endings into performed order.
      // Prefer it over renderer-specific MIDI interpretations.
      var exportMidi = track.midiBase64 || renderedMidi;
      if (exportMidi) {
        try {
          var exportBytes = Uint8Array.from(atob(exportMidi), function (c) { return c.charCodeAt(0); });
          midiBlobUrl = URL.createObjectURL(new Blob([exportBytes], { type: "audio/midi" }));
          midiDownload.href = midiBlobUrl;
          midiDownload.download = (track.filename || "score") + ".mid";
          midiDownload.hidden = false;
        } catch (err) { /* playback error below provides the actionable message */ }
      }
      var candidates = [track.midiBase64, renderedMidi].filter(Boolean);
      for (var i = 0; i < candidates.length && !notes.length; i++) {
        try {
          var midiBytes = Uint8Array.from(atob(candidates[i]), function (c) { return c.charCodeAt(0); });
          midiData = new Midi(midiBytes.buffer);
          notes = collectNotes(midiData);
          totalDuration = midiData.duration;
        } catch (err) { /* try the next MIDI source */ }
      }
      scoreDuration = hasVerovioScore
        ? ((track.playbackTimeMap && track.playbackTimeMap.length) ? totalDuration : getVerovioScoreDuration())
        : 0;
      notationTitle.textContent = track.sourceFilename || (track.file && track.file.name) || track.filename || "Score";
      notationSection.hidden = false;
      updatePageNav();
      playBtn.disabled = notes.length === 0;
      updateReseekControl();
      recordBtn.disabled = !hasVerovioScore || notes.length === 0;
      stopRecordBtn.disabled = true;
      practiceHint.hidden = false;
      practiceHint.textContent = hasVerovioScore
        ? "Record your playing and compare it to the sheet music."
        : "Practice requires a recognized sheet music preview.";
      if (!notes.length) {
        showError(track.playbackError || "No playable notes found. The recognition result is still available for review.");
        return;
      }
      return loadInstrument().then(function () {
        updateReseekControl();
      }).catch(function (err) {
        playBtn.disabled = true;
        updateReseekControl();
        showError("Playback unavailable: " + err.message + ". The recognition result is still available for review.");
      });
    });
  }

  function renderPlaylist() {
    if (!playlistEl) return;
    playlistEl.innerHTML = "";
    playlist.forEach(function (track) {
      var li = document.createElement("li");
      li.className = "playlist-item" + (track.id === currentTrackId ? " active" : "");
      li.dataset.trackId = track.id;
      var title = document.createElement("span");
      title.className = "playlist-item-title";
      title.textContent = track.filename;
      li.appendChild(title);
      var actions = document.createElement("div");
      actions.className = "playlist-item-actions";
      var upBtn = document.createElement("button");
      upBtn.type = "button";
      upBtn.className = "btn btn-icon";
      upBtn.title = "Move up";
      upBtn.textContent = "\u2191";
      upBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        reorderTrack(track.id, -1);
      });
      var downBtn = document.createElement("button");
      downBtn.type = "button";
      downBtn.className = "btn btn-icon";
      downBtn.title = "Move down";
      downBtn.textContent = "\u2193";
      downBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        reorderTrack(track.id, 1);
      });
      var delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn btn-icon";
      delBtn.title = "Remove";
      delBtn.textContent = "\u00d7";
      delBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        deleteTrack(track.id);
      });
      actions.appendChild(upBtn);
      actions.appendChild(downBtn);
      actions.appendChild(delBtn);
      li.appendChild(actions);
      li.addEventListener("click", function (e) {
        if (!e.target.closest(".playlist-item-actions")) {
          loadTrack(track.id);
        }
      });
      playlistEl.appendChild(li);
    });
  }

  function displayTrackName(filename) {
    return (filename || "score").replace(/\.(pdf|png|jpg|jpeg|musicxml|mxl|xml)$/i, "");
  }

  function applyResultToTrack(track, data) {
    track.libraryId = data.library_id || track.libraryId || null;
    track.engine = data.engine || track.engine || null;
    track.filename = displayTrackName(data.filename || track.sourceFilename || track.filename);
    track.sourceFilename = data.filename || track.sourceFilename || null;
    track.midiBase64 = data.midi_base64 || "";
    track.playbackError = data.playback_error || null;
    track.musicxmlBase64 = data.musicxml_base64 || null;
    track.musicxmlFormat = data.musicxml_format || null;
    track.recognitionReport = data.recognition_report || null;
    track.measuresPerFirstSystem = data.measures_per_first_system;
    track.measuresPerLine = data.measures_per_line;
    track.measureBoundaries = data.measure_boundaries || [];
    track.playbackTimeMap = data.playback_time_map || [];
    track.systemTimeRanges = data.system_time_ranges || [];
    track.systemRegions = data.system_regions || [];
    track.measureLayoutPositions = data.measure_layout_positions || [];
    track.measureNotePositions = data.measure_note_positions || [];
    track.measureNumbers = data.measure_numbers || track.measureNumbers || [];
    return track;
  }

  function setScoreEditorPosition(left, top) {
    if (!scoreEditor) return;
    var editorRect = scoreEditor.getBoundingClientRect();
    var viewportWidth = window.innerWidth || 1200;
    var viewportHeight = window.innerHeight || 800;
    var editorWidth = Math.min(editorRect.width, viewportWidth - 24);
    var editorHeight = Math.min(editorRect.height, viewportHeight - 24);
    left = Math.max(12, Math.min(left, viewportWidth - editorWidth - 12));
    top = Math.max(12, Math.min(top, viewportHeight - editorHeight - 12));
    scoreEditor.style.left = left + "px";
    scoreEditor.style.top = top + "px";
  }

  function positionScoreEditor(group) {
    if (!scoreEditor || scoreEditor.hidden) return;
    if (scoreEditorManuallyPositioned) {
      var currentRect = scoreEditor.getBoundingClientRect();
      setScoreEditorPosition(currentRect.left, currentRect.top);
      return;
    }
    var anchor = group || scoreEditBtn;
    if (!anchor || typeof anchor.getBoundingClientRect !== "function") return;
    var anchorRect = anchor.getBoundingClientRect();
    var editorRect = scoreEditor.getBoundingClientRect();
    var viewportWidth = window.innerWidth || 1200;
    var viewportHeight = window.innerHeight || 800;
    var editorWidth = Math.min(editorRect.width, viewportWidth - 24);
    var left = anchorRect.left + (anchorRect.width - editorWidth) / 2;
    var anchorGap = group && pendingEndingStart ? 32 : 10;
    var top = group ? anchorRect.top - editorRect.height - anchorGap : anchorRect.bottom + 10;
    if (top < 12) top = anchorRect.bottom + 10;
    if (top + editorRect.height > viewportHeight - 12) {
      top = Math.max(12, viewportHeight - editorRect.height - 12);
    }
    setScoreEditorPosition(left, top);
  }

  function beginScoreEditorDrag(event) {
    if (!scoreEditor || scoreEditor.hidden || (event.button != null && event.button !== 0)) return;
    event.preventDefault();
    var rect = scoreEditor.getBoundingClientRect();
    var offsetX = event.clientX - rect.left;
    var offsetY = event.clientY - rect.top;
    scoreEditorManuallyPositioned = true;
    scoreEditor.classList.add("is-dragging");

    function onMove(moveEvent) {
      if (moveEvent.preventDefault) moveEvent.preventDefault();
      setScoreEditorPosition(moveEvent.clientX - offsetX, moveEvent.clientY - offsetY);
    }

    function onUp() {
      scoreEditor.classList.remove("is-dragging");
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onUp);
    }

    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
  }

  function setScoreEditorOpen(open) {
    if (!scoreEditor || !scoreEditBtn) return;
    var track = playlist.find(function (item) { return item.id === currentTrackId; });
    open = !!open && !!(track && track.libraryId);
    if (open && scoreEditor.hidden) {
      scoreEditorManuallyPositioned = false;
      pendingMusicXmlEdits = [];
      selectedEditMeasure = null;
      pendingEndingStart = null;
      if (editMeasure) editMeasure.value = "";
      if (scoreEditorSave) scoreEditorSave.disabled = true;
      if (scoreEditorStatus) scoreEditorStatus.textContent = "Select a measure directly in the notation below.";
    }
    scoreEditor.hidden = !open;
    scoreEditBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (!open && editMeasureSelection) editMeasureSelection.hidden = true;
    if (!open && endingStartPreview) endingStartPreview.hidden = true;
    if (open) positionScoreEditor(null);
  }

  function loadNotationPreview(base64, format) {
    return verovioReady.then(function () {
      if (!verovioTk || !base64) throw new Error("The notation preview is unavailable.");
      var bytes = Uint8Array.from(atob(base64), function (c) { return c.charCodeAt(0); });
      applyVerovioLayout(false);
      var loaded = format === "mxl"
        ? verovioTk.loadZipDataBuffer(bytes.buffer)
        : verovioTk.loadData(new TextDecoder().decode(bytes));
      if (loaded === false) throw new Error("The staged MusicXML could not be rendered.");
      applyVerovioLayout();
      var total = verovioTk.getPageCount ? verovioTk.getPageCount() : 1;
      currentNotationPage = Math.max(1, Math.min(currentNotationPage, total));
      verovioNotation.innerHTML = verovioTk.renderToSVG(currentNotationPage);
      hasVerovioScore = true;
      notationViewport.hidden = false;
      verovioNotation.hidden = false;
      updatePageNav();
      restoreEditMeasureSelection();
    });
  }

  function positionEditMeasureSelection(group, reanchorToolbar) {
    if (!editMeasureSelection || !group || !notationViewport) return;
    var groupRect = group.getBoundingClientRect();
    var viewportRect = notationViewport.getBoundingClientRect();
    editMeasureSelection.style.left = (groupRect.left - viewportRect.left) + "px";
    editMeasureSelection.style.top = (groupRect.top - viewportRect.top) + "px";
    editMeasureSelection.style.width = groupRect.width + "px";
    editMeasureSelection.style.height = groupRect.height + "px";
    editMeasureSelection.hidden = false;
    if (reanchorToolbar) {
      scoreEditorManuallyPositioned = false;
      positionScoreEditor(group);
      scoreEditorManuallyPositioned = true;
    }
  }

  function measureNumberForGroup(group) {
    if (!group) return null;
    var attributes = {};
    if (verovioTk && typeof verovioTk.getElementAttr === "function" && group.id) {
      try {
        attributes = verovioTk.getElementAttr(group.id) || {};
        if (typeof attributes === "string") attributes = JSON.parse(attributes);
      } catch (error) { attributes = {}; }
    }
    return attributes.n != null ? String(attributes.n) : (group.dataset ? group.dataset.measureNumber : null);
  }

  function restorePendingEndingPreview() {
    if (!endingStartPreview || !pendingEndingStart || scoreEditor.hidden) {
      if (endingStartPreview) endingStartPreview.hidden = true;
      return;
    }
    var groups = verovioNotation.querySelectorAll("g.measure[id]");
    for (var i = 0; i < groups.length; i++) {
      if (measureNumberForGroup(groups[i]) !== pendingEndingStart.measure) continue;
      var groupRect = groups[i].getBoundingClientRect();
      var viewportRect = notationViewport.getBoundingClientRect();
      endingStartPreview.style.left = (groupRect.left - viewportRect.left) + "px";
      endingStartPreview.style.top = Math.max(1, groupRect.top - viewportRect.top - 20) + "px";
      endingStartPreview.style.width = groupRect.width + "px";
      endingStartPreview.dataset.endingLabel = pendingEndingStart.number + ".";
      endingStartPreview.hidden = false;
      return;
    }
    endingStartPreview.hidden = true;
  }

  function restoreEditMeasureSelection(reanchorToolbar) {
    if (!selectedEditMeasure || scoreEditor.hidden) return;
    var groups = verovioNotation.querySelectorAll("g.measure[id]");
    for (var i = 0; i < groups.length; i++) {
      if (measureNumberForGroup(groups[i]) === selectedEditMeasure) {
        positionEditMeasureSelection(groups[i], !!reanchorToolbar);
        restorePendingEndingPreview();
        return;
      }
    }
    if (editMeasureSelection) editMeasureSelection.hidden = true;
    restorePendingEndingPreview();
  }

  function selectMeasureForEdit(event) {
    if (!scoreEditor || scoreEditor.hidden || !event.target || typeof event.target.closest !== "function") return;
    var group = event.target.closest("g.measure");
    if (!group) return;
    var number = measureNumberForGroup(group);
    if (!number) {
      if (scoreEditorStatus) scoreEditorStatus.textContent = "That measure could not be identified. Try clicking inside its staff lines.";
      return;
    }
    selectedEditMeasure = number;
    if (editMeasure) editMeasure.value = number;
    positionEditMeasureSelection(group, true);
    if (scoreEditorStatus) scoreEditorStatus.textContent = "Measure " + number + " selected.";
  }

  function selectEnteredMeasureForEdit() {
    if (!editMeasure || !scoreEditor || scoreEditor.hidden) return;
    var number = editMeasure.value.trim();
    var track = playlist.find(function (item) { return item.id === currentTrackId; });
    var knownMeasures = track && track.measureNumbers ? track.measureNumbers.map(String) : [];
    if (!number) {
      selectedEditMeasure = null;
      if (editMeasureSelection) editMeasureSelection.hidden = true;
      if (scoreEditorStatus) scoreEditorStatus.textContent = "Click a measure in the notation or type its number.";
      return;
    }
    if (knownMeasures.length && knownMeasures.indexOf(number) === -1) {
      selectedEditMeasure = null;
      if (editMeasureSelection) editMeasureSelection.hidden = true;
      if (scoreEditorStatus) scoreEditorStatus.textContent = "Measure " + number + " was not found in this score.";
      return;
    }
    selectedEditMeasure = number;
    restoreEditMeasureSelection(true);
    if (scoreEditorStatus) scoreEditorStatus.textContent = "Measure " + number + " selected.";
  }

  function validateCurrentScore() {
    if (trackLoading) return Promise.resolve(false);
    var track = playlist.find(function (item) { return item.id === currentTrackId; });
    if (!track || !track.libraryId) {
      showError("This score is not stored in the shared server library.");
      return Promise.resolve(false);
    }
    trackLoading = true;
    scoreValidateBtn.disabled = true;
    if (validationStatus) validationStatus.textContent = "Validating…";
    var baseUrl = API_URL || window.location.origin;
    return fetch(baseUrl + "/library/" + encodeURIComponent(track.libraryId) + "/validate", {
      method: "POST",
    }).then(function (response) {
      return response.text().then(function (body) {
        var data;
        try { data = JSON.parse(body); } catch (err) { data = {}; }
        if (response.ok === false) throw new Error(data.detail || "Semantic validation failed.");
        return data;
      });
    }).then(function (data) {
      track.recognitionReport = data.recognition_report || null;
      var issueCount = track.recognitionReport && track.recognitionReport.issues
        ? track.recognitionReport.issues.length : 0;
      renderRecognitionReview(track, issueCount > 0);
      if (validationStatus) validationStatus.textContent = issueCount
        ? issueCount + (issueCount === 1 ? " issue found" : " issues found")
        : "No semantic issues found";
      return true;
    }).catch(function (error) {
      if (validationStatus) validationStatus.textContent = error.message || "Semantic validation failed.";
      return false;
    }).finally(function () {
      trackLoading = false;
      scoreValidateBtn.disabled = false;
    });
  }

  function stageMusicXmlEdit(payload) {
    var repeatMatch = /^(?:add|remove)_(forward|backward)_repeat$/.exec(payload.action);
    if (!repeatMatch) return pendingMusicXmlEdits.concat([payload]);
    var repeatKey = String(payload.measure) + ":" + repeatMatch[1];
    return pendingMusicXmlEdits.filter(function (edit) {
      var match = /^(?:add|remove)_(forward|backward)_repeat$/.exec(edit.action);
      return !match || String(edit.measure) + ":" + match[1] !== repeatKey;
    }).concat([payload]);
  }

  function updatePendingEndingStart(payload) {
    if (payload.action === "add_ending_start") {
      pendingEndingStart = {
        measure: String(payload.measure),
        number: String(payload.ending_number || "1"),
      };
    } else if ((payload.action === "add_ending_stop" || payload.action === "add_ending_discontinue")
               && pendingEndingStart
               && pendingEndingStart.number === String(payload.ending_number || "1")) {
      pendingEndingStart = null;
    } else if (payload.action === "remove_endings" && pendingEndingStart
               && pendingEndingStart.measure === String(payload.measure)) {
      pendingEndingStart = null;
    }
  }

  function applyMusicXmlEdit(action) {
    if (trackLoading) return Promise.resolve(false);
    var track = playlist.find(function (item) { return item.id === currentTrackId; });
    if (!track || !track.libraryId) {
      showError("This score is not stored in the shared server library.");
      return Promise.resolve(false);
    }
    if (!selectedEditMeasure) {
      if (scoreEditorStatus) scoreEditorStatus.textContent = "Click a measure in the notation first.";
      return Promise.resolve(false);
    }
    var payload = { measure: selectedEditMeasure, action: action };
    if (action.indexOf("ending") !== -1 && action !== "remove_endings") {
      payload.ending_number = editEndingNumber ? editEndingNumber.value : "1";
    }
    var buttons = Array.from(document.querySelectorAll("[data-edit-action]"));
    buttons.forEach(function (button) { button.disabled = true; });
    trackLoading = true;
    var stagedEdits = stageMusicXmlEdit(payload);
    if (scoreEditorStatus) scoreEditorStatus.textContent = "Updating preview…";
    var baseUrl = API_URL || window.location.origin;
    return fetch(baseUrl + "/library/" + encodeURIComponent(track.libraryId) + "/edit-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits: stagedEdits }),
    }).then(function (response) {
      return response.text().then(function (body) {
        var data;
        try { data = JSON.parse(body); } catch (err) { data = {}; }
        if (response.ok === false) throw new Error(data.detail || "The correction could not be previewed.");
        return data;
      });
    }).then(function (data) {
      return loadNotationPreview(data.musicxml_base64, data.musicxml_format).then(function () {
        pendingMusicXmlEdits = stagedEdits;
        updatePendingEndingStart(payload);
        restoreEditMeasureSelection();
        restorePendingEndingPreview();
        playBtn.disabled = true;
        if (scoreEditorSave) scoreEditorSave.disabled = !!pendingEndingStart;
        if (scoreEditorStatus) {
          if (pendingEndingStart) {
            scoreEditorStatus.textContent = "Ending " + pendingEndingStart.number + " starts at measure "
              + pendingEndingStart.measure + ". Select its last measure and close the bracket.";
          } else {
            scoreEditorStatus.textContent = data.edit_changed
              ? "Preview updated. Save to update playback."
              : "The preview is unchanged; this marker may already be present.";
          }
        }
        return true;
      });
    }).catch(function (error) {
      if (scoreEditorStatus) scoreEditorStatus.textContent = error.message || "The correction could not be saved.";
      return false;
    }).finally(function () {
      trackLoading = false;
      buttons.forEach(function (button) { button.disabled = false; });
    });
  }

  function discardMusicXmlEdits() {
    var track = playlist.find(function (item) { return item.id === currentTrackId; });
    pendingMusicXmlEdits = [];
    selectedEditMeasure = null;
    pendingEndingStart = null;
    if (endingStartPreview) endingStartPreview.hidden = true;
    if (scoreEditorSave) scoreEditorSave.disabled = true;
    if (!track || !track.musicxmlBase64) {
      setScoreEditorOpen(false);
      return Promise.resolve(false);
    }
    return loadNotationPreview(track.musicxmlBase64, track.musicxmlFormat).then(function () {
      playBtn.disabled = notes.length === 0;
      setScoreEditorOpen(false);
      return true;
    });
  }

  function saveMusicXmlEdits() {
    if (trackLoading || !pendingMusicXmlEdits.length) return Promise.resolve(false);
    if (pendingEndingStart) {
      if (scoreEditorStatus) scoreEditorStatus.textContent = "Close the pending ending bracket before saving.";
      return Promise.resolve(false);
    }
    var track = playlist.find(function (item) { return item.id === currentTrackId; });
    if (!track || !track.libraryId) return Promise.resolve(false);
    trackLoading = true;
    scoreEditorSave.disabled = true;
    if (scoreEditorStatus) scoreEditorStatus.textContent = "Saving changes…";
    var baseUrl = API_URL || window.location.origin;
    return fetch(baseUrl + "/library/" + encodeURIComponent(track.libraryId) + "/edit-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits: pendingMusicXmlEdits }),
    }).then(function (response) {
      return response.text().then(function (body) {
        var data;
        try { data = JSON.parse(body); } catch (error) { data = {}; }
        if (response.ok === false) throw new Error(data.detail || "The changes could not be saved.");
        return data;
      });
    }).then(function (data) {
      pendingMusicXmlEdits = [];
      selectedEditMeasure = null;
      pendingEndingStart = null;
      if (endingStartPreview) endingStartPreview.hidden = true;
      applyResultToTrack(track, data);
      return setupTrackFromStoredData(track).then(function () {
        if (validationStatus) validationStatus.textContent = "Changes saved";
        return true;
      });
    }).catch(function (error) {
      scoreEditorSave.disabled = false;
      if (scoreEditorStatus) scoreEditorStatus.textContent = error.message || "The changes could not be saved.";
      return false;
    }).finally(function () {
      trackLoading = false;
    });
  }

  function addToPlaylist(data, file) {
    var existing = data.library_id && playlist.find(function (track) {
      return track.libraryId === data.library_id;
    });
    if (existing) {
      applyResultToTrack(existing, data);
      currentTrackId = existing.id;
      renderPlaylist();
      return existing;
    }
    var id = data.library_id ? "library-" + data.library_id : "track-" + (++playlistIdCounter);
    var filename = displayTrackName(data.filename || file.name);
    var track = {
      id: id,
      filename: filename,
      sourceFilename: data.filename || file.name,
      libraryId: data.library_id || null,
      engine: data.engine || null,
      midiBase64: data.midi_base64,
      playbackError: data.playback_error || null,
      musicxmlBase64: data.musicxml_base64 || null,
      musicxmlFormat: data.musicxml_format || null,
      recognitionReport: data.recognition_report || null,
      measuresPerFirstSystem: data.measures_per_first_system,
      measuresPerLine: data.measures_per_line,
      measureBoundaries: data.measure_boundaries || [],
      playbackTimeMap: data.playback_time_map || [],
      systemTimeRanges: data.system_time_ranges || [],
      systemRegions: data.system_regions || [],
      measureLayoutPositions: data.measure_layout_positions || [],
      measureNotePositions: data.measure_note_positions || [],
      measureNumbers: data.measure_numbers || [],
      file: file,
    };
    playlist.push(track);
    currentTrackId = track.id;
    if (playlistSection) playlistSection.hidden = false;
    renderPlaylist();
    return track;
  }

  function ensureTrackData(track) {
    if (track.musicxmlBase64 || !track.libraryId) return Promise.resolve(track);
    var baseUrl = API_URL || window.location.origin;
    return fetch(baseUrl + "/library/" + encodeURIComponent(track.libraryId))
      .then(function (response) {
        if (response.ok === false) throw new Error("The score is no longer available on the server.");
        return response.text().then(function (body) { return JSON.parse(body); });
      })
      .then(function (data) { return applyResultToTrack(track, data); });
  }

  function loadTrack(id, fromUpload) {
    if (trackLoading || (uploadBusy && !fromUpload)) return Promise.resolve(false);
    var track = playlist.find(function (t) { return t.id === id; });
    if (!track) return Promise.resolve(false);
    stop();
    trackLoading = true;
    setUploadControlsDisabled(true);
    clearPracticeFeedback();
    currentTrackId = id;
    renderPlaylist();
    showStatus("Loading track…", "loading");
    playerSection.hidden = false;
    return ensureTrackData(track).then(function () {
      return setupTrackFromStoredData(track);
    }).then(function () {
      hideStatus();
      progressBar.style.width = "0%";
      var endTime = scoreDuration > 0 ? scoreDuration : totalDuration;
      progressText.textContent = "0:00 / " + formatTime(endTime);
      playhead = 0;
      lastPlayedIndex = 0;
      tempoSlider.value = "1";
      onTempoChange();
      playerSection.hidden = false;
      if (mainPlaceholder) mainPlaceholder.hidden = true;
      return true;
    }).catch(function (err) {
      showError(err.message || "Failed to load track");
      hideStatus();
      // Leave the score visible and allow a sound-loading failure to be retried.
      return false;
    }).finally(function () {
      trackLoading = false;
      setUploadControlsDisabled(false);
      updateReseekControl();
    });
  }

  function removeTrackLocally(id) {
    if (trackLoading) return;
    var idx = playlist.findIndex(function (t) { return t.id === id; });
    if (idx < 0) return;
    var wasCurrent = playlist[idx].id === currentTrackId;
    playlist.splice(idx, 1);
    if (wasCurrent) {
      if (playlist.length > 0) {
        var nextIdx = Math.min(idx, playlist.length - 1);
        loadTrack(playlist[nextIdx].id);
      } else {
        stop();
        currentTrackId = null;
        if (playlistSection) playlistSection.hidden = true;
        playerSection.hidden = true;
        if (mainPlaceholder) mainPlaceholder.hidden = false;
        if (musicxmlBlobUrl) URL.revokeObjectURL(musicxmlBlobUrl);
        if (midiBlobUrl) URL.revokeObjectURL(midiBlobUrl);
        musicxmlBlobUrl = null;
        midiBlobUrl = null;
        musicxmlDownload.hidden = true;
        musicxmlDownload.removeAttribute("href");
        midiDownload.hidden = true;
        midiDownload.removeAttribute("href");
        midiData = null;
        notes = [];
        totalDuration = 0;
      }
    } else {
      renderPlaylist();
    }
  }

  function deleteTrack(id) {
    if (trackLoading) return;
    var track = playlist.find(function (candidate) { return candidate.id === id; });
    if (!track) return;
    var confirmed = !window.confirm || window.confirm(
      "Delete “" + track.filename + "” from the shared library? This cannot be undone."
    );
    if (!confirmed) return;
    if (!track.libraryId) {
      removeTrackLocally(id);
      return;
    }
    var baseUrl = API_URL || window.location.origin;
    fetch(baseUrl + "/library/" + encodeURIComponent(track.libraryId), { method: "DELETE" })
      .then(function (response) {
        if (!response.ok) throw new Error("Could not delete the score from the server.");
        removeTrackLocally(id);
      })
      .catch(function (error) { showError(error.message || "Could not delete the score."); });
  }

  function loadServerLibrary() {
    var baseUrl = API_URL || window.location.origin;
    return fetch(baseUrl + "/library")
      .then(function (response) {
        if (response.ok === false) throw new Error("Could not load the shared library.");
        return response.text().then(function (body) { return JSON.parse(body); });
      })
      .then(function (data) {
        playlist = (data.items || []).map(function (item) {
          return {
            id: "library-" + item.id,
            libraryId: item.id,
            sourceFilename: item.filename,
            filename: displayTrackName(item.filename, item.engine),
            engine: item.engine || null,
            midiBase64: "",
            musicxmlBase64: null,
          };
        });
        if (playlistSection) playlistSection.hidden = playlist.length === 0;
        renderPlaylist();
      });
  }

  function reorderTrack(id, direction) {
    var idx = playlist.findIndex(function (t) { return t.id === id; });
    if (idx < 0) return;
    var newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= playlist.length) return;
    var tmp = playlist[idx];
    playlist[idx] = playlist[newIdx];
    playlist[newIdx] = tmp;
    renderPlaylist();
  }

  browseBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    fileInput.click();
  });

  dropzone.addEventListener("click", function (e) {
    if (e.target === dropzone || e.target.closest(".dropzone-content")) {
      fileInput.click();
    }
  });

  dropzone.addEventListener("dragover", function (e) {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });

  dropzone.addEventListener("dragleave", function () {
    dropzone.classList.remove("dragover");
  });

  dropzone.addEventListener("drop", function (e) {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    const files = e.dataTransfer.files;
    if (files.length) handleFile(files[0]);
  });

  fileInput.addEventListener("change", function () {
    const files = fileInput.files;
    if (files.length) handleFile(files[0]);
    fileInput.value = "";
  });

  playBtn.addEventListener("click", play);
  pauseBtn.addEventListener("click", pause);
  stopBtn.addEventListener("click", stop);
  if (reseekBtn) reseekBtn.addEventListener("click", toggleReseekMode);
  tempoSlider.addEventListener("input", onTempoChange);

  if (recordBtn) recordBtn.addEventListener("click", startRecording);
  if (stopRecordBtn) stopRecordBtn.addEventListener("click", stopRecording);

  var progressTrack = document.getElementById("progressTrack");
  if (progressTrack) {
    progressTrack.addEventListener("click", handleProgressSeek);
    progressTrack.addEventListener("mousedown", function (e) {
      e.preventDefault();
      handleProgressSeek(e);
      function onMove(ev) {
        handleProgressSeek(ev);
      }
      function onUp() {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  }

  if (prevPageBtn) prevPageBtn.addEventListener("click", function () { goToPage(currentNotationPage - 1); });
  if (nextPageBtn) nextPageBtn.addEventListener("click", function () { goToPage(currentNotationPage + 1); });
  if (scoreValidateBtn) scoreValidateBtn.addEventListener("click", validateCurrentScore);
  if (scoreEditBtn) scoreEditBtn.addEventListener("click", function () {
    if (scoreEditor.hidden) setScoreEditorOpen(true);
    else if (!pendingMusicXmlEdits.length || window.confirm("Discard the unsaved score corrections?")) discardMusicXmlEdits();
  });
  if (scoreEditorClose) scoreEditorClose.addEventListener("click", function () {
    if (!pendingMusicXmlEdits.length || window.confirm("Discard the unsaved score corrections?")) discardMusicXmlEdits();
  });
  if (scoreEditorCancel) scoreEditorCancel.addEventListener("click", function () {
    if (!pendingMusicXmlEdits.length || window.confirm("Discard the unsaved score corrections?")) discardMusicXmlEdits();
  });
  if (scoreEditorSave) scoreEditorSave.addEventListener("click", saveMusicXmlEdits);
  if (scoreEditorDragHandle) scoreEditorDragHandle.addEventListener("pointerdown", beginScoreEditorDrag);
  if (editMeasure) editMeasure.addEventListener("input", selectEnteredMeasureForEdit);
  if (verovioNotation) verovioNotation.addEventListener("click", handleNotationClick);
  Array.from(document.querySelectorAll("[data-edit-action]")).forEach(function (button) {
    button.addEventListener("click", function () { applyMusicXmlEdit(button.dataset.editAction); });
  });

  // SVG note coordinates change when the responsive score is resized. Keep
  // the paused marker aligned too, without reactivating it after Stop.
  window.addEventListener("resize", function () {
    if (playbackHighlightVisible) updateNotationView(false);
    if (selectedEditMeasure) restoreEditMeasureSelection();
    else if (scoreEditor && !scoreEditor.hidden) positionScoreEditor(null);
  });
  window.addEventListener("scroll", function () {
    if (selectedEditMeasure) restoreEditMeasureSelection();
    else if (scoreEditor && !scoreEditor.hidden) positionScoreEditor(null);
  });

  // Mobile Safari interrupts Web Audio when the screen locks or the browser
  // moves to the background. Freeze our own clock at the same time so waking
  // cannot skip silent notes. Playback resumes only from a fresh Play gesture.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) handlePlaybackInterruption();
  });
  window.addEventListener("pagehide", handlePlaybackInterruption);
  window.addEventListener("pageshow", function (event) {
    if (event.persisted && audioContext) audioNeedsWakeRecovery = true;
  });

  // Check backend on load (read body once to avoid "stream already read" error)
  fetch((API_URL || window.location.origin) + "/health")
    .then(function (r) { return r.text().then(function (t) { return JSON.parse(t); }); })
    .then(function (data) {
      var availability = document.getElementById("engineAvailability");
      availability.textContent = "Recognition: HOMR · " + (data.omr_homr ? "ready" : "unavailable");
      if (!data.omr_homr) {
        showError("HOMR is unavailable. Install HOMR in the backend environment and initialize its models with homr --init.");
      }
    })
    .catch(function () {
      showError("Cannot reach the backend. Start it with: cd backend && python -m uvicorn main:app --port 8000");
    });

  loadServerLibrary().catch(function (error) {
    showError(error.message || "Could not load the shared music library.");
  });

  function pollUploadStatus(jobId, file, pollInterval) {
    var baseUrl = API_URL || window.location.origin;
    pollInterval = pollInterval || 800;
    function poll() {
      return fetch(baseUrl + "/upload/status/" + jobId)
        .then(function (r) {
          if (!r.ok) {
            if (r.status === 404) {
              throw new Error("Job not found. The server may have restarted. Please try uploading again.");
            }
            return r.json()
              .then(function (body) { throw new Error(body.detail || body.error || r.statusText || "Upload status check failed"); })
              .catch(function () { throw new Error(r.statusText || "Upload status check failed"); });
          }
          return r.json();
        })
        .then(function (data) {
          var msg = data.message || "Processing…";
          if (data.status === "processing") {
            msg = msg + " This will take several minutes.";
          }
          showStatus(msg, "loading");
          if (data.status === "complete" && data.result) {
            return data.result;
          }
          if (data.status === "error") {
            throw new Error(data.error || "Upload failed");
          }
          return new Promise(function (resolve, reject) {
            setTimeout(function () {
              poll().then(resolve).catch(reject);
            }, pollInterval);
          });
        });
    }
    return poll();
  }

  function handleFile(file) {
    if (uploadBusy) return;
    if (trackLoading) { showError("Please wait for the score's sound to finish loading."); return; }
    var ext = file.name.toLowerCase().split(".").pop();
    if (!/^(pdf|png|jpg|jpeg)$/.test(ext)) {
      showError("Please select a PDF or image file (PNG, JPG).");
      return;
    }

    uploadBusy = true;
    setUploadControlsDisabled(true);
    stop();

    hideError();
    showStatus("Uploading file…", "loading");
    playerSection.hidden = true;
    if (mainPlaceholder) mainPlaceholder.hidden = true;

    var baseUrl = API_URL || window.location.origin;
    var formData = new FormData();
    formData.append("file", file);
    formData.append("engine", "homr");

    return fetch(baseUrl + "/upload", {
      method: "POST",
      body: formData,
    })
      .then(function (res) {
        return res.text().then(function (t) {
          if (!res.ok) {
            var msg = res.statusText || "Upload failed";
            try {
              var body = JSON.parse(t);
              var detail = body.detail;
              if (Array.isArray(detail) && detail[0] && detail[0].msg) {
                msg = detail[0].msg;
              } else if (typeof detail === "string") {
                msg = detail;
              } else if (detail) {
                msg = String(detail);
              } else if (t) {
                msg = t;
              }
            } catch (e) {
              if (t) msg = t;
            }
            throw new Error(msg);
          }
          return JSON.parse(t);
        });
      })
      .then(function (data) {
        var jobId = data.job_id;
        if (!jobId) {
          throw new Error("Invalid response from server");
        }
        return pollUploadStatus(jobId, file, 800);
      })
      .then(function (data) {
        if (!data.success || !data.musicxml_base64) {
          throw new Error(data.playback_error || "HOMR returned no MusicXML.");
        }
        if (data.engine && data.engine !== "homr") {
          throw new Error("Expected a HOMR result. Refresh the app and check the backend.");
        }
        // The request explicitly selected HOMR, including with older API responses.
        data.engine = "homr";
        var track = addToPlaylist(data, file);
        if (!track) throw new Error("Playlist is full. Remove a track to add more.");
        return loadTrack(track.id, true);
      })
      .catch(function (err) {
        var msg = err.message || "Something went wrong.";
        if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
          msg = "Could not reach the server. Make sure the backend is running (python -m uvicorn main:app --reload).";
        } else if (msg.includes("body stream already read") || msg.includes("already read")) {
          msg = "Audio loading error. Try refreshing the page, or use Chrome/Firefox. If it persists, the soundfont CDN may be blocked.";
        }
        showError(msg);
        hideStatus();
        if (playlist.length > 0) {
          playerSection.hidden = false;
          if (mainPlaceholder) mainPlaceholder.hidden = true;
        } else if (mainPlaceholder) {
          mainPlaceholder.hidden = false;
          mainPlaceholder.textContent = "Upload sheet music to get started";
        }
      }).finally(function () {
        uploadBusy = false;
        setUploadControlsDisabled(false);
        fileInput.value = "";
      });
  }
})();
