(function () {
  "use strict";

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function bytesFromBase64(value) {
    var binary = atob(value);
    return Uint8Array.from(binary, function (character) { return character.charCodeAt(0); });
  }

  function formatTime(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  class RecognitionComparison {
    constructor(options) {
      this.section = options.section;
      this.results = options.results;
      this.original = options.original;
      this.ready = options.ready;
      this.onPlayback = options.onPlayback;
      this.urls = [];
      this.toolkits = [];
      this.cards = {};
      this.generation = 0;
      this.sourcePdf = null;
    }

    clear() {
      this.generation++;
      this.urls.forEach(function (url) { URL.revokeObjectURL(url); });
      this.urls = [];
      this.toolkits.forEach(function (toolkit) {
        if (typeof toolkit.destroy === "function") toolkit.destroy();
      });
      this.toolkits = [];
      if (this.sourcePdf && typeof this.sourcePdf.destroy === "function") this.sourcePdf.destroy();
      this.sourcePdf = null;
      this.cards = {};
      this.results.replaceChildren();
      this.original.replaceChildren();
      this.section.hidden = true;
    }

    async showOriginal(file, generation) {
      var url = URL.createObjectURL(file);
      this.urls.push(url);
      if (!/\.pdf$/i.test(file.name)) {
        var image = element("img", "comparison-original-image");
        image.src = url;
        image.alt = "Original upload: " + file.name;
        this.original.appendChild(image);
        return;
      }
      var link = element("a", "comparison-download", "Download original PDF");
      link.href = url;
      link.download = file.name;
      this.original.appendChild(link);
      if (typeof pdfjsLib === "undefined") return;
      try {
        var pdf = await pdfjsLib.getDocument(url).promise;
        if (generation !== this.generation) { pdf.destroy(); return; }
        this.sourcePdf = pdf;
        var canvas = element("canvas", "comparison-original-canvas");
        var nav = this.navigation("Original PDF", pdf.numPages);
        this.original.appendChild(nav.container);
        this.original.appendChild(canvas);
        var draw = async function (number) {
          nav.previous.disabled = nav.next.disabled = true;
          try {
            var page = await pdf.getPage(number);
            if (generation !== this.generation) return;
            var natural = page.getViewport({ scale: 1 });
            var viewport = page.getViewport({ scale: 1200 / natural.width });
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            await page.render({ canvasContext: canvas.getContext("2d"), viewport: viewport }).promise;
            nav.update(number);
          } catch (error) {
            if (generation === this.generation) nav.label.textContent = "PDF preview unavailable; use the download.";
          }
        }.bind(this);
        nav.onChange = draw;
        await draw(1);
      } catch (error) {
        if (generation === this.generation) this.original.appendChild(element("p", "comparison-error", "Original PDF preview unavailable; use the download."));
      }
    }

    showPending(file) {
      this.clear();
      this.file = file;
      this.section.hidden = false;
      ["audiveris", "homr"].forEach(function (name) {
        var card = element("article", "comparison-card");
        var header = element("header", "score-header");
        header.appendChild(element("h3", "", (name === "homr" ? "HOMR" : "Audiveris") + " result"));
        var status = element("p", "comparison-engine-status", "Waiting…");
        status.setAttribute("role", "status");
        header.appendChild(status);
        card.appendChild(header);
        this.results.appendChild(card);
        this.cards[name] = { root: card, header: header, status: status };
      }, this);
      this.showOriginal(file, this.generation);
    }

    updateProgress(engines) {
      Object.keys(this.cards).forEach(function (name) {
        var entry = engines[name];
        if (entry) this.cards[name].status.textContent = entry.status === "error"
          ? "Recognition failed" : entry.message || entry.status;
      }, this);
    }

    fail(message) {
      Object.keys(this.cards).forEach(function (name) {
        this.cards[name].status.textContent = "Comparison interrupted: " + message;
      }, this);
    }

    cardForResult(data) {
      return Object.values(this.cards).find(function (card) { return card.data === data; });
    }

    playbackSettings(data) {
      var card = this.cardForResult(data);
      return card && card.player ? { tempo: Number(card.player.tempo.value), position: card.position || 0 } : null;
    }

    playbackError(data, message) {
      var card = this.cardForResult(data);
      if (card && card.player) card.player.status.textContent = "Playback failed: " + message;
    }

    addPlayer(card, name, data) {
      card.data = data;
      card.position = 0;
      card.duration = 0;
      var player = { root: element("div", "score-player"), available: !!data.midi_base64 };
      card.player = player;
      var engineLabel = name === "homr" ? "HOMR" : "Audiveris";
      player.root.setAttribute("role", "group");
      player.root.setAttribute("aria-label", engineLabel + " playback controls");
      var buttons = element("div", "score-player-buttons");
      ["play", "pause", "stop"].forEach(function (action) {
        var label = action.charAt(0).toUpperCase() + action.slice(1);
        var button = element("button", "btn btn-compact", label);
        button.type = "button";
        button.setAttribute("aria-label", label + " " + engineLabel);
        button.disabled = !player.available || action !== "play";
        button.addEventListener("click", function () { this.onPlayback(action, data, this.file); }.bind(this));
        player[action] = button;
        buttons.appendChild(button);
      }, this);
      player.root.appendChild(buttons);
      var tempoLabel = element("label", "score-player-tempo", "Tempo ");
      player.tempo = element("input");
      player.tempo.type = "range";
      player.tempo.min = "0.5";
      player.tempo.max = "2";
      player.tempo.step = "0.1";
      player.tempo.value = "1";
      player.tempo.disabled = !player.available;
      player.tempo.setAttribute("aria-label", engineLabel + " tempo");
      player.tempoValue = element("span", "", "1.0×");
      player.tempo.addEventListener("input", function () {
        player.tempoValue.textContent = Number(player.tempo.value).toFixed(1) + "×";
        this.onPlayback("tempo", data, this.file, Number(player.tempo.value));
      }.bind(this));
      tempoLabel.appendChild(player.tempo);
      tempoLabel.appendChild(player.tempoValue);
      player.root.appendChild(tempoLabel);
      player.seek = element("input", "score-player-seek");
      player.seek.type = "range";
      player.seek.min = "0";
      player.seek.max = "0";
      player.seek.step = "0.1";
      player.seek.value = "0";
      player.seek.disabled = true;
      player.seek.setAttribute("aria-label", engineLabel + " playback position");
      player.seek.addEventListener("input", function () {
        card.position = Number(player.seek.value);
        player.time.textContent = formatTime(card.position) + " / " + formatTime(card.duration);
        this.onPlayback("seek", data, this.file, card.position);
      }.bind(this));
      player.root.appendChild(player.seek);
      var info = element("div", "score-player-info");
      player.status = element("span", "", player.available ? "Ready to play" : "Playback unavailable");
      player.status.setAttribute("role", "status");
      player.time = element("span", "", "0:00 / 0:00");
      info.appendChild(player.status);
      info.appendChild(player.time);
      player.root.appendChild(info);
      card.header.appendChild(player.root);
    }

    updatePlayback(activeData, state) {
      Object.values(this.cards).forEach(function (card) {
        var player = card.player;
        if (!player) return;
        var active = card.data === activeData;
        if (active && !state.loading) {
          card.position = state.position;
          card.duration = state.duration;
          player.tempo.value = String(state.tempo);
          player.tempoValue.textContent = state.tempo.toFixed(1) + "×";
        }
        player.play.disabled = !player.available || state.loading || (active && state.playing);
        player.pause.disabled = state.loading || !active || !state.playing;
        player.stop.disabled = state.loading || !active || (!state.playing && card.position === 0);
        player.tempo.disabled = !player.available || state.loading;
        player.seek.disabled = !player.available || state.loading || card.duration <= 0;
        player.seek.max = String(card.duration);
        player.seek.value = String(card.position);
        player.time.textContent = formatTime(card.position) + " / " + formatTime(card.duration);
        player.status.textContent = !player.available ? "Playback unavailable"
          : active && state.loading ? "Loading sound…"
          : active && state.playing ? "Playing"
          : card.position > 0 ? "Paused" : "Ready to play";
        this.highlightPlayback(card, active && !state.loading ? state.position : null, active && state.playing);
      }, this);
    }

    highlightPlayback(card, position, followPage) {
      if (!card.score || !card.toolkit) return;
      card.score.querySelectorAll(".playing-note").forEach(function (note) { note.classList.remove("playing-note"); });
      if (position == null || position === 0 || typeof card.toolkit.getElementsAtTime !== "function") return;
      try {
        var elements = card.toolkit.getElementsAtTime(position * 1000);
        if (followPage && elements.page > 0 && elements.page !== card.nav.page) card.draw(elements.page);
        var ids = new Set(elements.notes || []);
        card.score.querySelectorAll("g.note").forEach(function (note) {
          if (ids.has(note.id)) note.classList.add("playing-note");
        });
      } catch (error) { /* A notation timing failure must not interrupt audio. */ }
    }

    navigation(label, total) {
      var nav = { container: element("div", "comparison-page-nav"), page: 1 };
      nav.previous = element("button", "btn btn-compact", "Previous");
      nav.next = element("button", "btn btn-compact", "Next");
      nav.label = element("span", "");
      [nav.previous, nav.next].forEach(function (button) { button.type = "button"; });
      nav.previous.setAttribute("aria-label", label + " previous page");
      nav.next.setAttribute("aria-label", label + " next page");
      nav.container.appendChild(nav.previous);
      nav.container.appendChild(nav.label);
      nav.container.appendChild(nav.next);
      nav.container.hidden = total <= 1;
      nav.update = function (page) {
        nav.page = page;
        nav.label.textContent = "Page " + page + " of " + total;
        nav.previous.disabled = page <= 1;
        nav.next.disabled = page >= total;
      };
      nav.previous.addEventListener("click", function () { if (nav.page > 1) nav.onChange(nav.page - 1); });
      nav.next.addEventListener("click", function () { if (nav.page < total) nav.onChange(nav.page + 1); });
      nav.update(1);
      return nav;
    }

    renderEngine(name, entry) {
      var card = this.cards[name];
      if (!entry || entry.status !== "complete" || !entry.result) {
        card.status.textContent = "Recognition failed";
        card.root.appendChild(element("p", "comparison-error", entry && entry.error || "No result returned."));
        return;
      }
      var data = entry.result;
      var report = data.recognition_report || {};
      card.status.textContent = "Ready · " + (report.measure_count == null ? "?" : report.measure_count)
        + " measures · " + (report.pitched_note_count == null ? "?" : report.pitched_note_count) + " noteheads";
      var actions = element("div", "comparison-actions");
      var bytes = bytesFromBase64(data.musicxml_base64);
      var url = URL.createObjectURL(new Blob([bytes], { type: data.musicxml_format === "mxl"
        ? "application/vnd.recordare.musicxml" : "application/vnd.recordare.musicxml+xml" }));
      this.urls.push(url);
      var download = element("a", "comparison-download", "Download MusicXML");
      download.href = url;
      download.download = this.file.name.replace(/\.[^.]+$/, "") + "-" + name + (data.musicxml_format === "mxl" ? ".mxl" : ".musicxml");
      actions.appendChild(download);
      card.header.appendChild(actions);
      this.addPlayer(card, name, data);
      if (data.playback_error) card.root.appendChild(element("p", "comparison-error", "Playback unavailable: " + data.playback_error));
      var sourceLayout = report.source_layout;
      var messages = (report.export_warnings || []).concat(sourceLayout ? sourceLayout.warnings || [] : [])
        .concat((report.issues || []).map(function (issue) { return issue.message; }));
      var details = element("details", "comparison-warnings");
      details.appendChild(element("summary", "", messages.length ? "Review warnings (" + messages.length + ")" : "Automatic transcription — unverified"));
      details.appendChild(element("p", "", sourceLayout && sourceLayout.preserved
        ? "Source layout preserved. Compare notes against the original; this is not an accuracy guarantee."
        : "This engine's own layout is shown; alignment with the original is not verified."));
      messages.forEach(function (message) { details.appendChild(element("p", "", message)); });
      card.root.appendChild(details);
      try {
        if (typeof verovio === "undefined") throw new Error("Verovio did not load. Check network access and refresh.");
        var toolkit = new verovio.toolkit();
        this.toolkits.push(toolkit);
        toolkit.setOptions({ pageWidth: 2400, pageHeight: 3400, scale: 60,
          breaks: (sourceLayout && sourceLayout.preserved) || report.has_encoded_breaks ? "encoded" : "auto",
          condense: "none", adjustPageHeight: true, breaksNoWidow: false, systemMaxPerPage: 0,
          header: "none", footer: "none" });
        var loaded = data.musicxml_format === "mxl" ? toolkit.loadZipDataBuffer(bytes.buffer)
          : toolkit.loadData(new TextDecoder().decode(bytes));
        if (loaded === false || toolkit.getPageCount() < 1) throw new Error("Could not render this engine's MusicXML.");
        var nav = this.navigation(name, toolkit.getPageCount());
        var score = element("div", "comparison-score");
        var draw = function (page) { score.innerHTML = toolkit.renderToSVG(page); nav.update(page); };
        card.toolkit = toolkit;
        card.score = score;
        card.nav = nav;
        card.draw = draw;
        // Generate the timing map for this score's own note IDs and pages.
        try {
          var midi = toolkit.renderToMIDI();
          if (typeof Midi !== "undefined" && midi) card.duration = new Midi(bytesFromBase64(midi).buffer).duration;
        } catch (error) { /* Playback can still use the backend MIDI. */ }
        card.player.seek.max = String(card.duration);
        card.player.seek.disabled = !card.player.available || card.duration <= 0;
        card.player.time.textContent = "0:00 / " + formatTime(card.duration);
        nav.onChange = draw;
        card.root.appendChild(nav.container);
        card.root.appendChild(score);
        draw(1);
      } catch (error) {
        card.root.appendChild(element("p", "comparison-error", "Preview unavailable: " + error.message + " Download the MusicXML to inspect it."));
      }
    }

    async show(result) {
      var generation = this.generation;
      await this.ready;
      if (generation !== this.generation) return;
      ["audiveris", "homr"].forEach(function (name) { this.renderEngine(name, result.engines[name]); }, this);
    }
  }

  window.RecognitionComparison = RecognitionComparison;
})();
