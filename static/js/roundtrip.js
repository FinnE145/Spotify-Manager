(() => {
  const startBtn = document.getElementById("roundtrip-start-btn");
  if (!startBtn) return;

  const stopBtn = document.getElementById("roundtrip-stop-btn");
  const reconcileBtn = document.getElementById("roundtrip-reconcile-btn");
  const clearBtn = document.getElementById("roundtrip-clear-failures-btn");
  const busyNote = document.getElementById("roundtrip-busy-note");
  const errorEl = document.getElementById("roundtrip-error");
  const progressEl = document.getElementById("roundtrip-progress");
  const progressFill = document.getElementById("roundtrip-progress-fill");
  const progressLabel = document.getElementById("roundtrip-progress-label");
  const requestsEl = document.getElementById("roundtrip-requests");
  const logEl = document.getElementById("roundtrip-log");
  const logEmptyEl = document.getElementById("roundtrip-log-empty");

  const COUNT_FIELDS = [
    "remaining_uris",
    "batches",
    "requests_estimate",
    "resolved_tracks",
    "aliases",
    "failed_uris",
    "wanted_uris",
    "reconcilable",
    "review_uris",
    "requests",
  ];

  const JOB_NAMES = {
    snapshot: "a snapshot pull",
    history_import: "a play-history import",
    roundtrip: "a round-trip",
  };

  // Whether this page has actually watched a run, so a finished_at left over
  // from an earlier run doesn't announce itself on a fresh page load.
  let sawRunning = false;

  function api(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  function setField(name, value) {
    document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => {
      el.textContent = typeof value === "number" ? value.toLocaleString() : value;
    });
    document.querySelectorAll(`[data-plural-for="${name}"]`).forEach((el) => {
      el.textContent = value === 1 ? "" : el.dataset.pluralSuffix || "s";
    });
  }

  function setControls(status) {
    const otherJob = status.active_job && status.active_job !== "roundtrip";
    // Tracked off the live count too: with nothing left to do a run would
    // spend the guard's two requests and the clear's one for no work.
    startBtn.disabled = Boolean(status.active_job) || !status.remaining_uris;
    // Tracked off the live count, not just `running`, so it disables itself
    // once a run leaves nothing to reconcile.
    reconcileBtn.disabled = Boolean(status.active_job) || !status.reconcilable;
    stopBtn.disabled = !status.running || status.stopping;
    stopBtn.textContent = status.stopping ? "Stopping…" : "Stop";
    busyNote.hidden = !otherJob;
    if (otherJob) {
      busyNote.textContent = `${JOB_NAMES[status.active_job] || status.active_job} is running — one job at a time.`;
    }
    progressEl.hidden = !status.running;
    requestsEl.hidden = !status.running;
  }

  function phaseLabel(status) {
    if (status.phase === "guard") return "Verifying the loader playlist…";
    if (status.phase === "clearing") return "Clearing the loader playlist…";
    let label = `Batch ${status.batch_done}/${status.batch_total}`;
    if (status.current) label += ` — ${status.current}`;
    if (status.phase === "reconciling") {
      return (
        `Reconciling · ${label} · ${status.reconciled} matched, ` +
        `${status.needs_review} for manual review`
      );
    }
    return (
      `${label} · ${status.uris_stored.toLocaleString()} stored, ` +
      `${status.aliases_created} aliased, ${status.uris_failed} failed`
    );
  }

  function renderLog(entries) {
    // Follow the tail only when the reader is already at it, so scrolling up
    // to read an earlier batch doesn't get yanked back down every poll.
    const following =
      logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 24;

    // Replaced wholesale rather than diffed — the log is capped server-side,
    // so it stays small enough for that to be the simplest correct thing.
    logEl.textContent = "";
    entries.forEach((entry) => {
      const li = document.createElement("li");
      const ts = makeDateSpan(entry.ts);
      ts.className = "event-log-ts";
      li.appendChild(ts);
      li.appendChild(document.createTextNode(entry.message));
      logEl.appendChild(li);
    });
    logEmptyEl.hidden = entries.length > 0;
    if (following) logEl.scrollTop = logEl.scrollHeight;
  }

  function showDone(status) {
    progressLabel.hidden = false;
    progressLabel.textContent = "";
    errorEl.hidden = true; // the terminal state is fully described here

    const totals =
      `${status.uris_stored.toLocaleString()} tracks stored, ` +
      `${status.aliases_created} aliased, ${status.uris_failed} URIs failed, ` +
      `${status.requests} requests spent`;

    const summary = document.createElement("span");
    if (status.outcome === "rate_limited") {
      summary.textContent = `Rate limited after ${totals} — retry `;
      progressLabel.appendChild(summary);
      if (status.retry_at) progressLabel.appendChild(makeDateSpan(status.retry_at));
    } else if (status.outcome === "error") {
      summary.textContent = `Run failed after ${totals}: ${status.error}`;
      progressLabel.appendChild(summary);
    } else if (status.outcome === "stopped") {
      // A deliberate stop is not a fault and must not render as one.
      summary.textContent = `Stopped — ${totals}.`;
      progressLabel.appendChild(summary);
    } else if (status.outcome === "breaker") {
      summary.textContent = `Stopped by the circuit breaker (three consecutive failed batches) — ${totals}.`;
      progressLabel.appendChild(summary);
    } else {
      summary.textContent = `Run finished — ${totals}.`;
      progressLabel.appendChild(summary);
    }

    if (status.left_in_playlist) {
      const left = document.createElement("span");
      left.textContent = ` ${status.left_in_playlist} item(s) were left in the loader playlist — clear them by hand in Spotify if you want to.`;
      progressLabel.appendChild(left);
    }

    if (status.failures && status.failures.length) {
      const list = document.createElement("ul");
      status.failures.forEach((f) => {
        const li = document.createElement("li");
        const id = f.uri.split(":").pop();
        const link = document.createElement("a");
        link.href = `https://open.spotify.com/track/${id}`;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = id;
        li.appendChild(link);
        li.appendChild(document.createTextNode(` — ${f.reason}`));
        list.appendChild(li);
      });
      progressLabel.appendChild(list);
    }

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
    api("/api/roundtrip/status")
      .then((status) => {
        COUNT_FIELDS.forEach((name) => setField(name, status[name]));
        renderLog(status.log || []);

        if (status.running) sawRunning = true;
        setControls(status);

        if (status.running) {
          progressLabel.hidden = false;
          progressLabel.textContent = phaseLabel(status);
          const pct = status.batch_total
            ? Math.round((status.batch_done / status.batch_total) * 100)
            : 0;
          progressFill.style.width = `${pct}%`;
        } else if (status.finished_at && sawRunning) {
          showDone(status);
          sawRunning = false;
        }

        // Keep polling while another job holds the slot, so the start button
        // comes back on its own once it finishes.
        if (status.active_job) setTimeout(poll, 1000);
      })
      .catch(() => {
        // Transient failure (e.g. dev server restart mid-run) — keep going.
        setTimeout(poll, 1000);
      });
  }

  function startRun(path, button) {
    errorEl.hidden = true;
    button.disabled = true;
    api(path, { method: "POST" })
      .then((data) => {
        if (data.error) {
          errorEl.hidden = false;
          errorEl.textContent = data.detail ? `${data.error} (${data.detail})` : data.error;
          button.disabled = false;
          return;
        }
        sawRunning = true;
        poll();
      })
      .catch((e) => {
        errorEl.hidden = false;
        errorEl.textContent = `Request failed: ${e}. The dev server may have restarted — try again.`;
        button.disabled = false;
      });
  }

  startBtn.addEventListener("click", () => startRun("/api/roundtrip/start", startBtn));
  reconcileBtn.addEventListener("click", () =>
    startRun("/api/roundtrip/reconcile", reconcileBtn)
  );

  // ---------- manual aliases ----------

  const reviewError = document.getElementById("review-error");
  const reviewSaveBtn = document.getElementById("review-save-btn");

  if (reviewSaveBtn) {
    // One save for the whole table: saving row-by-row reloaded the page and
    // threw away every other selection made along the way.
    const chosen = () =>
      [...document.querySelectorAll("[data-review-uri]")]
        .map((row) => {
          const select = row.querySelector("[data-review-select]");
          return select && select.value
            ? { requested_uri: row.dataset.reviewUri, track_id: select.value }
            : null;
        })
        .filter(Boolean);

    document.querySelectorAll("[data-review-select]").forEach((select) => {
      select.addEventListener("change", () => {
        reviewSaveBtn.disabled = chosen().length === 0;
      });
    });

    reviewSaveBtn.addEventListener("click", () => {
      const aliases = chosen();
      if (!aliases.length) return;
      reviewSaveBtn.disabled = true;
      reviewSaveBtn.textContent = "Saving…";
      reviewError.hidden = true;
      api("/api/roundtrip/alias", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ aliases }),
      })
        .then((data) => {
          if (data.error) throw new Error(data.detail || data.error);
          window.location.reload();
        })
        .catch((e) => {
          reviewError.hidden = false;
          reviewError.textContent = `Could not save: ${e.message || e}. Nothing was changed.`;
          reviewSaveBtn.disabled = false;
          reviewSaveBtn.textContent = "Save aliases";
        });
    });
  }

  stopBtn.addEventListener("click", () => {
    // Switched immediately so it's obvious the request landed — the actual
    // stop waits for the current batch to finish and commit.
    stopBtn.disabled = true;
    stopBtn.textContent = "Stopping…";
    api("/api/roundtrip/stop", { method: "POST" }).catch(() => {});
  });

  clearBtn.addEventListener("click", () => {
    clearBtn.disabled = true;
    api("/api/roundtrip/clear-failures", { method: "POST" })
      .then(() => window.location.reload())
      .catch(() => {
        clearBtn.disabled = false;
      });
  });

  // Pick up a run already going (a page reload mid-run), and learn on load
  // whether another job is currently blocking the start button.
  poll();
})();
