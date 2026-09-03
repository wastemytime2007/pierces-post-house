/**
 * SyncMatrix — Drop 3.6.
 *
 * Displays the audio sync results as a grid of A-roll rows × lav columns.
 * Each cell shows the confidence score, color-coded by the thresholds the
 * backend uses (>= 10 reliable, 5-10 maybe, < 5 weak).
 *
 * Two rendering modes:
 *   (1) "Just computed" — pairs arrive one-at-a-time from audio_sync_pair
 *       events during the pipeline run. We build the matrix incrementally.
 *   (2) "Persisted" — when the project has a cached audio_sync in its
 *       state, we render that directly. This is the common case when
 *       the user is looking at a project that's already been processed.
 *
 * The component takes EITHER a list of pair events (live mode) OR a full
 * AudioSyncState-shaped object from the project. If neither, shows empty.
 */
export default function SyncMatrix({ pairs, syncState, liveStatus, onSelectPair, selectedKey }) {
  // Normalize inputs into a unified pair list
  const pairList = _resolvePairs(pairs, syncState);

  if (!pairList.length && liveStatus !== "running") {
    return (
      <div className="sync-matrix-empty">
        <em>No audio sync yet.</em>{" "}
        Run the pipeline to generate lav-to-A-roll sync data. Needed for
        automatic audio alignment on export.
      </div>
    );
  }

  // Derive the unique A-rolls (rows) and lavs (columns)
  const arollSet = new Set(pairList.map((p) => p.aroll));
  const lavSet = new Set(pairList.map((p) => p.audio));
  const arolls = Array.from(arollSet).sort();
  const lavs = Array.from(lavSet).sort();

  // Build lookup by (aroll, audio)
  const pairLookup = new Map();
  for (const p of pairList) {
    pairLookup.set(`${p.aroll}|${p.audio}`, p);
  }

  return (
    <div className="sync-matrix-wrap">
      <div className="sync-matrix-header">
        <div className="sync-matrix-title">Audio Sync</div>
        <div className="sync-matrix-sub">
          {liveStatus === "running"
            ? `Matching ${pairList.length} pair${pairList.length === 1 ? "" : "s"}…`
            : `${pairList.filter((p) => p.reliable).length} reliable of ${pairList.length} pair${pairList.length === 1 ? "" : "s"}`}
        </div>
      </div>
      <div className="sync-matrix-scroll">
        <table className="sync-matrix">
          <thead>
            <tr>
              <th className="sync-cell-corner" />
              {lavs.map((lav) => (
                <th key={lav} className="sync-cell-head" title={lav}>
                  {_shortName(lav)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {arolls.map((aroll) => (
              <tr key={aroll}>
                <th className="sync-cell-row" title={aroll}>
                  {_shortName(aroll)}
                </th>
                {lavs.map((lav) => {
                  const pair = pairLookup.get(`${aroll}|${lav}`);
                  const key = `${aroll}|${lav}`;
                  const selectable = !!(onSelectPair && pair && pair.audioFull);
                  return (
                    <td
                      key={lav}
                      className={`sync-cell ${_scoreClass(pair)} ${selectable ? "sync-cell-selectable" : ""} ${selectedKey === key ? "sync-cell-selected" : ""}`}
                      title={_tooltip(pair)}
                      onClick={selectable ? () => onSelectPair(pair, key) : undefined}
                    >
                      {pair ? (
                        <>
                          <div className="sync-score">
                            {pair.score == null
                              ? "—"
                              : pair.score.toFixed(1)}
                          </div>
                          {pair.offset != null && (
                            <div className="sync-offset">
                              {pair.offset >= 0 ? "+" : ""}
                              {pair.offset.toFixed(1)}s
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="sync-score">·</div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sync-legend">
        <span className="sync-legend-item">
          <span className="sync-swatch sync-strong" />
          Strong (≥10)
        </span>
        {/* Drop 4.47.4: cross-validated cells — weak score, but offset
            confirmed by agreement with strong matches on other clips. */}
        <span
          className="sync-legend-item"
          title="Score is below 10 but the offset agrees with strong matches on other clips. Used on export."
        >
          <span className="sync-swatch sync-promoted" />
          Cross-validated
        </span>
        <span className="sync-legend-item">
          <span className="sync-swatch sync-maybe" />
          Maybe (5-10)
        </span>
        <span className="sync-legend-item">
          <span className="sync-swatch sync-weak" />
          Weak (&lt;5)
        </span>
      </div>
    </div>
  );
}

function _resolvePairs(pairs, syncState) {
  if (Array.isArray(pairs) && pairs.length) {
    // Live mode — events carry filenames directly
    return pairs.map((p) => ({
      aroll: p.aroll,
      audio: p.audio,
      score: p.score,
      offset: p.offset,
      reliable: p.reliable,
      // Drop 4.47.4: pair was marked reliable via cross-validation,
      // not by raw score crossing threshold. Used to label cells with
      // a different tooltip explaining the promotion.
      promoted: !!p.promoted,
      error: p.error,
    }));
  }
  if (syncState && Array.isArray(syncState.pairs) && syncState.pairs.length) {
    // Persisted mode — convert full paths to basenames for display, but
    // keep the full paths too (Post House Task 2.1: needed so a clicked
    // cell can hand real file paths to the playback preview).
    return syncState.pairs.map((p) => ({
      aroll: _baseName(p.aroll_file || p.aroll_proxy || ""),
      audio: _baseName(p.audio_file || ""),
      score: p.score,
      offset: p.offset_sec,
      // Drop 4.47.4: a pair is reliable if its raw score crosses
      // threshold OR if the backend promoted it via cross-validation
      // (offset-difference consistency with strong matches).
      reliable: p.score >= 10 || !!p.promoted_via_consistency,
      promoted: !!p.promoted_via_consistency,
      arollFull: p.aroll_file || "",
      arollProxyFull: p.aroll_proxy || "",
      audioFull: p.audio_file || "",
      offsetSec: p.offset_sec,
    }));
  }
  return [];
}

function _baseName(p) {
  if (!p) return "";
  const ix = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return ix >= 0 ? p.slice(ix + 1) : p;
}

function _shortName(name) {
  if (name.length <= 22) return name;
  return name.slice(0, 10) + "…" + name.slice(-10);
}

function _scoreClass(pair) {
  if (!pair) return "sync-empty";
  if (pair.error) return "sync-error";
  if (pair.score == null) return "sync-empty";
  // Drop 4.47.4: promoted-via-consistency cells get a distinct class
  // (between maybe and strong) so users can see at a glance which
  // sub-threshold matches were rescued by cross-validation.
  if (pair.promoted) return "sync-promoted";
  if (pair.score >= 10) return "sync-strong";
  if (pair.score >= 5) return "sync-maybe";
  return "sync-weak";
}

function _tooltip(pair) {
  if (!pair) return "";
  if (pair.error) return `Error: ${pair.error}`;
  let outcome;
  if (pair.promoted) {
    // Drop 4.47.4: weak score, but cross-validated against strong
    // matches via offset-difference consistency. Used on export.
    outcome = "Score is weak, but offset is consistent with strong matches on other clips. Used on export (cross-validated).";
  } else if (pair.reliable) {
    outcome = "Match is reliable; used on export.";
  } else {
    outcome = "Match is below threshold; skipped on export.";
  }
  return (
    `Score: ${pair.score?.toFixed(2) ?? "—"}\n` +
    `Offset: ${pair.offset?.toFixed(2) ?? "—"} sec\n` +
    outcome
  );
}
