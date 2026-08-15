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

  // ---------- round-trip queue box (spec M §4.6) ----------
  const listeningExcludedNote = document.getElementById("listening-excluded-note");
  const listeningClearBtn = document.getElementById("listening-clear-btn");
  const listeningReaddBtn = document.getElementById("listening-readd-btn");

  // ---------- album backfill ----------
  const backfillStopBtn = document.getElementById("backfill-stop-btn");
  const backfillBusyNote = document.getElementById("backfill-busy-note");
  const backfillError = document.getElementById("backfill-error");
  const backfillProgressEl = document.getElementById("backfill-progress");
  const backfillProgressFill = document.getElementById("backfill-progress-fill");
  const backfillProgressLabel = document.getElementById("backfill-progress-label");
  const backfillLogEl = document.getElementById("backfill-log");
  const backfillLogEmptyEl = document.getElementById("backfill-log-empty");

  const COUNT_FIELDS = [
    "remaining_uris",
    "batches",
    "requests_estimate",
    "resolved_tracks",
    "aliases",
    "failed_uris",
    "listening_uris",
    "album_page_uris",
    "album_backfill_uris",
    "reconcilable",
    "review_uris",
    "requests",
  ];

  const JOB_NAMES = {
    snapshot: "a snapshot pull",
    history_import: "a play-history import",
    roundtrip: "a round-trip",
    backfill: "an album backfill",
  };

  // Whether this page has actually watched a run, so a finished_at left over
  // from an earlier run doesn't announce itself on a fresh page load. One
  // flag per job, since either can be the one that's running.
  let sawRunning = false;
  let sawBackfillRunning = false;

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

  function setQueueControls(status) {
    listeningExcludedNote.hidden = !status.listening_muted;
    listeningClearBtn.hidden = status.listening_muted;
    listeningReaddBtn.hidden = !status.listening_muted;
    document.querySelectorAll("[data-wanted-clear]").forEach((btn) => {
      const field = btn.dataset.wantedClear === "album" ? "album_page_uris" : "album_backfill_uris";
      btn.disabled = !status[field];
    });
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

  // ---------- album backfill status (spec M §4.5/§4.6) ----------
  // Driven from the same poll loop as the round-trip itself -- see poll()
  // below -- rather than a second setTimeout chain, so the two can never
  // drift out of step.

  function backfillPhaseLabel(status) {
    let label = `${status.albums_done}/${status.albums_total} albums`;
    if (status.current_album) label += ` — ${status.current_album}`;
    return `${label} · ${status.uris_queued} uri(s) queued, ${status.requests} requests spent`;
  }

  function renderBackfillLog(entries) {
    const following =
      backfillLogEl.scrollHeight - backfillLogEl.scrollTop - backfillLogEl.clientHeight < 24;
    backfillLogEl.textContent = "";
    entries.forEach((entry) => {
      const li = document.createElement("li");
      const ts = makeDateSpan(entry.ts);
      ts.className = "event-log-ts";
      li.appendChild(ts);
      li.appendChild(document.createTextNode(entry.message));
      backfillLogEl.appendChild(li);
    });
    backfillLogEmptyEl.hidden = entries.length > 0;
    if (following) backfillLogEl.scrollTop = backfillLogEl.scrollHeight;
  }

  function showBackfillDone(status) {
    backfillProgressLabel.hidden = false;
    backfillProgressLabel.textContent = "";
    backfillError.hidden = true;
    const totals =
      `${status.albums_done}/${status.albums_total} album(s), ` +
      `${status.uris_queued} uri(s) queued, ${status.requests} requests spent`;

    const summary = document.createElement("span");
    if (status.outcome === "rate_limited") {
      summary.textContent = `Rate limited after ${totals} — retry `;
      backfillProgressLabel.appendChild(summary);
      if (status.retry_at) backfillProgressLabel.appendChild(makeDateSpan(status.retry_at));
    } else if (status.outcome === "error") {
      summary.textContent = `Run failed after ${totals}: ${status.error}`;
      backfillProgressLabel.appendChild(summary);
    } else if (status.outcome === "stopped") {
      summary.textContent = `Stopped — ${totals}.`;
      backfillProgressLabel.appendChild(summary);
    } else {
      summary.textContent = `Finished — ${totals}.`;
      backfillProgressLabel.appendChild(summary);
    }

    const reloadLink = document.createElement("a");
    reloadLink.href = "#";
    reloadLink.textContent = " Reload to see the fresh queue and Add-button estimates";
    reloadLink.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.reload();
    });
    backfillProgressLabel.appendChild(reloadLink);
  }

  function handleBackfillStatus(status) {
    const otherJob = status.active_job && status.active_job !== "backfill";
    document.querySelectorAll("[data-backfill-add]").forEach((btn) => {
      // dataset.empty is the server-rendered "0 albums in scope" fact, which
      // stays true until a reload re-derives it -- active_job is the only
      // part of this that's meant to change live.
      btn.disabled = Boolean(status.active_job) || btn.dataset.empty === "1";
    });
    backfillStopBtn.disabled = !status.running || status.stopping;
    backfillStopBtn.textContent = status.stopping ? "Stopping…" : "Stop";
    backfillBusyNote.hidden = !otherJob;
    if (otherJob) {
      backfillBusyNote.textContent = `${JOB_NAMES[status.active_job] || status.active_job} is running — one job at a time.`;
    }
    backfillProgressEl.hidden = !status.running;
    renderBackfillLog(status.log || []);

    if (status.running) {
      sawBackfillRunning = true;
      backfillProgressLabel.hidden = false;
      backfillProgressLabel.textContent = backfillPhaseLabel(status);
      const pct = status.albums_total
        ? Math.round((status.albums_done / status.albums_total) * 100)
        : 0;
      backfillProgressFill.style.width = `${pct}%`;
    } else if (status.finished_at && sawBackfillRunning) {
      showBackfillDone(status);
      sawBackfillRunning = false;
    }
  }

  function poll() {
    // One loop drives both the round-trip's own status and the backfill
    // job's -- a second independent setTimeout chain would be two things to
    // keep in step, and only one job can ever be active at a time anyway.
    Promise.all([api("/api/roundtrip/status"), api("/api/backfill/status")])
      .then(([status, backfillStatus]) => {
        COUNT_FIELDS.forEach((name) => setField(name, status[name]));
        renderLog(status.log || []);

        if (status.running) sawRunning = true;
        setControls(status);
        setQueueControls(status);

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

        handleBackfillStatus(backfillStatus);

        // Keep polling while another job holds the slot, so buttons come
        // back on their own once it finishes.
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

  // ---------- round-trip queue box ----------
  // Every clear/mute fires on one click, no confirm step (spec M §4.6) --
  // each is reversible for free, so a two-step confirm would be friction for
  // nothing. Re-polls once on success so the counts update without a reload.

  document.querySelectorAll("[data-wanted-clear]").forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.disabled = true;
      api("/api/roundtrip/wanted/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: btn.dataset.wantedClear }),
      })
        .then(() => poll())
        .catch(() => {
          btn.disabled = false;
        });
    });
  });

  function setListeningMuted(muted, button) {
    button.disabled = true;
    api("/api/roundtrip/listening/mute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ muted }),
    })
      .then(() => poll())
      .catch(() => {
        button.disabled = false;
      });
  }

  listeningClearBtn.addEventListener("click", () => setListeningMuted(true, listeningClearBtn));
  listeningReaddBtn.addEventListener("click", () => setListeningMuted(false, listeningReaddBtn));

  // ---------- album backfill ----------

  document.querySelectorAll("[data-backfill-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
      backfillError.hidden = true;
      btn.disabled = true;
      api("/api/backfill/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ generations: parseInt(btn.dataset.backfillAdd, 10) }),
      })
        .then((data) => {
          if (data.error) {
            backfillError.hidden = false;
            backfillError.textContent = data.detail ? `${data.error} (${data.detail})` : data.error;
            btn.disabled = false;
            return;
          }
          sawBackfillRunning = true;
          poll();
        })
        .catch((e) => {
          backfillError.hidden = false;
          backfillError.textContent = `Request failed: ${e}. The dev server may have restarted — try again.`;
          btn.disabled = false;
        });
    });
  });

  backfillStopBtn.addEventListener("click", () => {
    backfillStopBtn.disabled = true;
    backfillStopBtn.textContent = "Stopping…";
    api("/api/backfill/stop", { method: "POST" }).catch(() => {});
  });

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

  // Pick up a run already going (a page reload mid-run, round-trip or
  // backfill), and learn on load whether another job is currently blocking
  // the start/Add buttons.
  poll();
})();
