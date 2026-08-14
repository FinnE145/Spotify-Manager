(() => {
  const STORAGE_KEY = "canonical_viewer_selection";

  // Inline, because alert() is silently suppressed in the in-app browser.
  function showError(msg) {
    const el = document.getElementById("viewer-error");
    if (!el) return;
    el.hidden = false;
    el.textContent = msg;
  }

  function getSelection() {
    try {
      return new Set(JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]"));
    } catch (e) {
      return new Set();
    }
  }

  function setSelection(sel) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...sel]));
  }

  function updateControls(sel) {
    const countEl = document.getElementById("selected-count");
    if (countEl) countEl.textContent = String(sel.size);
    const btn = document.getElementById("group-selected-btn");
    if (btn) btn.disabled = sel.size < 2;
  }

  const selection = getSelection();

  document.querySelectorAll(".search-checkbox").forEach((cb) => {
    const tid = cb.dataset.trackId;
    cb.checked = selection.has(tid);
    cb.addEventListener("change", () => {
      if (cb.checked) selection.add(tid);
      else selection.delete(tid);
      setSelection(selection);
      updateControls(selection);
    });
  });

  updateControls(selection);

  const groupBtn = document.getElementById("group-selected-btn");
  if (groupBtn) {
    groupBtn.addEventListener("click", () => {
      if (selection.size < 2) return;
      window.location.href = "/dev/canonical/review?tracks=" + encodeURIComponent([...selection].join(","));
    });
  }

  const clearBtn = document.getElementById("clear-selection-btn");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      selection.clear();
      setSelection(selection);
      document.querySelectorAll(".search-checkbox").forEach((cb) => {
        cb.checked = false;
      });
      updateControls(selection);
    });
  }

  // ---------- Auto-group ----------
  // Preview, then confirm. Synchronous on the server (~1.2s), so a plain
  // "working..." message is all the progress reporting this needs.

  const agStatus = document.getElementById("autogroup-status");
  const agConfirm = document.getElementById("autogroup-confirm");
  const agPreviewBtn = document.getElementById("autogroup-preview-btn");
  const agRunBtn = document.getElementById("autogroup-run-btn");
  const agCancelBtn = document.getElementById("autogroup-cancel-btn");
  const agUndoBtn = document.getElementById("autogroup-undo-btn");

  function agJson(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  function agReset() {
    agConfirm.hidden = true;
    agPreviewBtn.hidden = false;
    agPreviewBtn.disabled = false;
  }

  if (agPreviewBtn) {
    agPreviewBtn.addEventListener("click", () => {
      agPreviewBtn.disabled = true;
      agStatus.textContent = "Checking…";
      agJson("/api/canonical/autogroup/preview").then((data) => {
        if (data.error) {
          agStatus.textContent = data.detail || data.error;
          agReset();
          return;
        }
        if (!data.groups_closed) {
          agStatus.textContent = `Nothing to auto-group — none of the ${data.queue_total} queue item(s) qualify.`;
          agReset();
          return;
        }
        agStatus.textContent =
          `This will resolve ${data.groups_closed} of ${data.queue_total} queue items ` +
          `(${data.tracks_affected} tracks), leaving ${data.remaining}.`;
        agPreviewBtn.hidden = true;
        agConfirm.hidden = false;
      });
    });

    agRunBtn.addEventListener("click", () => {
      agRunBtn.disabled = true;
      agCancelBtn.disabled = true;
      agStatus.textContent = "Grouping…";
      agJson("/api/canonical/autogroup", { method: "POST" }).then((data) => {
        if (data.error) {
          agStatus.textContent = data.detail || data.error;
          agRunBtn.disabled = false;
          agCancelBtn.disabled = false;
          return;
        }
        window.location.reload();
      });
    });

    agCancelBtn.addEventListener("click", () => {
      agStatus.textContent = "";
      agReset();
    });
  }

  // Two-step inline, never window.confirm(): the in-app browser disables
  // native dialogs and makes confirm() return false, so a confirm-gated
  // handler silently does nothing at all. Same reason showError() below
  // exists instead of alert().
  const agUndoConfirm = document.getElementById("autogroup-undo-confirm");
  const agUndoYes = document.getElementById("autogroup-undo-yes");
  const agUndoCancel = document.getElementById("autogroup-undo-cancel");

  if (agUndoBtn) {
    agUndoBtn.addEventListener("click", () => {
      agUndoBtn.hidden = true;
      agUndoConfirm.hidden = false;
    });

    agUndoCancel.addEventListener("click", () => {
      agUndoConfirm.hidden = true;
      agUndoBtn.hidden = false;
      agStatus.textContent = "";
    });

    agUndoYes.addEventListener("click", () => {
      agUndoYes.disabled = true;
      agUndoCancel.disabled = true;
      agStatus.textContent = "Restoring…";
      agJson("/api/canonical/autogroup/undo", { method: "POST" }).then((data) => {
        if (data.error) {
          agStatus.textContent = data.detail || data.error;
          agUndoYes.disabled = false;
          agUndoCancel.disabled = false;
          return;
        }
        window.location.reload();
      });
    });
  }

  // ---------- Cross-artist pane ----------
  // Loaded after the page paints: it and the two unreviewed counts are the
  // only things here that need full detection, which is ~350ms. Searching
  // refetches in place rather than reloading, so a search doesn't re-render
  // the (server-rendered, unaffected) Groups listing above it.

  const crossForm = document.getElementById("cross-form");
  const crossInput = document.getElementById("cross-input");
  const crossBody = document.getElementById("cross-body");
  const crossTotal = document.getElementById("cross-total");

  function loadCross(query) {
    crossBody.innerHTML = '<p class="meta">Loading cross-artist candidates…</p>';
    fetch("/api/canonical/cross/listing?cross=" + encodeURIComponent(query))
      .then((r) => r.json())
      .then((data) => {
        if (data.error) {
          crossBody.innerHTML = "";
          showError(data.detail || data.error);
          return;
        }
        crossBody.innerHTML = data.html;
        crossTotal.textContent = String(data.total);
        document.getElementById("unreviewed-main").textContent = String(data.unreviewed_main);
        document.getElementById("unreviewed-cross").textContent = String(data.unreviewed_cross);
      })
      .catch(() => {
        crossBody.innerHTML = "";
        showError("Could not load the cross-artist candidates.");
      });
  }

  if (crossForm) {
    crossForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const query = crossInput.value.trim();
      // Keep ?cross= in the URL so a reload or a copied link still shows the
      // same filter -- replaceState, so Back still leaves the page.
      const url = new URL(window.location.href);
      if (query) url.searchParams.set("cross", query);
      else url.searchParams.delete("cross");
      window.history.replaceState({}, "", url);
      loadCross(query);
    });

    loadCross(crossInput.value.trim());
  }

  document.querySelectorAll(".pin-star").forEach((btn) => {
    btn.addEventListener("click", () => {
      const trackId = btn.dataset.trackId;
      fetch("/api/canonical/pin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: trackId }),
      })
        .then((r) => r.json())
        .then((data) => {
          if (data.error) {
            showError(data.detail || data.error);
            return;
          }
          window.location.reload();
        });
    });
  });
})();
