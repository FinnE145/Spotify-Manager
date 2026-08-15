// docs/specs/async-recompute-N.md §7.2: click feedback for the
// generation-confirm form (templates/_macros.html's generation_confirm_banner),
// loaded site-wide from base.html so it can never drift from the macro it
// serves. No-ops when the form isn't on the page.
//
// The trap: the form carries its decision on the submit button
// (name="decision", value="yes"|"no"). event.submitter correctly identifies
// which button was clicked, but disabling that button *synchronously inside
// the submit handler* still drops it from the submitted form data -- the
// browser constructs the entry list from live DOM state immediately after
// the submit event finishes dispatching, in the same synchronous step, and
// a disabled field (including the submitter itself) is excluded regardless
// of how it was identified. Verified directly: a synchronous disable here
// posts no `decision` and the route 400s. Deferring the disable past that
// step (setTimeout 0) lets the browser read the still-enabled button first.
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("generation-confirm-form");
  if (!form) return;

  form.addEventListener("submit", (event) => {
    const clicked = event.submitter;
    setTimeout(() => {
      form.querySelectorAll("button[type=submit]").forEach((btn) => {
        btn.disabled = true;
      });
      if (clicked) clicked.textContent = "Working…";
    }, 0);
  });
});
