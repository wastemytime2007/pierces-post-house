// One quiet sound for the two moments worth hearing from another room: a batch finished, and a trimmed XML came back.
// Off until the person turns it on in Settings, because a batch can end at 3 a.m. (Things rewards finishing with a sound; this one asks first.)
// Synthesised, so there is no audio file to ship and nothing to load before it plays.

const TONES = {
  finished: [523.25, 659.25, 783.99],       // a major triad, rising: the work is done
  arrived: [659.25, 523.25],                // two notes falling: something came in
};

export function soundsOn() {
  try { return localStorage.getItem("ph.sounds") === "on"; } catch (e) { return false; }
}

export function chime(kind = "finished") {
  if (!soundsOn()) return;
  const notes = TONES[kind] || TONES.finished;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const now = ctx.currentTime;
    notes.forEach((hz, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = hz;
      const at = now + i * 0.085;
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(0.055, at + 0.012);            // quiet: a note, not an alert
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.42);
      osc.connect(gain).connect(ctx.destination);
      osc.start(at);
      osc.stop(at + 0.45);
    });
    setTimeout(() => ctx.close().catch(() => {}), 1200);
  } catch (e) { /* no audio on this machine: a sound is a courtesy, never a failure */ }
}
