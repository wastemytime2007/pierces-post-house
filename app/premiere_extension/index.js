(function () {
  var csInterface = new CSInterface();
  var statusEl = document.getElementById("status");
  var logEl = document.getElementById("log");
  var seen = {}; // dedupe: don't re-log the same processed/error item every poll
  var pollCount = 0;

  // Backoff: poll fast right after activity (catches an import quickly),
  // then slow way down when idle so this isn't walking the whole Project
  // panel every 2s all day for nothing. Any new activity resets to fast.
  var FAST_MS = 2000;
  var MAX_MS = 20000;
  var STEP_MS = 2000;
  var currentInterval = FAST_MS;

  function addLogLine(text, cls) {
    var li = document.createElement("li");
    li.className = cls;
    li.textContent = text;
    logEl.insertBefore(li, logEl.firstChild);
  }

  function handleResult(json) {
    var result;
    try {
      result = JSON.parse(json);
    } catch (e) {
      statusEl.textContent = "Error reading Premiere response.";
      return;
    }

    var hadActivity = result.processed.length > 0 || result.errors.length > 0;
    currentInterval = hadActivity ? FAST_MS : Math.min(currentInterval + STEP_MS, MAX_MS);

    for (var i = 0; i < result.processed.length; i++) {
      var p = result.processed[i];
      var key = "ok:" + p.name;
      if (!seen[key]) {
        seen[key] = true;
        addLogLine("Interpreted → " + p.targetFps.toFixed(3) + "fps: " + p.name, "done");
      }
    }
    for (var j = 0; j < result.errors.length; j++) {
      var e = result.errors[j];
      var ekey = "err:" + e.name + ":" + e.error;
      if (!seen[ekey]) {
        seen[ekey] = true;
        addLogLine("Error on " + e.name + ": " + e.error, "err");
      }
    }

    pollCount++;
    var idleSecs = Math.round(currentInterval / 1000);
    statusEl.textContent = hadActivity
      ? "Watching for tagged clips… (checked " + pollCount + "x)"
      : "Watching for tagged clips… (checked " + pollCount + "x, idle — every " + idleSecs + "s)";
  }

  function poll() {
    csInterface.evalScript("scanAndInterpret()", function (result) {
      handleResult(result);
      setTimeout(poll, currentInterval);
    });
  }

  poll();
})();
