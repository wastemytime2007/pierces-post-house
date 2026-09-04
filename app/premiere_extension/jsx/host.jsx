/**
 * host.jsx — ExtendScript that runs inside Premiere Pro.
 *
 * The ONLY job of this extension: find Project-panel items the XML
 * export already labeled "... [INTERPRET TO X.XXXfps]" and apply
 * Premiere's own Modify > Interpret Footage to them, automatically.
 * It does not import files, build bins, build sequences, or place
 * clips — the FCP7 XML export already does all of that. This is not
 * a rebuild of that pipeline, just the one piece Interpret Footage
 * that a static XML export cannot express (confirmed by real testing;
 * see ROADMAP.md Decision Log, 2026-09-03).
 *
 * Idempotent by design: re-running scanAndInterpret() on a project
 * that's already been processed does nothing, because it checks each
 * matching item's CURRENT interpreted frame rate before touching it.
 */

var TAG_PATTERN = /\[INTERPRET TO ([\d.]+)fps\]\s*$/;
var FPS_TOLERANCE = 0.01; // fps
var LOG_PATH = Folder.userData.fsName + "/posthouse_interpreter.log";

/**
 * Transcript-flagging marker colors, real Premiere Marker.setColorByIndex()
 * indices — confirmed 2026-09-03 that Premiere's FCP7 XML import does NOT
 * honor the <color><red>/<green>/<blue> block our exporter writes on
 * <marker> (that element isn't part of the xmeml marker schema at all —
 * only name/in/out/comment are). Every imported marker silently defaults
 * to color index 0 (green), which is exactly the "all markers are the
 * same default green" Ryan reported. The only real way to set a marker's
 * color in Premiere is this ExtendScript API, post-import, in-app — same
 * shape as scanAndInterpret() below solving the analogous frame-rate gap.
 * Index mapping is Premiere's own fixed 8-swatch palette (confirmed via
 * Ryan's screenshots of the marker color picker): 0=Green 1=Red 2=Purple
 * 3=Orange 4=Yellow 5=White 6=Blue 7=Cyan.
 */
var FIT_COLOR_INDEX = { "strong": 4, "possible": 0, "off_topic": 1 };
var FIT_PREFIX_PATTERN = /^(strong|possible|off_topic):/;

function ping() {
    return JSON.stringify({ ok: true });
}

/**
 * The extension is invisible now (no panel, no menu entry — see
 * manifest.xml) so this log file is the only way to see what it did.
 */
function writeLog(line) {
    try {
        var f = new File(LOG_PATH);
        f.open("a");
        f.writeln("[" + new Date().toString() + "] " + line);
        f.close();
    } catch (e) {
        // Nothing to fall back to if logging itself fails; never let
        // logging break the actual interpretation work.
    }
}

/**
 * Recursively collect every clip-type ProjectItem (never bins) under root.
 */
function collectClipItems(root, out) {
    for (var i = 0; i < root.children.numItems; i++) {
        var item = root.children[i];
        if (item.type === ProjectItemType.BIN) {
            collectClipItems(item, out);
        } else if (item.type === ProjectItemType.CLIP) {
            out.push(item);
        }
    }
}

/**
 * Scan the current project for tagged items and interpret any that
 * aren't already at the target rate. Returns a JSON string:
 * { processed: [{name, targetFps}], skipped: [{name, reason}], errors: [{name, error}] }
 */
function scanAndInterpret() {
    var result = { processed: [], skipped: [], errors: [] };

    if (!app.project) {
        result.errors.push({ name: "(no project)", error: "No project open." });
        return JSON.stringify(result);
    }

    var items = [];
    try {
        collectClipItems(app.project.rootItem, items);
    } catch (e) {
        result.errors.push({ name: "(scan)", error: e.toString() });
        return JSON.stringify(result);
    }

    for (var i = 0; i < items.length; i++) {
        var item = items[i];
        var match = TAG_PATTERN.exec(item.name);
        if (!match) continue;

        var targetFps = parseFloat(match[1]);
        if (isNaN(targetFps) || targetFps <= 0) {
            result.errors.push({ name: item.name, error: "Could not parse target fps from name." });
            continue;
        }

        try {
            var interp = item.getFootageInterpretation();
            if (interp && Math.abs(interp.frameRate - targetFps) < FPS_TOLERANCE) {
                result.skipped.push({ name: item.name, reason: "already at target rate" });
                continue;
            }
            interp.frameRate = targetFps;
            item.setFootageInterpretation(interp);
            result.processed.push({ name: item.name, targetFps: targetFps });
            writeLog("Interpreted -> " + targetFps.toFixed(3) + "fps: " + item.name);
        } catch (e) {
            result.errors.push({ name: item.name, error: e.toString() });
            writeLog("ERROR on " + item.name + ": " + e.toString());
        }
    }

    return JSON.stringify(result);
}

/**
 * Recolor every marker (sequence-level and clip-attached) whose comment
 * starts with a fit prefix ("strong:"/"possible:"/"off_topic:" — see
 * posthouse/transcript_markers.py build_flag_markers_for_phrase) to its
 * real Premiere color. Idempotent: skips a marker already at the target
 * index. Returns { colored: [{name, fit}], skipped: [...], errors: [...] }.
 */
function recolorMarkerCollection(markers, ownerLabel, result) {
    if (!markers || !markers.numMarkers) return;
    for (var m = markers.getFirstMarker(); m !== undefined; m = markers.getNextMarker(m)) {
        try {
            var comments = m.comments || "";
            var match = FIT_PREFIX_PATTERN.exec(comments);
            if (!match) continue;
            var fit = match[1];
            var targetIndex = FIT_COLOR_INDEX[fit];
            if (targetIndex === undefined) continue;

            var current = m.getColorByIndex();
            if (current === targetIndex) {
                result.skipped.push({ name: m.name, reason: "already " + fit });
                continue;
            }
            m.setColorByIndex(targetIndex);
            result.colored.push({ name: m.name, fit: fit, owner: ownerLabel });
            writeLog("Colored marker '" + m.name + "' (" + ownerLabel + ") -> " + fit + " (index " + targetIndex + ")");
        } catch (e) {
            result.errors.push({ name: m.name || "(unnamed)", error: e.toString() });
            writeLog("ERROR coloring marker on " + ownerLabel + ": " + e.toString());
        }
    }
}

function scanAndColorMarkers() {
    var result = { colored: [], skipped: [], errors: [] };

    if (!app.project) {
        result.errors.push({ name: "(no project)", error: "No project open." });
        return JSON.stringify(result);
    }

    if (app.project.activeSequence) {
        recolorMarkerCollection(app.project.activeSequence.markers, "sequence", result);
    }

    var items = [];
    try {
        collectClipItems(app.project.rootItem, items);
    } catch (e) {
        result.errors.push({ name: "(scan)", error: e.toString() });
        return JSON.stringify(result);
    }

    for (var i = 0; i < items.length; i++) {
        var item = items[i];
        try {
            recolorMarkerCollection(item.getMarkers(), "clip:" + item.name, result);
        } catch (e) {
            result.errors.push({ name: item.name, error: e.toString() });
        }
    }

    return JSON.stringify(result);
}
