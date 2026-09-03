/**
 * Minimal CSInterface — wraps the native CEP bridge (__adobe_cep__).
 *
 * This covers only what this panel needs. For the full Adobe
 * CSInterface library (~1500 lines), see:
 * https://github.com/Adobe-CEP/CEP-Resources/blob/master/CEP_11.x/CSInterface.js
 */

var CSInterface = (function () {
  function CSInterface() {}

  /**
   * Evaluate an ExtendScript string inside the host app (Premiere Pro).
   * @param {string} script   ExtendScript to evaluate
   * @param {function} [callback]  Called with the result string when done
   */
  CSInterface.prototype.evalScript = function (script, callback) {
    if (window.__adobe_cep__) {
      window.__adobe_cep__.evalScript(script, callback || function () {});
    } else {
      console.warn("CSInterface: __adobe_cep__ not available (not inside a CEP host).");
      if (callback) callback("EvalScript error.");
    }
  };

  return CSInterface;
})();
