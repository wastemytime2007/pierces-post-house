import { useState } from "react";
import SyncMatrix from "../../components/SyncMatrix.jsx";
import AudioSyncPreview from "../../components/AudioSyncPreview.jsx";

/**
 * AETab — Post House Task 2.1, the Assistant Editor's first slice.
 *
 * Reviews the audio sync PreCut's own `sync_project()` already computed
 * (run via Ingest's pipeline with "Audio sync" checked; persisted at
 * `project.audio_sync`). This tab does not re-run sync — it's a review
 * checkpoint: the same matrix Ingest already shows, plus the one thing
 * PreCut's frontend has never had anywhere: an actual playable preview,
 * so a reliable-looking score can be confirmed by ear, not just trusted.
 *
 * Smallest proof unit (Ryan's choice, 2026-09-03): Runnells Day 1 — 2
 * A-roll clips x 4 audio files across two mic units, already staged in
 * this repo's benchmark/ folder. Signed off when Ryan runs this against
 * that project, reviews the pairs, previews at least one, and confirms
 * the result is correct.
 */
export default function AETab({ project }) {
  const [selected, setSelected] = useState(null); // { pair, key }

  const syncState = project.audio_sync;
  const hasSync = !!(syncState && Array.isArray(syncState.pairs) && syncState.pairs.length);

  return (
    <div className="ae-tab">
      <div className="ae-tab-header">
        <h2>Assistant Editor · Audio Sync Review</h2>
        <p className="ae-tab-sub">
          Reviews the sync PreCut already computed for this project. Click
          any reliable (green) cell to preview it. If nothing shows below,
          run the pipeline in the Ingest tab with "Audio sync" checked
          first.
        </p>
      </div>

      <SyncMatrix
        syncState={syncState}
        onSelectPair={hasSync ? (pair, key) => setSelected({ pair, key }) : undefined}
        selectedKey={selected?.key}
      />

      <div className="ae-tab-preview">
        <AudioSyncPreview pair={selected?.pair} />
      </div>
    </div>
  );
}
