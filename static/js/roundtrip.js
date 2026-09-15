(() => {
  const startBtn = document.getElementById("roundtrip-start-btn");
  if (!startBtn) return;

  const stopBtn = document.getElementById("roundtrip-stop-btn");
  const reconcileBtn = document.getElementById("roundtrip-reconcile-btn");
  const clearBtn = document.getElementById("roundtrip-clear-failures-btn");
  const busyNote = document.getElementById("roundtrip-busy-note");
  const errorEl = document.getElementById("roundtrip-error");
  const progressFill = document.getElementById("roundtrip-progress-fill");
  const logEl = document.getElementById("roundtrip-log");
  const logEmptyEl = document.getElementById("roundtrip-log-empty");

  // ---------- sticky action bar (ui-framework-W.md §9.9) ----------
  // Three states: idle (counts + start buttons), live (progress + newest feed
  // line + Stop), done (that line + Reload). Both jobs drive the one bar.
  const barIdle = document.getElementById("rt-bar-idle");
  const barLive = document.getElementById("rt-bar-live");
  const liveLine = document.getElementById("rt-live-line");
  const reloadBtn = document.getElementById("rt-reload-btn");

  // Which job the bar is currently showing, so the single Stop button knows
  // which endpoint to hit.
  let liveJob = null;

  // ---------- round-trip queue box (spec M §4.6) ----------
  const listeningExcludedNote = document.getElementById("listening-excluded-note");
  const listeningClearBtn = document.getElementById("listening-clear-btn");
  const listeningReaddBtn = document.getElementById("listening-readd-btn");
  const incompleteIsrcClearBtn = document.getElementById("incomplete-isrc-clear-btn");

  // The backfill has no controls of its own beyond its Add buttons: its
  // progress, its feed, its Stop and its errors are all the shared ones above.
  const backfillError = errorEl;

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
    "incomplete_isrc_uris",
    "reconcilable",
    "review_uris",
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
    busyNote.hidden = !otherJob;
    if (otherJob) {
      busyNote.textContent = `${JOB_NAMES[status.active_job] || status.active_job} is running. One job at a time.`;
    }
  }

  function setBarState(state) {
    barIdle.hidden = state !== "idle";
    barLive.hidden = state === "idle";
    stopBtn.hidden = state !== "live";
    reloadBtn.hidden = state !== "done";
  }

  function setQueueControls(status) {
    listeningExcludedNote.hidden = !status.listening_muted;
    listeningClearBtn.hidden = status.listening_muted;
    // Muting a row with nothing in it does nothing -- the other three rows
    // always had this and the listening one never did.
    listeningClearBtn.disabled = !status.listening_uris;
    listeningReaddBtn.hidden = !status.listening_muted;
    document.querySelectorAll("[data-wanted-clear]").forEach((btn) => {
      const field = btn.dataset.wantedClear === "album" ? "album_page_uris" : "album_backfill_uris";
      btn.disabled = !status[field];
    });
    incompleteIsrcClearBtn.disabled = !status.incomplete_isrc_uris;
  }

  function phaseLabel(status) {
    if (status.phase === "guard") return "Verifying the loader playlist…";
    if (status.phase === "clearing") return "Clearing the loader playlist…";
    let label = `Batch ${status.batch_done}/${status.batch_total}`;
    if (status.current) label += ` · ${status.current}`;
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

  function renderFeed(rtEntries, backfillEntries) {
    // One feed for both jobs. Each entry keeps its source so a backfill run and
    // a round-trip run are distinguishable in the same list; they are merged by
    // timestamp rather than concatenated.
    const entries = [
      ...rtEntries.map((e) => ({ ...e, source: "round-trip" })),
      ...backfillEntries.map((e) => ({ ...e, source: "backfill" })),
    ].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
    renderLog(entries);
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
      if (entry.source) {
        const tag = document.createElement("span");
        tag.className = "badge";
        tag.textContent = entry.source;
        li.appendChild(tag);
        li.appendChild(document.createTextNode(" "));
      }
      li.appendChild(document.createTextNode(entry.message));
      logEl.appendChild(li);
    });
    logEmptyEl.hidden = entries.length > 0;
    if (following) logEl.scrollTop = logEl.scrollHeight;
  }

  function showDone(status) {
    liveLine.textContent = "";
    errorEl.hidden = true; // the terminal state is fully described here

    const totals =
      `${status.uris_stored.toLocaleString()} tracks stored, ` +
      `${status.aliases_created} aliased, ${status.uris_failed} URIs failed, ` +
      `${status.requests} requests spent`;

    const summary = document.createElement("span");
    if (status.outcome === "rate_limited") {
      summary.textContent = `Rate limited after ${totals}. Retry `;
      liveLine.appendChild(summary);
      if (status.retry_at) liveLine.appendChild(makeDateSpan(status.retry_at));
    } else if (status.outcome === "error") {
      summary.textContent = `Run failed after ${totals}: ${status.error}`;
      liveLine.appendChild(summary);
    } else if (status.outcome === "stopped") {
      // A deliberate stop is not a fault and must not render as one.
      summary.textContent = `Stopped after ${totals}.`;
      liveLine.appendChild(summary);
    } else if (status.outcome === "breaker") {
      summary.textContent = `Stopped by the circuit breaker (three consecutive failed batches) after ${totals}.`;
      liveLine.appendChild(summary);
    } else {
      summary.textContent = `Run finished: ${totals}.`;
      liveLine.appendChild(summary);
    }

    if (status.left_in_playlist) {
      const left = document.createElement("span");
      left.textContent = ` ${status.left_in_playlist} item(s) were left in the loader playlist. Clear them by hand in Spotify if you want to.`;
      liveLine.appendChild(left);
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
        li.appendChild(document.createTextNode(`: ${f.reason}`));
        list.appendChild(li);
      });
      liveLine.appendChild(list);
    }

  }

  // ---------- album backfill status (spec M §4.5/§4.6) ----------
  // Driven from the same poll loop as the round-trip itself -- see poll()
  // below -- rather than a second setTimeout chain, so the two can never
  // drift out of step.

  function backfillPhaseLabel(status) {
    let label = `${status.albums_done}/${status.albums_total} albums`;
    if (status.current_album) label += ` · ${status.current_album}`;
    return `${label} · ${status.uris_queued} uri(s) queued, ${status.requests} requests spent`;
  }

  function showBackfillDone(status) {
    liveLine.textContent = "";
    backfillError.hidden = true;
    const totals =
      `${status.albums_done}/${status.albums_total} album(s), ` +
      `${status.uris_queued} uri(s) queued, ${status.requests} requests spent`;

    const summary = document.createElement("span");
    if (status.outcome === "rate_limited") {
      summary.textContent = `Rate limited after ${totals}. Retry `;
      liveLine.appendChild(summary);
      if (status.retry_at) liveLine.appendChild(makeDateSpan(status.retry_at));
    } else if (status.outcome === "error") {
      summary.textContent = `Run failed after ${totals}: ${status.error}`;
      liveLine.appendChild(summary);
    } else if (status.outcome === "stopped") {
      summary.textContent = `Stopped after ${totals}.`;
      liveLine.appendChild(summary);
    } else {
      summary.textContent = `Finished: ${totals}.`;
      liveLine.appendChild(summary);
    }

  }

  function handleBackfillStatus(status) {
    document.querySelectorAll("[data-backfill-add]").forEach((btn) => {
      // dataset.empty is the server-rendered "0 albums in scope" fact, which
      // stays true until a reload re-derives it -- active_job is the only
      // part of this that's meant to change live.
      btn.disabled = Boolean(status.active_job) || btn.dataset.empty === "1";
    });
  }

  function poll() {
    // One loop drives both the round-trip's own status and the backfill
    // job's -- a second independent setTimeout chain would be two things to
    // keep in step, and only one job can ever be active at a time anyway.
    Promise.all([api("/api/roundtrip/status"), api("/api/backfill/status")])
      .then(([status, backfillStatus]) => {
        COUNT_FIELDS.forEach((name) => setField(name, status[name]));
        renderFeed(status.log || [], backfillStatus.log || []);

        if (status.running) sawRunning = true;
        if (backfillStatus.running) sawBackfillRunning = true;
        setControls(status);
        setQueueControls(status);
        handleBackfillStatus(backfillStatus);

        const live = status.running ? status : backfillStatus.running ? backfillStatus : null;
        if (live) {
          liveJob = status.running ? "roundtrip" : "backfill";
          setBarState("live");
          liveLine.textContent = status.running
            ? phaseLabel(status)
            : backfillPhaseLabel(backfillStatus);
          const pct = status.running
            ? (status.batch_total ? Math.round((status.batch_done / status.batch_total) * 100) : 0)
            : (backfillStatus.albums_total
                ? Math.round((backfillStatus.albums_done / backfillStatus.albums_total) * 100)
                : 0);
          progressFill.style.width = `${pct}%`;
          stopBtn.disabled = live.stopping;
          stopBtn.textContent = live.stopping ? "Stopping…" : "Stop";
        } else if (sawRunning && status.finished_at) {
          showDone(status);
          setBarState("done");
          sawRunning = false;
        } else if (sawBackfillRunning && backfillStatus.finished_at) {
          showBackfillDone(backfillStatus);
          setBarState("done");
          sawBackfillRunning = false;
        }

        // Reschedule on evidence from EITHER payload. `active_job` is stamped
        // onto each response as it is served, and these are two concurrent
        // requests -- so a job that released the slot between them came back as
        // `active_job: null` here while the backfill payload still read
        // "running". The loop then painted that stale frame and stopped
        // forever, leaving the bar frozen mid-run with Stop still live.
        if (status.active_job || status.running || backfillStatus.running) {
          setTimeout(poll, 1000);
        }
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
        errorEl.textContent = `Request failed: ${e}. The dev server may have restarted. Try again.`;
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

  incompleteIsrcClearBtn.addEventListener("click", () => {
    incompleteIsrcClearBtn.disabled = true;
    api("/api/roundtrip/incomplete-isrc/clear", { method: "POST" })
      .then(() => poll())
      .catch(() => {
        incompleteIsrcClearBtn.disabled = false;
      });
  });

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
          backfillError.textContent = `Request failed: ${e}. The dev server may have restarted. Try again.`;
          btn.disabled = false;
        });
    });
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
    // stop waits for the current batch to finish and commit. One button for
    // both jobs, so it has to ask which one it is stopping.
    stopBtn.disabled = true;
    stopBtn.textContent = "Stopping…";
    const path = liveJob === "backfill" ? "/api/backfill/stop" : "/api/roundtrip/stop";
    api(path, { method: "POST" }).catch(() => {});
  });

  reloadBtn.addEventListener("click", () => window.location.reload());

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
