(function () {
  var csInterface = new CSInterface();

  // Backoff: poll fast right after activity (catches an import quickly),
  // then slow way down when idle so this isn't walking the whole Project
  // panel every 2s all day for nothing. Any new activity resets to fast.
  // (host.jsx writes what happened to posthouse_interpreter.log — there's
  // no panel here to show it in.)
  var FAST_MS = 2000;
  var MAX_MS = 20000;
  var STEP_MS = 2000;
  var currentInterval = FAST_MS;

  function poll() {
    csInterface.evalScript("scanAndInterpret()", function (result) {
      var hadActivity = false;
      try {
        var parsed = JSON.parse(result);
        hadActivity = parsed.processed.length > 0 || parsed.errors.length > 0;
      } catch (e) {
        // Malformed/empty response — treat as idle, back off same as normal.
      }
      currentInterval = hadActivity ? FAST_MS : Math.min(currentInterval + STEP_MS, MAX_MS);
      setTimeout(poll, currentInterval);
    });
  }

  poll();
})();
