/* Dependency-free behavioral smoke test. No browser or network is used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function element() {
  const classes = new Set();
  return {
    hidden: true, style: {}, dataset: {}, children: [], events: {}, value: '1', textContent: '',
    classList: { toggle() {}, add(name) { classes.add(name); }, remove(name) { classes.delete(name); }, contains(name) { return classes.has(name); } },
    addEventListener(name, callback) { this.events[name] = callback; },
    setAttribute(key, value) { this[key] = value; },
    removeAttribute(key) { delete this[key]; },
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    querySelector(selector) {
      if (selector.startsWith('#')) return scoreNodes.get(selector.slice(1)) || null;
      if (selector === '.notehead') return this.notehead || null;
      return selector === 'svg' && this.innerHTML ? {} : null;
    },
    querySelectorAll(selector) {
      return selector === 'g.note.playing, [data-playing]'
        ? [...scoreNodes.values()].filter(node => node.classList.contains('playing')) : [];
    },
    getBoundingClientRect() { return this.rect || { left: 100, top: 200, width: 800, height: 1000, bottom: 1200 }; },
    getContext() { return { clearRect() {} }; },
  };
}
const scoreNodes = new Map();
const windowEvents = {};
const documentEvents = {};
let elementsAtTime = () => ({ page: 1, notes: [] });
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(match => match[1]));
for (const removed of ['engineSelector', 'engineAudiveris', 'engineHomr', 'viewToggle', 'sourceImage', 'pdfContainer', 'comparisonSection', 'trackName', 'recognitionEngine']) {
assert.ok(!ids.has(removed), removed + ' is removed from the page');
}
assert.doesNotMatch(html, /comparison\.js|pdf\.min\.js/);
assert.doesNotMatch(html, /score-editor-heading|score-editor-help|<span>Selected measure<\/span>/);
assert.match(html, /data-tooltip="Click a measure in the score or type its number/);
assert.match(html, /id="reseekBtn"[^>]*>Reseek to \.\.\.<\/button>/);
assert.match(html, /Tap Reseek, then tap the score\. Playback pauses automatically\./);
const elements = new Map();
const get = id => {
  if (!ids.has(id)) return null;
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
};
const options = [];
const blobs = [];
const revoked = [];
let rendererFails = false;
let midiFails = false;
let pageCount = 2;
const operations = [];
const midiInputs = [];
let now = 0;
const sounds = { stops: 0, played: [], stop() { this.stops++; }, play(...args) { this.played.push(args); } };
const audioContexts = [];
class TestAudioContext {
  constructor() {
    this.state = 'running';
    this.resumeCalls = 0;
    this.suspendCalls = 0;
    audioContexts.push(this);
  }
  resume() {
    this.resumeCalls++;
    this.state = 'running';
    return Promise.resolve();
  }
  suspend() {
    this.suspendCalls++;
    this.state = 'suspended';
    return Promise.resolve();
  }
}
class Toolkit {
  setOptions(value) { options.push(value); operations.push('options'); }
  loadData() { operations.push('load'); return !rendererFails; }
  loadZipDataBuffer() { operations.push('load'); }
  redoLayout() {}
  renderToMIDI() { return midiFails ? null : 'AQ=='; }
  renderToSVG() { return '<svg><g class="note"/></svg>'; }
  getPageCount() { return pageCount; }
  getElementsAtTime(ms) { return elementsAtTime(ms); }
  getTimeForElement(id) { return scoreNodes.get(id)?.onset || 0; }
  getElementAttr(id) { return id === 'measure-71' ? { n: '71' } : {}; }
}
const context = {
  document: { hidden: false, getElementById: get, createElement: element,
    addEventListener(name, callback) { documentEvents[name] = callback; },
    removeEventListener(name, callback) { if (documentEvents[name] === callback) delete documentEvents[name]; },
    querySelectorAll() { return []; } },
  window: { location: { protocol: 'http:', origin: 'http://test.invalid' }, innerHeight: 700,
            addEventListener(name, callback) { windowEvents[name] = callback; },
            confirm() { return true; },
            AudioContext: TestAudioContext },
  Soundfont: { instrument: async () => sounds },
  verovio: { module: {}, toolkit: Toolkit },
  fetch: async () => ({ text: async () => '{"omr_homr":true}' }),
  URL: { createObjectURL: blob => { blobs.push(blob); return 'blob:test-' + blobs.length; }, revokeObjectURL(url) { revoked.push(url); } },
  Midi: class { constructor(buffer) { midiInputs.push(new Uint8Array(buffer)[0]); this.duration = 4; this.tracks = [{ notes: [{ midi: 60, time: 0, duration: 4, velocity: 0.8 }] }]; } },
  CSS: { escape: value => value }, TextDecoder, Uint8Array, Blob, FormData: class {
    constructor() { this.values = {}; }
    append(key, value) { this.values[key] = value; }
  }, console, setTimeout, clearTimeout,
  atob: s => Buffer.from(s, 'base64').toString('binary'),
  performance: { now: () => now }, requestAnimationFrame() { return 1; }, cancelAnimationFrame() {},
};
let script = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
// Expose only in the test VM, leaving the production closure unchanged.
script = script.replace(/\}\)\(\);\s*$/, `globalThis.testApi = { setupTrackFromStoredData, handleFile, loadTrack, deleteTrack, loadServerLibrary, goToPage,
  play, pause, stop, seekTo, onTempoChange, tick, setScoreEditorOpen, applyMusicXmlEdit, validateCurrentScore,
  selectMeasureForEdit, saveMusicXmlEdits, discardMusicXmlEdits, notationTimeForPlayback,
  playbackTimeForNotation,
  resetInstrument: () => { instrument = null; },
  getPlaybackState: () => ({ isPlaying, playhead, tempo, trackLoading, currentTrackId, playlist }) }; })();`);
vm.runInNewContext(script, context);
context.verovio.module.onRuntimeInitialized();

(async () => {
  let pickerOpened = 0;
  get('fileInput').click = () => { pickerOpened++; };
  get('dropzone').closest = () => null;
  get('dropzone').events.click({ target: get('dropzone') });
  get('browseBtn').events.click({ stopPropagation() {} });
  assert.equal(pickerOpened, 2);
  const track = {
    engine: 'homr', filename: 'score', file: { name: 'score.png' },
    midiBase64: 'AA==', musicxmlBase64: 'PHNjb3JlLz4=', musicxmlFormat: 'xml',
    systemRegions: [[1, 4], [5, 8]],
    recognitionReport: { has_encoded_breaks: true, notice: 'Review the transcription',
      issues: [{ measure: '8', message: '<check tuplet>' }], export_warnings: [],
      source_layout: { preserved: true, warnings: [], pages: [{ width: 1200, height: 1500 }],
        systems: [{ staff_count: 2, measure_count: 4 }, { staff_count: 2, measure_count: 3 }] } },
  };
  await context.testApi.setupTrackFromStoredData(track);
  assert.equal(midiInputs.at(-1), 0, 'Backend MIDI is preferred because it expands repeat playback');
  const exportedMidiBytes = new Uint8Array(await blobs.at(-1).arrayBuffer());
  assert.equal(blobs.at(-1).type, 'audio/midi');
  assert.equal(exportedMidiBytes[0], 0, 'MIDI download uses the repeat-expanded backend payload');
  assert.equal(get('engineAvailability').textContent, 'Recognition: HOMR · ready');
  assert.equal(options.at(-1).breaks, 'encoded');
  assert.equal(options.at(-1).condense, 'none');
  assert.equal(options.at(-1).pageHeight, 2625);
  assert.equal(operations[operations.indexOf('load') - 1], 'options');
  assert.match(get('recognitionNotice').textContent, /2 systems/);
  assert.equal(get('recognitionIssues').children[0].textContent, '<check tuplet>');
  assert.equal(get('notationViewport').hidden, false);
  assert.equal(get('verovioNotation').hidden, false);
  assert.equal(get('musicxmlDownload').hidden, false);
  assert.equal(get('musicxmlDownload').download, 'score.musicxml');
  assert.equal(get('midiDownload').hidden, false);
  assert.equal(get('midiDownload').download, 'score.mid');
  assert.equal(get('notationTitle').textContent, 'score.png');
  assert.ok(blobs.every(blob => blob instanceof Blob), 'Only generated download data gets blob URLs, never the original file');
  assert.equal(get('pageNavSection').hidden, false);
  context.testApi.goToPage(2);
  assert.equal(get('pageNavText').textContent, 'Page 2 of 2');
  assert.equal(get('nextPageBtn').disabled, true);
  pageCount = 1;
  track.file.name = 'score.pdf';
  await context.testApi.setupTrackFromStoredData(track);
  assert.equal(get('notationViewport').hidden, false, 'PDF uploads display recognized MusicXML, not a PDF canvas');
  assert.equal(get('pageNavSection').hidden, true);
  assert.ok(revoked.length > 0, 'Previous download URL is released');
  track.recognitionReport.has_encoded_breaks = false;
  delete track.recognitionReport.source_layout;
  track.systemRegions = [];
  await context.testApi.setupTrackFromStoredData(track);
  assert.equal(options.at(-1).breaks, 'auto');
  rendererFails = true;
  await context.testApi.setupTrackFromStoredData(track);
  assert.equal(get('notationViewport').hidden, true);
  assert.equal(get('notationMessage').hidden, false);
  assert.match(get('notationMessage').textContent, /Recognition preview unavailable/);
  assert.equal(get('musicxmlDownload').hidden, false, 'Render failure retains the MusicXML download');
  rendererFails = false;
  midiFails = true;
  await context.testApi.setupTrackFromStoredData({ ...track, midiBase64: '', playbackError: 'Bad rhythm' });
  assert.equal(get('verovioNotation').hidden, false);
  assert.equal(get('playBtn').disabled, true);
  assert.equal(get('musicxmlDownload').hidden, false);
  assert.equal(get('errorBox').textContent, 'Bad rhythm');
  midiFails = false;

  const state = context.testApi.getPlaybackState;
  let submittedEngines = [];
  let result = { success: true, engine: 'homr', musicxml_base64: 'PHNjb3JlLz4=', musicxml_format: 'xml', midi_base64: 'AA==' };
  context.fetch = async (url, request) => {
    if (url.endsWith('/upload')) {
      submittedEngines.push(request.body.values.engine);
      return { ok: true, text: async () => '{"job_id":"homr-job"}' };
    }
    return { ok: true, json: async () => ({ status: 'complete', result }) };
  };
  for (const name of ['first.png', 'second.pdf']) {
    await context.testApi.handleFile({ name });
    assert.equal(submittedEngines.at(-1), 'homr', 'Every upload explicitly selects HOMR');
    assert.equal(get('playerSection').hidden, false);
    assert.equal(get('notationTitle').textContent, name);
    assert.equal(state().currentTrackId, state().playlist.at(-1).id, 'New upload displays its own result');
    assert.equal(get('fileInput').disabled, false);
  }
  assert.equal(state().playlist.length, 2);
  await context.testApi.loadTrack(state().playlist[0].id);
  assert.equal(get('notationTitle').textContent, 'first.png');
  context.testApi.play();
  now = 1000;
  context.testApi.tick();
  assert.equal(state().isPlaying, true, 'Final sustained note is allowed to finish');
  assert.equal(state().playhead, 1);
  context.testApi.pause();
  assert.equal(state().isPlaying, false);

  // Locking an iPad suspends Web Audio and requestAnimationFrame. The app must
  // preserve the playhead instead of counting the sleeping interval, then
  // recover the context from the next explicit Play gesture.
  context.testApi.play();
  context.document.hidden = true;
  documentEvents.visibilitychange();
  assert.equal(state().isPlaying, false, 'Screen lock pauses playback');
  const beforeSleep = state().playhead;
  now += 60000;
  context.document.hidden = false;
  documentEvents.visibilitychange();
  await context.testApi.play();
  assert.equal(audioContexts[0].suspendCalls, 1, 'Wake recovery cycles the audio context');
  assert.equal(audioContexts[0].resumeCalls, 1, 'Wake recovery resumes audio before playback');
  now += 500;
  context.testApi.tick();
  assert.ok(Math.abs(state().playhead - (beforeSleep + 0.5)) < 0.001,
    'Time spent asleep is not counted as playback');
  context.testApi.pause();

  context.testApi.seekTo(2);
  get('tempoSlider').value = '1.5';
  context.testApi.onTempoChange();
  context.testApi.play();
  now += 400;
  context.testApi.tick();
  assert.ok(Math.abs(state().playhead - 2.6) < 0.001);
  get('tempoSlider').value = '2';
  context.testApi.onTempoChange();
  now += 200;
  context.testApi.tick();
  assert.ok(Math.abs(state().playhead - 3) < 0.001, 'Tempo changes do not jump the playhead');
  now += 600;
  context.testApi.tick();
  assert.equal(state().isPlaying, false);
  context.testApi.stop();
  assert.equal(state().playhead, 0);
  context.testApi.resetInstrument();
  context.Soundfont.instrument = async () => { throw new Error('Sound unavailable'); };
  await context.testApi.loadTrack(state().playlist[0].id);
  assert.equal(get('verovioNotation').hidden, false, 'Audio failure cannot hide the score');
  assert.match(get('errorBox').textContent, /Playback unavailable/);
  context.Soundfont.instrument = async () => sounds;
  await context.testApi.loadTrack(state().playlist[0].id);
  assert.equal(get('playBtn').disabled, false);

  // A server-backed score exposes the focused correction strip and persists
  // a repeat edit before refreshing notation and semantic warnings.
  state().playlist[0].libraryId = 'shared-score';
  state().playlist[0].measureNumbers = ['71', '102'];
  await context.testApi.setupTrackFromStoredData(state().playlist[0]);
  assert.equal(get('scoreEditBtn').hidden, false);
  context.testApi.setScoreEditorOpen(true);
  assert.equal(get('scoreEditor').hidden, false);
  get('scoreEditor').rect = { left: 100, top: 100, width: 600, height: 45, bottom: 145 };
  get('scoreEditorDragHandle').events.pointerdown({ button: 0, clientX: 110, clientY: 110, preventDefault() {} });
  documentEvents.pointermove({ clientX: 310, clientY: 260, preventDefault() {} });
  documentEvents.pointerup({});
  assert.equal(get('scoreEditor').style.left, '300px', 'The correction toolbar can be dragged horizontally');
  assert.equal(get('scoreEditor').style.top, '250px', 'The correction toolbar can be dragged away from covered measures');
  get('scoreEditor').rect = { left: 300, top: 250, width: 600, height: 45, bottom: 295 };
  const selectedMeasure = element();
  selectedMeasure.id = 'measure-71';
  selectedMeasure.closest = () => selectedMeasure;
  context.testApi.selectMeasureForEdit({ target: selectedMeasure });
  assert.equal(get('editMeasure').value, '71');
  assert.equal(get('scoreEditor').style.left, '200px', 'Selecting a measure reanchors the correction toolbar once');
  assert.equal(get('scoreEditor').style.top, '145px');
  windowEvents.scroll();
  assert.equal(get('scoreEditor').style.left, '200px', 'Scrolling does not override the anchored or dragged position');
  let editRequest;
  context.fetch = async (url, request) => {
    editRequest = { url, request };
    return { ok: true, text: async () => JSON.stringify({
      success: true, musicxml_base64: 'PHNjb3JlLz4=', musicxml_format: 'xml', edit_changed: 1,
    }) };
  };
  assert.equal(await context.testApi.applyMusicXmlEdit('add_forward_repeat'), true);
  assert.match(editRequest.url, /\/library\/shared-score\/edit-preview$/);
  assert.deepEqual(JSON.parse(editRequest.request.body), { edits: [{ measure: '71', action: 'add_forward_repeat' }] });
  assert.match(get('scoreEditorStatus').textContent, /Preview updated/);
  assert.equal(get('scoreEditorSave').disabled, false);
  assert.equal(get('scoreEditor').hidden, false);
  assert.equal(await context.testApi.applyMusicXmlEdit('remove_forward_repeat'), true);
  assert.deepEqual(JSON.parse(editRequest.request.body), {
    edits: [{ measure: '71', action: 'remove_forward_repeat' }],
  }, 'The remove replaces the staged add for the same repeat marker');
  assert.equal(await context.testApi.applyMusicXmlEdit('add_forward_repeat'), true);
  assert.deepEqual(JSON.parse(editRequest.request.body), {
    edits: [{ measure: '71', action: 'add_forward_repeat' }],
  }, 'The forward repeat can be added again without replaying its inverse edits');
  get('editMeasure').value = '102';
  get('editMeasure').events.input();
  assert.equal(get('scoreEditorStatus').textContent, 'Measure 102 selected.');
  get('editEndingNumber').value = '1';
  assert.equal(await context.testApi.applyMusicXmlEdit('add_ending_start'), true);
  assert.deepEqual(JSON.parse(editRequest.request.body), { edits: [
    { measure: '71', action: 'add_forward_repeat' },
    { measure: '102', action: 'add_ending_start', ending_number: '1' },
  ] });
  assert.match(get('scoreEditorStatus').textContent, /Select its last measure and close the bracket/);
  assert.equal(get('scoreEditorSave').disabled, true, 'An incomplete ending cannot be saved');
  assert.equal(await context.testApi.applyMusicXmlEdit('add_ending_stop'), true);
  assert.deepEqual(JSON.parse(editRequest.request.body), { edits: [
    { measure: '71', action: 'add_forward_repeat' },
    { measure: '102', action: 'add_ending_start', ending_number: '1' },
    { measure: '102', action: 'add_ending_stop', ending_number: '1' },
  ] });
  assert.equal(get('scoreEditorSave').disabled, false);
  context.fetch = async (url, request) => {
    editRequest = { url, request };
    return { ok: true, text: async () => JSON.stringify({
      success: true, library_id: 'shared-score', filename: 'first.png', engine: 'homr',
      musicxml_base64: 'PHNjb3JlLz4=', musicxml_format: 'xml', midi_base64: 'AA==',
      recognition_report: { issues: [], issue_counts: {} }, edit_changed: 1,
    }) };
  };
  assert.equal(await context.testApi.saveMusicXmlEdits(), true);
  assert.match(editRequest.url, /\/library\/shared-score\/edit-batch$/);
  assert.deepEqual(JSON.parse(editRequest.request.body), { edits: [
    { measure: '71', action: 'add_forward_repeat' },
    { measure: '102', action: 'add_ending_start', ending_number: '1' },
    { measure: '102', action: 'add_ending_stop', ending_number: '1' },
  ] });
  assert.equal(get('validationStatus').textContent, 'Changes saved');
  context.fetch = async (url, request) => {
    editRequest = { url, request };
    return { ok: true, text: async () => JSON.stringify({
      success: true,
      recognition_report: { issues: [{ code: 'repeat_unmatched_backward', measure: '102', message: 'Missing forward repeat' }] },
    }) };
  };
  assert.equal(await context.testApi.validateCurrentScore(), true);
  assert.match(editRequest.url, /\/library\/shared-score\/validate$/);
  assert.equal(editRequest.request.method, 'POST');
  assert.equal(get('validationStatus').textContent, '1 issue found');
  assert.equal(get('recognitionReview').open, true);
  assert.equal(get('recognitionIssues').children[0].textContent, 'Missing forward repeat');

  let rejectUpload;
  context.fetch = (url, request) => {
    assert.equal(request.body.values.engine, 'homr');
    return new Promise((resolve, reject) => { rejectUpload = reject; });
  };
  const pending = context.testApi.handleFile({ name: 'third.png' });
  assert.equal(get('browseBtn').disabled, true);
  assert.equal(get('fileInput').disabled, true);
  assert.equal(context.testApi.handleFile({ name: 'duplicate.png' }), undefined);
  rejectUpload(new Error('HOMR failed'));
  await pending;
  assert.equal(get('browseBtn').disabled, false);
  assert.match(get('errorBox').textContent, /HOMR failed/);
  assert.equal(state().playlist.length, 2, 'Recognition errors do not add a fake result');

  // Real note IDs and screen geometry: all sounding chord notes highlight,
  // but a held bass note cannot hold the cursor at the start of the measure.
  await context.testApi.loadTrack(state().playlist[0].id);
  const system = element();
  system.rect = { left: 150, top: 800, width: 500, height: 150, bottom: 950 };
  let scrolls = 0;
  system.scrollIntoView = () => { scrolls++; };
  const measure = element();
  measure.rect = { left: 150, top: 210, width: 250, height: 150, bottom: 360 };
  measure.closest = () => system;
  const secondMeasure = element();
  secondMeasure.rect = { left: 150, top: 820, width: 250, height: 150, bottom: 970 };
  secondMeasure.closest = () => system;
  function scoreNote(id, onset, x, bar = measure) {
    const node = element();
    node.onset = onset;
    node.closest = () => bar;
    node.notehead = element();
    node.notehead.rect = { left: x, top: bar.rect.top + 20, width: 10, height: 8 };
    scoreNodes.set(id, node);
    return node;
  }
  const held = scoreNote('held', 0, 160);
  const first = scoreNote('first', 0, 160);
  const next = scoreNote('next', 1000, 240);
  const later = scoreNote('later', 2000, 180, secondMeasure);
  elementsAtTime = ms => ms < 1000 ? { page: 1, notes: ['held', 'first'] }
    : ms < 1500 ? { page: 1, notes: ['held', 'next'] }
    : ms < 2000 ? { page: 1, notes: [] }
    : { page: 2, notes: ['later'] };
  state().playlist[0].measureBoundaries = [[0, 2], [2, 4]];
  state().playlist[0].playbackTimeMap = [
    { playback_start: 0, playback_end: 2, score_start: 0, score_end: 2 },
    { playback_start: 2, playback_end: 4, score_start: 2, score_end: 4 },
    { playback_start: 4, playback_end: 6, score_start: 0, score_end: 2 },
    { playback_start: 6, playback_end: 8, score_start: 4, score_end: 6 },
  ];
  assert.equal(context.testApi.notationTimeForPlayback(4.25), 0.25,
    'Cursor time jumps back to the written repeat start on the second pass');
  assert.equal(context.testApi.playbackTimeForNotation(0.5, 4.25), 0.5,
    'An ambiguous repeated score position prefers the earlier playback occurrence');
  context.testApi.seekTo(3);
  context.testApi.play();
  assert.equal(get('reseekBtn').disabled, false, 'Reseek remains available during playback');
  get('reseekBtn').events.click();
  assert.equal(state().isPlaying, false, 'Entering reseek mode pauses playback automatically');
  assert.equal(get('reseekBtn')['aria-pressed'], 'true');
  assert.equal(get('playbackCursor').hidden, false, 'Reseek mode reveals the current cursor');
  assert.equal(get('playbackCursor').classList.contains('reseek-awaiting'), true,
    'Reseek mode gives the cursor its slow-blink state');
  const reseekMeasure = element();
  reseekMeasure.id = 'measure-71';
  reseekMeasure.rect = { left: 150, top: 210, width: 250, height: 150, bottom: 360 };
  const reseekTarget = { closest(selector) { return selector === 'g.measure' ? reseekMeasure : null; } };
  get('verovioNotation').events.click({ target: reseekTarget, clientX: 212.5 });
  assert.equal(state().playhead, 0.5, 'Reseek rewinds through the repeat instead of choosing its future pass');
  assert.equal(get('reseekBtn')['aria-pressed'], 'false');
  assert.equal(get('playbackCursor').classList.contains('reseek-awaiting'), false);
  context.testApi.seekTo(0);
  assert.equal(held.classList.contains('playing'), true);
  assert.equal(first['data-playing'], '1');
  assert.equal(get('measureHighlight').style.display, 'block');
  assert.equal(get('measureHighlight').style.left, '50px');
  assert.equal(get('playbackCursor').hidden, false);
  assert.equal(get('playbackCursor').style.left, '65px');
  context.testApi.play();
  assert.equal(scrolls, 1, 'Follow an offscreen system when playback starts');
  pageCount = 2;
  const playheadBeforePageChange = state().playhead;
  context.testApi.goToPage(2);
  assert.equal(state().isPlaying, true, 'Changing notation pages does not pause playback');
  assert.equal(state().playhead, playheadBeforePageChange, 'Changing notation pages does not seek playback');
  assert.equal(get('pageNavText').textContent, 'Page 2 of 2');
  now += 1100;
  context.testApi.tick();
  assert.equal(get('pageNavText').textContent, 'Page 2 of 2', 'Manual page remains visible while playback continues elsewhere');
  assert.equal(first.classList.contains('playing'), false);
  assert.equal(first['data-playing'], undefined);
  assert.equal(held.classList.contains('playing'), false, 'Cursor notes remain hidden when playback is on another page');
  assert.equal(get('playbackCursor').hidden, true);
  assert.equal(scrolls, 1, 'Browsing another page cannot trigger playback auto-scroll');
  context.testApi.seekTo(1.1);
  assert.equal(get('pageNavText').textContent, 'Page 1 of 2', 'Seeking resumes automatic cursor page following');
  assert.equal(held.classList.contains('playing'), true);
  assert.equal(next.classList.contains('playing'), true);
  assert.equal(get('playbackCursor').style.left, '145px', 'Cursor follows the latest onset, not the held note');
  context.testApi.pause();
  assert.equal(next.classList.contains('playing'), true, 'Pause retains the current position');
  get('notationViewport').rect = { left: 120, top: 200, width: 600, height: 1000 };
  windowEvents.resize();
  assert.equal(get('playbackCursor').style.left, '125px', 'Paused overlays adapt to resized notation');
  context.testApi.seekTo(1.7);
  assert.equal(held.classList.contains('playing'), false, 'No notes remain active in a rest');
  assert.equal(get('playbackCursor').hidden, false, 'Cursor continues across rests in the current measure');
  assert.equal(get('playbackCursor').style.left, '242.5px');
  assert.equal(get('measureHighlight').style.display, 'block', 'The current bar stays indicated during a rest');
  context.testApi.seekTo(2.1);
  assert.equal(later.classList.contains('playing'), true);
  assert.equal(get('playbackCursor').style.top, '620px', 'Cursor follows the note onto the next page');
  get('tempoSlider').value = '2';
  context.testApi.onTempoChange();
  assert.equal(later.classList.contains('playing'), true);
  context.testApi.stop();
  assert.equal(get('measureHighlight').style.display, 'none');
  assert.equal(get('playbackCursor').hidden, true);
  assert.equal(later.classList.contains('playing'), false);
  windowEvents.resize();
  assert.equal(get('playbackCursor').hidden, true, 'Resize after Stop cannot resurrect the marker');
  context.testApi.seekTo(0);
  context.testApi.play();
  now += 2500;
  context.testApi.tick();
  assert.equal(state().isPlaying, false);
  assert.equal(get('playbackCursor').hidden, true, 'Natural completion clears the marker');

  // Shared-library deletion is permanent and must require explicit consent.
  const beforeDelete = state().playlist.length;
  state().playlist[0].libraryId = 'shared-score';
  context.window.confirm = () => false;
  context.testApi.deleteTrack(state().playlist[0].id);
  assert.equal(state().playlist.length, beforeDelete, 'Cancel keeps the shared score');
  let deletedUrl = null;
  context.window.confirm = () => true;
  context.fetch = async (url, request) => {
    deletedUrl = url;
    assert.equal(request.method, 'DELETE');
    return { ok: true, text: async () => '{"success":true}' };
  };
  context.testApi.deleteTrack(state().playlist[0].id);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(deletedUrl, /\/library\/shared-score$/);
  assert.equal(state().playlist.length, beforeDelete - 1, 'Confirmed delete removes the server-backed score');

  // A new client builds its playlist from the server and loads score data lazily.
  const sharedResult = {
    success: true, library_id: 'server-item', filename: 'Shared Ballad.pdf', engine: 'homr',
    musicxml_base64: 'PHNjb3JlLz4=', musicxml_format: 'xml', midi_base64: 'AA=='
  };
  context.fetch = async url => ({
    ok: true,
    text: async () => url.endsWith('/library')
      ? JSON.stringify({ items: [{ id: 'server-item', filename: 'Shared Ballad.pdf', engine: 'homr' }] })
      : JSON.stringify(sharedResult),
  });
  await context.testApi.loadServerLibrary();
  assert.equal(state().playlist.length, 1);
  assert.equal(state().playlist[0].filename, 'Shared Ballad');
  assert.equal(state().playlist[0].musicxmlBase64, null, 'Library listing remains lightweight');
  await context.testApi.loadTrack(state().playlist[0].id);
  assert.equal(state().playlist[0].musicxmlBase64, 'PHNjb3JlLz4=', 'Selecting fetches the stored score');
  console.log('PASS: visible chord highlights, latest-note cursor, rest/page transitions, pause/seek/resize/tempo, follow scrolling and cleanup');
  console.log('PASS: HOMR-only upload UI, recognized PNG/PDF view, engine provenance, layout, page navigation and downloads');
  console.log('PASS: failed rendering/MIDI/audio retain reviewable results; upload locking and playlist selection');
  console.log('PASS: play/pause/stop/seek, tempo continuity and sustained-note completion');

  // A machine with Audiveris but no HOMR must report HOMR missing, not look ready.
  context.fetch = async () => ({ text: async () => '{"omr_homr":false,"omr_audiveris":true}' });
  vm.runInNewContext(script, context);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(get('engineAvailability').textContent, 'Recognition: HOMR · unavailable');
  assert.match(get('errorBox').textContent, /HOMR is unavailable/);
  console.log('PASS: HOMR-specific availability, without fallback to another installed engine');
})().catch(error => { console.error(error); process.exitCode = 1; });
