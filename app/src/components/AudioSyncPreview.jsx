import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";

/**
 * AudioSyncPreview — Post House Task 2.1 (Assistant Editor tab).
 *
 * PreCut's frontend has no video/audio playback surface anywhere; this is
 * the first one. Given a sync pair (A-roll proxy + external audio file +
 * the offset between them), plays both together so Ryan can actually hear
 * whether the sync PreCut computed is correct, not just read a score.
 *
 * Sync math (see precut_pipeline/audio_sync.py's SyncPair docstring):
 *   audio_time = aroll_time - offset_sec
 * The overlap between the two files starts at aroll_time = max(0, offset_sec)
 * (that's where the audio file's own t=0 lands, or 0 if the audio was
 * already rolling before the A-roll started).
 */
export default function AudioSyncPreview({ pair }) {
  const videoRef = useRef(null);
  const audioRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);
  const [muteCameraAudio, setMuteCameraAudio] = useState(true);
  const [currentTime, setCurrentTime] = useState(0);

  const offsetSec = pair?.offsetSec ?? 0;
  const videoSrc = pair?.arollProxyFull
    ? convertFileSrc(pair.arollProxyFull)
    : pair?.arollFull
      ? convertFileSrc(pair.arollFull)
      : null;
  const audioSrc = pair?.audioFull ? convertFileSrc(pair.audioFull) : null;

  const overlapStartAroll = Math.max(0, offsetSec);
  const overlapStartAudio = Math.max(0, -offsetSec);

  // Reset playback state whenever a new pair is selected.
  useEffect(() => {
    setPlaying(false);
    setReady(false);
    setError(null);
    setCurrentTime(0);
  }, [pair?.arollFull, pair?.audioFull]);

  const seekToOverlapStart = () => {
    const v = videoRef.current, a = audioRef.current;
    if (!v || !a) return;
    v.currentTime = overlapStartAroll;
    a.currentTime = overlapStartAudio;
    setCurrentTime(overlapStartAroll);
  };

  const togglePlay = async () => {
    const v = videoRef.current, a = audioRef.current;
    if (!v || !a) return;
    if (playing) {
      v.pause();
      a.pause();
      setPlaying(false);
      return;
    }
    // First play from a stopped state: jump to where both actually overlap.
    if (v.currentTime < overlapStartAroll || a.currentTime < overlapStartAudio) {
      seekToOverlapStart();
    }
    try {
      await Promise.all([v.play(), a.play()]);
      setPlaying(true);
    } catch (e) {
      setError(String(e?.message || e));
    }
  };

  // Drift correction: HTML5 media elements can't be started in perfect
  // lockstep, and one can silently fall behind the other over a long clip.
  // Re-align audio to where it should be relative to the video's own clock
  // whenever they drift past 150ms.
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      const v = videoRef.current, a = audioRef.current;
      if (!v || !a) return;
      const expectedAudioTime = v.currentTime - offsetSec;
      if (Math.abs(a.currentTime - expectedAudioTime) > 0.15) {
        a.currentTime = Math.max(0, expectedAudioTime);
      }
      setCurrentTime(v.currentTime);
    }, 400);
    return () => clearInterval(id);
  }, [playing, offsetSec]);

  const handleScrub = (e) => {
    const v = videoRef.current, a = audioRef.current;
    if (!v || !a) return;
    const t = Number(e.target.value);
    v.currentTime = t;
    a.currentTime = Math.max(0, t - offsetSec);
    setCurrentTime(t);
  };

  if (!pair) {
    return (
      <div className="ae-preview-empty">
        Click a reliable pair in the matrix to preview it here.
      </div>
    );
  }

  if (!videoSrc || !audioSrc) {
    return (
      <div className="ae-preview-empty">
        This pair is missing a file path (aroll_proxy/aroll_file or
        audio_file) — can't preview it. Check the saved audio_sync state.
      </div>
    );
  }

  return (
    <div className="ae-preview">
      <div className="ae-preview-video-wrap">
        <video
          ref={videoRef}
          src={videoSrc}
          muted={muteCameraAudio}
          onLoadedMetadata={() => { seekToOverlapStart(); setReady(true); }}
          onEnded={() => setPlaying(false)}
          className="ae-preview-video"
        />
      </div>
      {/* The external audio file has no visual element — it just needs to
          play in lockstep with the video's picture. */}
      <audio ref={audioRef} src={audioSrc} onEnded={() => setPlaying(false)} />

      <div className="ae-preview-controls">
        <button className="ae-preview-play" onClick={togglePlay} disabled={!ready}>
          {playing ? "Pause" : "▶ Play from sync point"}
        </button>
        <label className="ae-preview-mute">
          <input
            type="checkbox"
            checked={muteCameraAudio}
            onChange={(e) => setMuteCameraAudio(e.target.checked)}
          />
          Mute camera's own audio (hear only {basename(pair.audioFull)})
        </label>
      </div>

      <input
        type="range"
        className="ae-preview-scrub"
        min={0}
        max={videoRef.current?.duration || 0}
        step={0.05}
        value={currentTime}
        onChange={handleScrub}
      />

      <div className="ae-preview-meta">
        <div><strong>A-roll:</strong> {basename(pair.arollFull)}</div>
        <div><strong>Audio:</strong> {basename(pair.audioFull)}</div>
        <div><strong>Offset:</strong> {offsetSec >= 0 ? "+" : ""}{offsetSec.toFixed(2)}s
          {" — "}{offsetSec >= 0
            ? "audio starts this far into the A-roll"
            : "audio was already rolling before the A-roll started"}
        </div>
        <div><strong>Score:</strong> {pair.score?.toFixed(2) ?? "—"}</div>
      </div>

      {error && <div className="ae-preview-error">{error}</div>}
    </div>
  );
}

function basename(p) {
  if (!p) return "";
  const ix = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return ix >= 0 ? p.slice(ix + 1) : p;
}
