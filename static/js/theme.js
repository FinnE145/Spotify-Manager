// docs/specs/ui-framework-W.md §4: the light/dark toggle.
//
// The *initial* value is not set here -- it is set by an inline script in
// base.html's <head>, and that placement is load-bearing. An external file,
// even one loaded synchronously, leaves a window in which the document paints
// light before a stored dark choice lands, and the flash happens on every
// page load. This file owns only the click, which has no such constraint.
//
// Three states, two stored values: "light" and "dark" are explicit choices,
// and the *absence* of the key means follow the system. That is why the
// toggle writes a value but the inline script falls back to a media query
// rather than to a default string.
document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("theme-toggle");
  const icon = document.getElementById("theme-icon");
  if (!btn || !icon) return;
  const root = document.documentElement;

  const render = () => {
    const dark = root.getAttribute("data-bs-theme") === "dark";
    // The button shows the theme it switches *to*, not the one in force.
    icon.className = dark ? "bi bi-sun" : "bi bi-moon";
    btn.title = dark ? "Switch to light" : "Switch to dark";
  };

  btn.addEventListener("click", () => {
    const next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-bs-theme", next);
    // localStorage throws outright in some privacy modes; a failed *write*
    // must still leave the page on the theme the click asked for.
    try {
      localStorage.setItem("symr_theme", next);
    } catch (e) {}
    render();
  });

  render();
});
