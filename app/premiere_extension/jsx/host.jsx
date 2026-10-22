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


/* ------------------------------------------------------------------
 * Post House job inbox (Ryan, 2026-10-22: "make the xml open in a new
 * premiere project file that it names and places in the folder with the
 * other files it creates").
 *
 * The app cannot name Premiere's project from outside (an imported XML
 * lands in "Untitled"), so it leaves a small job file and this does it:
 * make the new project at the path the app chose, import the XML into
 * it, save. Jobs are plain "key=value" lines, parsed by hand and never
 * evaluated, because anything that can write a file there could
 * otherwise run code inside Premiere.
 *
 *   <Application Support>/Post House/premiere_jobs/<id>.job      (the request)
 *   <Application Support>/Post House/premiere_jobs/<id>.result   (ok=1 / error=...)
 *   <Application Support>/Post House/premiere_jobs/link.txt      (heartbeat the app reads)
 * ------------------------------------------------------------------ */

var LINK_VERSION = 2;
var JOBS_DIR = Folder.userData.fsName + "/Post House/premiere_jobs";
var _tick = 0;

function _writeText(path, text) {
    var f = new File(path);
    f.encoding = "UTF-8";
    if (!f.open("w")) return false;
    f.write(text);
    f.close();
    return true;
}

function _readText(path) {
    var f = new File(path);
    f.encoding = "UTF-8";
    if (!f.open("r")) return "";
    var t = f.read();
    f.close();
    return t;
}

/** "key=value" lines into an object. A value is everything after the first "=". */
function parseJob(text) {
    var out = {};
    var lines = String(text).split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
        var at = lines[i].indexOf("=");
        if (at > 0) out[lines[i].substring(0, at)] = lines[i].substring(at + 1);
    }
    return out;
}

function _endsWith(s, tail) {
    s = String(s).toLowerCase();
    return s.length >= tail.length && s.substring(s.length - tail.length) === tail;
}

/**
 * One job: a new project at job.project, the XML imported into it, saved.
 * Returns "" on success, else why it did not work.
 */
function runPostHouseJob(job) {
    if (!job.xml || !_endsWith(job.xml, ".xml")) return "the job has no XML";
    if (!job.project || !_endsWith(job.project, ".prproj")) return "the job has no project path ending in .prproj";
    if (!new File(job.xml).exists) return "the XML is not there: " + job.xml;
    if (new File(job.project).exists) return "a project already exists at " + job.project + " (not overwritten)";

    var made;
    try {
        made = app.newProject(job.project);
    } catch (e) {
        return "Premiere would not make the project: " + e.toString();
    }
    if (made === false) return "Premiere would not make the project";

    var imported;
    try {
        imported = app.project.importFiles([job.xml], true, app.project.rootItem, false);
    } catch (e2) {
        return "the project was made but the XML did not import: " + e2.toString();
    }
    if (imported === false) return "the project was made but the XML did not import";

    try {
        app.project.save();
    } catch (e3) {
        return "imported, but Premiere would not save the project: " + e3.toString();
    }
    return "";
}

/**
 * Called about once a second by index.js: keeps the heartbeat fresh and runs
 * any waiting job. Returns how many jobs it handled (so the caller can poll fast while busy).
 */
function postHouseTick() {
    var handled = 0;
    try {
        var dir = new Folder(JOBS_DIR);
        if (!dir.exists) dir.create();
        _tick += 1;
        if (_tick % 3 === 1) {
            _writeText(JOBS_DIR + "/link.txt", "v=" + LINK_VERSION + "\nt=" + (new Date()).getTime() + "\napp=" + app.version + "\n");
        }
        var jobs = dir.getFiles("*.job");
        for (var i = 0; i < jobs.length; i++) {
            var jobFile = jobs[i];
            var id = decodeURI(jobFile.name).replace(/\.job$/, "");
            var running = new File(JOBS_DIR + "/" + id + ".running");
            if (!jobFile.rename(running.name)) continue;          // another tick (or Premiere) already took it
            var job = parseJob(_readText(running.fsName));
            var why = runPostHouseJob(job);
            _writeText(JOBS_DIR + "/" + id + ".result", why === "" ? "ok=1\nproject=" + job.project + "\n" : "error=" + why + "\n");
            writeLog("job " + id + (why === "" ? " ok: " + job.project : " failed: " + why));
            running.remove();
            handled += 1;
        }
    } catch (e) {
        writeLog("ERROR in postHouseTick: " + e.toString());
    }
    return handled;
}
