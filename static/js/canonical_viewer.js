(() => {
  const STORAGE_KEY = "canonical_viewer_selection";

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
            alert(data.detail || data.error);
            return;
          }
          window.location.reload();
        });
    });
  });
})();
