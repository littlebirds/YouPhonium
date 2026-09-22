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
  const trackNameEl = document.getElementById("trackName");
  const recognitionEngineEl = document.getElementById("recognitionEngine");
  const ENGINE_LABELS = { audiveris: "Audiveris", homr: "HOMR", oemer: "oemer" };
  const playBtn = document.getElementById("playBtn");
  const pauseBtn = document.getElementById("pauseBtn");
  const stopBtn = document.getElementById("stopBtn");
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
  const musicxmlDownload = document.getElementById("musicxmlDownload");
  const notationMessage = document.getElementById("notationMessage");

  const MAX_PLAYLIST_SIZE = 20;
  let playlist = [];
  let currentTrackId = null;
  let playlistIdCounter = 0;

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
  let verovioTk = null;
  let verovioReady = null;
  let currentNotationPage = 1;
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
    clearVerovioHighlights();
    verovioNotation.innerHTML = verovioTk.renderToSVG(currentNotationPage);
    var firstNote = verovioNotation.querySelector("g.note[id]");
    if (firstNote && typeof verovioTk.getTimeForElement === "function") {
      var time = verovioTk.getTimeForElement(firstNote.id);
      if (Number.isFinite(time) && time >= 0) seekTo(time / 1000);
    }
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

  function updateNotationView(followPlayback) {
    if (!hasVerovioScore || !verovioTk) return;
    playbackHighlightVisible = true;
    var track = currentTrackForLayout;
    var timeMs = playhead * 1000;
    var currentElements = verovioTk.getElementsAtTime(timeMs);
    var targetPage = currentNotationPage;

    if (currentElements && currentElements.page && currentElements.page !== 0) {
      targetPage = currentElements.page;
    }

    if (targetPage !== currentNotationPage) {
      currentNotationPage = targetPage;
      if (verovioNotation) {
        verovioNotation.innerHTML = verovioTk.renderToSVG(currentNotationPage);
      }
      updatePageNav();
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
      var measureEl = null;
      var cursorNote = null;
      var latestOnset = -Infinity;
      var hasPlayingNotes = currentElements && currentElements.notes && currentElements.notes.length;
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
      if (!measureEl && currentElements && track && track.measureBoundaries) {
        var boundaries = track.measureBoundaries;
        for (var b = 0; b < boundaries.length; b++) {
          if (playhead >= boundaries[b][0] && playhead < boundaries[b][1]) {
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
      var timeMs = notes[i].time * 1000;
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
      clearVerovioHighlights();
    }
  }

  function play() {
    if (isPlaying || trackLoading || !instrument || !notes.length) return;
    if (playhead >= (scoreDuration > 0 ? scoreDuration : totalDuration)) {
      playhead = 0;
      lastPlayedIndex = 0;
    }
    clearPracticeFeedback();
    ensureAudioContext().resume();
    hideError();
    isPlaying = true;
    playBtn.disabled = true;
    pauseBtn.disabled = false;
    stopBtn.disabled = false;
    startRealTime = performance.now() - (playhead / tempo) * 1000;
    updateNotationView();
    rafId = requestAnimationFrame(tick);
  }

  function pause() {
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

  function setupTrackFromStoredData(track) {
    // Keep the engine associated with the result, including older playlist entries.
    if (recognitionEngineEl) recognitionEngineEl.textContent = ENGINE_LABELS[track.engine]
      ? "Recognized by " + ENGINE_LABELS[track.engine] : "Recognition engine not recorded";
    if (musicxmlBlobUrl) URL.revokeObjectURL(musicxmlBlobUrl);
    musicxmlBlobUrl = null;
    musicxmlDownload.hidden = true;
    musicxmlDownload.removeAttribute("href");
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
    currentTrackForLayout = track;
    if (recognitionReview) {
      var report = track.recognitionReport;
      recognitionReview.hidden = !report;
      recognitionReview.open = false;
      if (report) {
        var issues = report.issues || [];
        var sourceLayout = report.source_layout;
        var warnings = (report.export_warnings || []).concat(sourceLayout ? (sourceLayout.warnings || []) : []);
        var numbers = Array.from(new Set(issues.map(function (i) { return i.measure; })));
        document.getElementById("recognitionSummary").textContent = numbers.length
          ? "Check measures " + numbers.join(", ") + " — recognition may be incomplete"
          : (warnings.length ? "Recognition export required recovery — review details" : "Automatic transcription — compare with the original");
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
    }
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
      var candidates = [renderedMidi, track.midiBase64].filter(Boolean);
      for (var i = 0; i < candidates.length && !notes.length; i++) {
        try {
          var midiBytes = Uint8Array.from(atob(candidates[i]), function (c) { return c.charCodeAt(0); });
          midiData = new Midi(midiBytes.buffer);
          notes = collectNotes(midiData);
          totalDuration = midiData.duration;
        } catch (err) { /* try the next MIDI source */ }
      }
      scoreDuration = hasVerovioScore ? getVerovioScoreDuration() : 0;
      notationTitle.textContent = "Recognized Sheet Music";
      notationSection.hidden = false;
      updatePageNav();
      playBtn.disabled = notes.length === 0;
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
      return loadInstrument().catch(function (err) {
        playBtn.disabled = true;
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

  function addToPlaylist(data, file) {
    if (playlist.length >= MAX_PLAYLIST_SIZE) {
      showError("Playlist is full. Remove a track to add more.");
      return;
    }
    var id = "track-" + (++playlistIdCounter);
    var filename = file.name.replace(/\.(pdf|png|jpg|jpeg)$/i, "");
    if (ENGINE_LABELS[data.engine]) filename += " — " + ENGINE_LABELS[data.engine];
    var track = {
      id: id,
      filename: filename,
      engine: data.engine || null,
      midiBase64: data.midi_base64,
      playbackError: data.playback_error || null,
      musicxmlBase64: data.musicxml_base64 || null,
      musicxmlFormat: data.musicxml_format || null,
      recognitionReport: data.recognition_report || null,
      measuresPerFirstSystem: data.measures_per_first_system,
      measuresPerLine: data.measures_per_line,
      measureBoundaries: data.measure_boundaries || [],
      systemTimeRanges: data.system_time_ranges || [],
      systemRegions: data.system_regions || [],
      measureLayoutPositions: data.measure_layout_positions || [],
      measureNotePositions: data.measure_note_positions || [],
      file: file,
    };
    playlist.push(track);
    currentTrackId = track.id;
    if (playlistSection) playlistSection.hidden = false;
    renderPlaylist();
    return track;
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
    return setupTrackFromStoredData(track).then(function () {
      hideStatus();
      trackNameEl.textContent = track.filename;
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
    });
  }

  function deleteTrack(id) {
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
        musicxmlBlobUrl = null;
        musicxmlDownload.hidden = true;
        musicxmlDownload.removeAttribute("href");
        midiData = null;
        notes = [];
        totalDuration = 0;
      }
    } else {
      renderPlaylist();
    }
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

  // SVG note coordinates change when the responsive score is resized. Keep
  // the paused marker aligned too, without reactivating it after Stop.
  window.addEventListener("resize", function () {
    if (playbackHighlightVisible) updateNotationView(false);
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
