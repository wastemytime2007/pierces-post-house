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

function ping() {
    return JSON.stringify({ ok: true });
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
        } catch (e) {
            result.errors.push({ name: item.name, error: e.toString() });
        }
    }

    return JSON.stringify(result);
}
