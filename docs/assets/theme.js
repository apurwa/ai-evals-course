/* Theme toggle, three states: system (default), light, dark.
 *
 * "System" is a real state, not the absence of one. A two-state toggle that
 * writes light or dark on first click takes the choice away from a reader who
 * never asked to make it, and then keeps it forever. So the cycle returns to
 * system, and system stores nothing.
 *
 * Applied on DOMContentLoaded rather than inline in <head>, which means a
 * reader whose stored choice differs from their OS setting may see one frame
 * of the wrong palette. The alternative is a blocking inline script in every
 * page. For a course site that is the wrong trade.
 */
(function () {
  var KEY = "wayfarer-evals-theme";
  var order = ["system", "light", "dark"];

  function apply(mode) {
    if (mode === "system") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", mode);
    }
  }

  function stored() {
    try {
      var v = localStorage.getItem(KEY);
      return order.indexOf(v) === -1 ? "system" : v;
    } catch (e) {
      // Private browsing or blocked storage. Fall back to system rather than
      // breaking the page over a preference.
      return "system";
    }
  }

  var mode = stored();
  apply(mode);

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme");
    if (!btn) return;

    function label() { btn.textContent = mode; }
    label();

    btn.addEventListener("click", function () {
      mode = order[(order.indexOf(mode) + 1) % order.length];
      apply(mode);
      label();
      try {
        if (mode === "system") localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, mode);
      } catch (e) { /* preference is not worth an exception */ }
    });
  });
})();
