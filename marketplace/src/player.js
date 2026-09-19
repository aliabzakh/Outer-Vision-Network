// Plays a song in the browser with the same ElevenLabs samples tools/synth.py uses, repitched per note.
let ctx;
const buffers = {};
let meta;

const freq = (midi) => 440 * 2 ** ((midi - 69) / 12);

async function sample(name) {
  if (!buffers[name]) {
    buffers[name] = fetch(`/samples/${name}.wav`).then((r) => {
      if (!r.ok) throw new Error(name);
      return r.arrayBuffer();
    }).then((b) => ctx.decodeAudioData(b)).catch(() => null);
  }
  return buffers[name];
}

function beep(t, f, vol) {                      // fallback when a sample is missing
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.frequency.value = f;
  g.gain.setValueAtTime(0.25 * vol, t);
  g.gain.exponentialRampToValueAtTime(0.001, t + 0.8);
  o.connect(g).connect(ctx.destination);
  o.start(t);
  o.stop(t + 0.8);
}

/** Schedules every note; returns a stop() function. onNote(i) fires as each note sounds. */
export async function play(song, onNote) {
  ctx = ctx || new AudioContext();
  await ctx.resume();
  meta = meta || (await fetch('/samples/samples.json').then((r) => r.json()).catch(() => ({})));
  const names = [...new Set(song.notes.map((n) => n.instrument))];
  const bufs = Object.fromEntries(await Promise.all(names.map(async (n) => [n, await sample(n)])));
  const start = ctx.currentTime + 0.15;
  const sources = [];
  const timers = song.notes.map((n, i) => {
    const t = start + n.t;
    const vol = n.volume ?? 1;
    const buf = bufs[n.instrument];
    if (buf) {
      const s = ctx.createBufferSource();
      const g = ctx.createGain();
      s.buffer = buf;
      const f0 = meta[n.instrument]?.f0;
      if (f0 && n.midi) s.playbackRate.value = freq(n.midi) / f0;
      g.gain.value = 0.9 * vol;
      s.connect(g).connect(ctx.destination);
      s.start(t);
      s.stop(t + 1.6);
      sources.push(s);
    } else if (n.midi) {
      beep(t, freq(n.midi), vol);
    }
    return setTimeout(() => onNote?.(i), (t - ctx.currentTime) * 1000);
  });
  const end = setTimeout(() => onNote?.(-1), (song.duration_s + 1) * 1000);
  return () => {
    timers.forEach(clearTimeout);
    clearTimeout(end);
    sources.forEach((s) => { try { s.stop(); } catch { /* already done */ } });
    onNote?.(-1);
  };
}
