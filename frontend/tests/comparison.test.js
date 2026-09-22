/* Independent score renderers, page controls, downloads, and partial failures. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element(tag) {
  return {
    tag, children: [], events: {}, hidden: false, disabled: false, textContent: '',
    setAttribute(key, value) { this[key] = value; },
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    addEventListener(event, handler) { this.events[event] = handler; },
    querySelectorAll() { return []; },
    click() { if (!this.disabled && this.events.click) this.events.click(); },
  };
}
const toolkits = [], revoked = [], selected = [];
let urlCounter = 0;
class Toolkit {
  constructor() { this.id = toolkits.length; this.pages = []; toolkits.push(this); }
  setOptions(options) { this.options = options; }
  loadData(xml) { this.xml = xml; return !xml.includes('broken'); }
  loadZipDataBuffer(bytes) { this.bytes = bytes; return true; }
  getPageCount() { return 2; }
  renderToSVG(page) { this.pages.push(page); return `<svg>engine-${this.id}-page-${page}</svg>`; }
  destroy() { this.destroyed = true; }
}
const context = {
  window: {}, document: { createElement: element },
  verovio: { toolkit: Toolkit }, Blob, TextDecoder, Uint8Array,
  atob: value => Buffer.from(value, 'base64').toString('binary'),
  URL: { createObjectURL: () => `blob:${++urlCounter}`, revokeObjectURL: url => revoked.push(url) },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'comparison.js'), 'utf8'), context);
const section = element('section'), results = element('div'), original = element('div');
const view = new context.window.RecognitionComparison({ section, results, original, ready: Promise.resolve(),
  onPlayback: (action, data, file, value) => selected.push({ action, data, file, value }) });
const file = { name: 'score.png' };
const result = engine => ({ status: 'complete', result: {
  engine, musicxml_base64: Buffer.from('<score/>').toString('base64'), musicxml_format: 'xml', midi_base64: 'AA==',
  recognition_report: { measure_count: 20, pitched_note_count: 180, source_layout: { preserved: engine === 'audiveris' } },
} });
const child = (card, className) => card.children.find(node => node.className === className)
  || card.children.map(node => child(node, className)).find(Boolean);

(async () => {
  view.showPending(file);
  assert.equal(section.hidden, false);
  assert.equal(original.children[0].tag, 'img');
  view.updateProgress({ audiveris: { status: 'processing', message: 'Recognizing page 1' } });
  assert.equal(view.cards.audiveris.status.textContent, 'Recognizing page 1');
  await view.show({ engines: { audiveris: result('audiveris'), homr: result('homr') } });
  assert.equal(toolkits.length, 2);
  assert.notEqual(toolkits[0], toolkits[1]);
  assert.equal(toolkits[0].options.breaks, 'encoded');
  assert.equal(toolkits[1].options.breaks, 'auto');
  const [a, h] = results.children;
  assert.equal(view.cards.audiveris.header.children[0].textContent, 'Audiveris result');
  assert.equal(view.cards.homr.header.children[0].textContent, 'HOMR result');
  assert.match(child(a, 'comparison-score').innerHTML, /engine-0-page-1/);
  assert.match(child(h, 'comparison-score').innerHTML, /engine-1-page-1/);
  child(a, 'comparison-page-nav').children[2].click();
  assert.match(child(a, 'comparison-score').innerHTML, /page-2/);
  assert.match(child(h, 'comparison-score').innerHTML, /page-1/);
  assert.equal(child(a, 'comparison-actions').children[0].download, 'score-audiveris.musicxml');
  assert.equal(child(h, 'comparison-actions').children[0].download, 'score-homr.musicxml');
  assert.equal(view.cards.homr.header.tag, 'header');
  assert.ok(view.cards.homr.header.children.includes(view.cards.homr.player.root));
  assert.equal(child(h, 'comparison-actions').children.length, 1, 'No separate Load in player action');
  view.cards.homr.player.play.click();
  assert.equal(selected[0].action, 'play');
  assert.equal(selected[0].data.engine, 'homr');
  assert.equal(selected[0].file, file);
  const data = view.cards.homr.data;
  view.updatePlayback(data, { loading: true, playing: false, position: 0, duration: 60, tempo: 1 });
  assert.equal(view.cards.audiveris.player.play.disabled, true);
  assert.equal(view.cards.homr.player.play.disabled, true);
  assert.match(view.cards.homr.player.status.textContent, /Loading/);
  view.updatePlayback(data, { loading: false, playing: true, position: 15, duration: 60, tempo: 1.5 });
  assert.equal(view.cards.homr.player.play.disabled, true);
  assert.equal(view.cards.audiveris.player.play.disabled, false);
  assert.equal(view.cards.homr.player.pause.disabled, false);
  assert.equal(view.cards.audiveris.player.pause.disabled, true);
  assert.equal(view.cards.homr.player.time.textContent, '0:15 / 1:00');
  view.cards.homr.player.pause.click();
  assert.equal(selected.at(-1).action, 'pause');
  view.cards.homr.player.tempo.value = '1.8';
  view.cards.homr.player.tempo.events.input();
  assert.equal(selected.at(-1).action, 'tempo');
  assert.equal(selected.at(-1).value, 1.8);
  view.cards.homr.player.seek.value = '30';
  view.cards.homr.player.seek.events.input();
  assert.equal(selected.at(-1).action, 'seek');
  assert.equal(selected.at(-1).value, 30);
  assert.equal(view.playbackSettings(data).position, 30);
  assert.equal(view.playbackSettings(data).tempo, 1.8);
  assert.equal(view.cards.audiveris.player.tempo.value, '1');
  view.updatePlayback(data, { loading: false, playing: false, position: 0, duration: 60, tempo: 1.8 });
  assert.equal(view.cards.homr.player.stop.disabled, true);

  view.showPending(file);
  assert.equal(revoked.length, 3);
  assert.ok(toolkits.slice(0, 2).every(t => t.destroyed));
  await view.show({ engines: { audiveris: { status: 'error', error: '<not installed>' }, homr: result('homr') } });
  assert.equal(child(results.children[0], 'comparison-error').textContent, '<not installed>');
  assert.ok(child(results.children[1], 'comparison-score'));

  view.showPending(file);
  const bad = result('audiveris');
  bad.result.musicxml_base64 = Buffer.from('<broken/>').toString('base64');
  const noMidi = result('homr');
  noMidi.result.midi_base64 = '';
  noMidi.result.playback_error = 'Invalid rhythm';
  await view.show({ engines: { audiveris: bad, homr: noMidi } });
  assert.match(child(results.children[0], 'comparison-error').textContent, /Preview unavailable/);
  assert.ok(child(results.children[1], 'comparison-score'));
  assert.equal(view.cards.homr.player.play.disabled, true);
  assert.equal(view.cards.homr.player.tempo.disabled, true);

  view.showPending(file);
  await view.show({ engines: { audiveris: { status: 'error', error: 'Failed A' }, homr: { status: 'error', error: 'Failed H' } } });
  assert.ok(results.children.every(card => child(card, 'comparison-error')));
  view.clear();
  assert.equal(section.hidden, true);
  assert.equal(results.children.length, 0);
  console.log('PASS: independent rendering/pages, header playback controls, seek/tempo callbacks, exclusive state, cleanup, partial/total failures');
})().catch(error => { console.error(error); process.exitCode = 1; });
