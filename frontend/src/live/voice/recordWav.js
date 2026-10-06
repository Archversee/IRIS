// Push-to-talk recording for MAVIS's /voice/ptt (Whisper wants 16 kHz mono
// WAV). Ported from the original MAVIS page's recordMicWav()/encodeWav().

function encodeWav(samples, sampleRate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, n * 2, true);
  let o = 44;
  for (let i = 0; i < n; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

/**
 * Record from the mic until `stopSignal` resolves (or maxMs passes).
 * Resolves {blob, peak, durationMs}; rejects with err.code mic_denied |
 * mic_error | mic_insecure (browsers only allow the mic on https/localhost).
 */
export function recordMicWav({ maxMs = 20000, stopSignal = null } = {}) {
  return new Promise(async (resolve, reject) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      reject(Object.assign(new Error("mic"), { code: "mic_insecure" }));
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch (e) {
      reject(Object.assign(new Error("mic"), { code: e?.name === "NotAllowedError" ? "mic_denied" : "mic_error" }));
      return;
    }
    // Ask for 16 kHz (Whisper's rate) but fall back to the device rate:
    // Firefox refuses to connect a mic stream to a context at another rate.
    const AC = window.AudioContext || window.webkitAudioContext;
    let ctx, src;
    try {
      ctx = new AC({ sampleRate: 16000 });
      src = ctx.createMediaStreamSource(stream);
    } catch {
      try { ctx?.close(); } catch { /* fine */ }
      try {
        ctx = new AC();
        src = ctx.createMediaStreamSource(stream);
      } catch {
        stream.getTracks().forEach((t) => t.stop());
        reject(Object.assign(new Error("mic"), { code: "mic_error" }));
        return;
      }
    }
    try { await ctx.resume(); } catch { /* fine */ }
    const sr = ctx.sampleRate || 16000;
    const chunks = [];
    let peak = 0;
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (ev) => {
      const input = ev.inputBuffer.getChannelData(0);
      chunks.push(new Float32Array(input));
      let s = 0;
      for (let i = 0; i < input.length; i++) s += input[i] * input[i];
      peak = Math.max(peak, Math.sqrt(s / Math.max(1, input.length)));
    };
    const mute = ctx.createGain();
    mute.gain.value = 0;
    src.connect(proc); proc.connect(mute); mute.connect(ctx.destination);

    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      try { proc.disconnect(); src.disconnect(); } catch { /* fine */ }
      stream.getTracks().forEach((t) => t.stop());
      const total = chunks.reduce((n, c) => n + c.length, 0);
      let out = new Float32Array(total);
      let o = 0;
      for (const c of chunks) { out.set(c, o); o += c.length; }
      let outSr = sr;
      if (Math.abs(sr - 16000) > 50) {          // resample toward 16 kHz
        const nOut = Math.max(1, Math.round(out.length * 16000 / sr));
        const res = new Float32Array(nOut);
        for (let i = 0; i < nOut; i++) {
          const x = i * (out.length - 1) / Math.max(1, nOut - 1);
          const i0 = Math.floor(x), i1 = Math.min(out.length - 1, i0 + 1);
          res[i] = out[i0] * (1 - (x - i0)) + out[i1] * (x - i0);
        }
        out = res;
        outSr = 16000;
      }
      ctx.close().catch(() => {});
      resolve({ blob: encodeWav(out, outSr), peak, durationMs: (out.length / outSr) * 1000 });
    };
    const timer = setTimeout(finish, maxMs);
    stopSignal?.then(() => { clearTimeout(timer); finish(); }).catch(() => {});
  });
}

export const VOICE_REASONS = {
  too_short: "That was too short. Hold the button while you speak.",
  silence: "Couldn't hear you. Speak a little louder and try again.",
  bad_audio: "Couldn't read the recording. Check the microphone and try again.",
  speaker_reject: "Voice not recognised. Only the enrolled trainee can ask by voice.",
  empty: "Couldn't understand that. Try again, a little clearer.",
  chat_disabled: "Chat is disabled in this mode.",
  stt_unavailable: "Speech recognition isn't ready yet. Give it a minute after MAVIS starts.",
  stt_timeout: "Speech recognition took too long. Try again in a moment.",
  mic_denied: "Microphone permission denied. Allow the mic in the browser, then try again.",
  mic_error: "Couldn't open the microphone.",
  mic_insecure: "The browser only allows the microphone on https or localhost. Open IRIS via localhost on this PC.",
};
