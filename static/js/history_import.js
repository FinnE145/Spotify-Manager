(() => {
  const uploadForm = document.getElementById("upload-form");
  if (!uploadForm) return;

  const fileInput = document.getElementById("history-file");
  const uploadBtn = document.getElementById("upload-btn");
  const reimportBtn = document.getElementById("reimport-btn");
  const busyNote = document.getElementById("snapshot-busy-note");
  const errorEl = document.getElementById("import-error");
  const progressEl = document.getElementById("import-progress");
  const progressFill = document.getElementById("import-progress-fill");
  const progressLabel = document.getElementById("import-progress-label");

  const COUNT_FIELDS = [
    "total_plays",
    "distinct_uris",
    "in_library_uris",
    "foreign_uris",
    "in_library_plays",
    "in_library_pct",
    "tracks_total",
    "tracks_never_played",
  ];

  // Tracked separately from `running` so the re-import button isn't enabled
  // before anything has been uploaded, and a snapshot pull greys both
  // buttons out rather than letting a ~66 MB upload end in a 409.
  let hasUpload = !reimportBtn.disabled;
  let snapshotRunning = false;
  // Whether this page has actually watched a run, so a finished_at left over
  // from an earlier import doesn't announce itself on a fresh page load.
  let sawRunning = false;

  function api(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  function setField(name, value) {
    document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => {
      el.textContent = typeof value === "number" ? value.toLocaleString() : value;
    });
  }

  function setDateField(name, isoValue) {
    document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => {
      if (isoValue) {
        el.dataset.datetime = isoValue;
        el.title = isoValue;
        el.textContent = formatRelativeTime(isoValue);
      } else {
        delete el.dataset.datetime;
        el.removeAttribute("title");
        el.textContent = "—";
      }
    });
  }

  function setRunning(running) {
    const blocked = running || snapshotRunning;
    fileInput.disabled = blocked;
    uploadBtn.disabled = blocked;
    reimportBtn.disabled = blocked || !hasUpload;
    busyNote.hidden = !snapshotRunning;
    progressEl.hidden = !running;
  }

  function phaseLabel(status) {
    if (status.phase === "extracting") return "Extracting the export zip…";
    let label = `Parsing files: ${status.files_done}/${status.files_total}`;
    if (status.current_file) label += ` (${status.current_file})`;
    return `${label} — ${status.rows_read.toLocaleString()} rows read, ${status.rows_inserted.toLocaleString()} inserted`;
  }

  function showDone(status) {
    progressLabel.hidden = false;
    progressLabel.textContent = "";
    errorEl.hidden = true; // the terminal state is fully described here

    const summary = document.createElement("span");
    const counts =
      `${status.rows_read.toLocaleString()} rows read, ` +
      `${status.rows_inserted.toLocaleString()} inserted`;
    summary.textContent = status.error
      ? `Import failed after ${counts}: ${status.error}`
      : `Import finished — ${counts}.`;
    progressLabel.appendChild(summary);

    const reloadLink = document.createElement("a");
    reloadLink.href = "#";
    reloadLink.textContent = "Reload to see results";
    reloadLink.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.reload();
    });
    progressLabel.appendChild(reloadLink);
  }

  function poll() {
    api("/api/history/status")
      .then((status) => {
        COUNT_FIELDS.forEach((name) => setField(name, status[name]));
        setDateField("play_range_start", status.play_range_start);
        setDateField("play_range_end", status.play_range_end);

        snapshotRunning = Boolean(status.snapshot_running);
        if (status.running) sawRunning = true;
        setRunning(status.running);

        if (status.running) {
          progressLabel.hidden = false;
          progressLabel.textContent = phaseLabel(status);
          const pct = status.files_total
            ? Math.round((status.files_done / status.files_total) * 100)
            : 0;
          progressFill.style.width = `${pct}%`;
        } else if (status.finished_at && sawRunning) {
          showDone(status);
          sawRunning = false;
        }

        // Keep polling while a snapshot pull holds the lock, so the buttons
        // come back on their own once it finishes.
        if (status.running || snapshotRunning) setTimeout(poll, 1000);
      })
      .catch(() => {
        // Transient failure (e.g. dev server restart mid-import) — keep going.
        setTimeout(poll, 1000);
      });
  }

  function started(data) {
    if (data.error) {
      errorEl.hidden = false;
      errorEl.textContent = data.detail || data.error;
      progressLabel.hidden = true;
      setRunning(false);
      return;
    }
    sawRunning = true;
    setRunning(true);
    poll();
  }

  function failed(e) {
    errorEl.hidden = false;
    errorEl.textContent = `Request failed: ${e}. The dev server may have restarted — try again.`;
    setRunning(false);
  }

  uploadForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!fileInput.files.length) {
      errorEl.hidden = false;
      errorEl.textContent = "Choose the export .zip first.";
      return;
    }
    errorEl.hidden = true;
    progressLabel.hidden = false;
    progressLabel.textContent = "Uploading…";
    setRunning(true);

    const body = new FormData();
    body.append("file", fileInput.files[0]);
    api("/api/history/import", { method: "POST", body })
      .then((data) => {
        hasUpload = hasUpload || !data.error;
        started(data);
      })
      .catch(failed);
  });

  reimportBtn.addEventListener("click", () => {
    errorEl.hidden = true;
    api("/api/history/reimport", { method: "POST" }).then(started).catch(failed);
  });

  // Pick up a run already going (a page reload mid-import), and learn on load
  // whether a snapshot pull is currently blocking imports.
  poll();
})();
