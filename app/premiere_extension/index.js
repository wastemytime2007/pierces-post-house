(function () {
  var csInterface = new CSInterface();
  var statusEl = document.getElementById("status");
  var logEl = document.getElementById("log");
  var seen = {}; // dedupe: don't re-log the same processed/error item every poll
  var pollCount = 0;

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
    statusEl.textContent = "Watching for tagged clips… (checked " + pollCount + "x)";
  }

  function poll() {
    csInterface.evalScript("scanAndInterpret()", function (result) {
      handleResult(result);
      setTimeout(poll, 2000);
    });
  }

  poll();
})();
