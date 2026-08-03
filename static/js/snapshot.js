(() => {
  function api(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  // ---------- sortable tables (playlist / track detail pages) ----------

  document.querySelectorAll("table.sortable").forEach((table) => {
    const tbody = table.querySelector("tbody");
    table.querySelectorAll("th[data-sort]").forEach((th, colIndex) => {
      let ascending = true;
      th.addEventListener("click", () => {
        const rows = Array.from(tbody.querySelectorAll("tr"));
        const type = th.dataset.sort;
        rows.sort((a, b) => {
          if (type === "date") {
            // Compare the underlying ISO timestamp, not the relative-time
            // text (which doesn't sort chronologically as a string).
            const ael = a.children[colIndex].querySelector("[data-datetime]");
            const bel = b.children[colIndex].querySelector("[data-datetime]");
            const av = ael ? ael.dataset.datetime : "";
            const bv = bel ? bel.dataset.datetime : "";
            return av < bv ? -1 : av > bv ? 1 : 0;
          }
          const av = a.children[colIndex].textContent.trim();
          const bv = b.children[colIndex].textContent.trim();
          if (type === "num") {
            return (parseFloat(av) || 0) - (parseFloat(bv) || 0);
          }
          return av.localeCompare(bv);
        });
        if (!ascending) rows.reverse();
        ascending = !ascending;
        rows.forEach((row) => tbody.appendChild(row));
      });
    });
  });

  // ---------- exclude toggles (index playlist table + playlist detail header) ----------

  document.querySelectorAll("[data-exclude-toggle]").forEach((el) => {
    el.addEventListener("change", () => {
      const excluded = el.checked;
      api("/api/snapshot/exclude", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ playlist_ids: [el.dataset.playlistId], excluded }),
      }).catch(() => {
        el.checked = !excluded;
      });
    });
  });

  // ---------- pull / refresh controls (index page) ----------

  const pullBtn = document.getElementById("pull-btn");
  const refreshBtn = document.getElementById("refresh-btn");
  if (!pullBtn && !refreshBtn) return;

  const errorEl = document.getElementById("snapshot-error");
  const progressEl = document.getElementById("snapshot-progress");
  const progressFill = document.getElementById("snapshot-progress-fill");
  const progressLabel = document.getElementById("snapshot-progress-label");
  const hideUncapturableToggle = document.getElementById("hide-uncapturable-toggle");

  // ---------- uncapturable-playlist toggle ----------

  function applyUncapturableFilter() {
    const hide = hideUncapturableToggle && hideUncapturableToggle.checked;
    document.querySelectorAll("#playlist-table tbody tr[data-uncapturable]").forEach((row) => {
      row.hidden = hide;
    });
  }

  if (hideUncapturableToggle) {
    hideUncapturableToggle.addEventListener("change", applyUncapturableFilter);
  }

  function setField(name, value) {
    document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => {
      el.textContent = value;
    });
  }

  function setFailingField(count) {
    setField("playlists_failing", count);
    const wrap = document.getElementById("playlists-failing-wrap");
    if (wrap) wrap.hidden = !count;
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
        el.textContent = "never";
      }
    });
  }

  function setRunning(running) {
    if (pullBtn) pullBtn.disabled = running;
    if (refreshBtn) refreshBtn.disabled = running;
    progressEl.hidden = !running;
  }

  function phaseLabel(status) {
    if (status.phase === "tracks") {
      let label = `Pulling tracks: ${status.run_done}/${status.run_total}`;
      if (status.current_playlist) label += ` (${status.current_playlist})`;
      return label;
    }
    if (status.phase === "liked_songs") return "Pulling Liked Songs…";
    return "Pulling playlists…";
  }

  function makeDateSpan(isoValue) {
    const span = document.createElement("span");
    span.dataset.datetime = isoValue;
    span.title = isoValue;
    span.textContent = formatRelativeTime(isoValue);
    return span;
  }

  function showDone(status) {
    progressLabel.hidden = false;
    progressLabel.textContent = "";
    errorEl.hidden = true; // terminal state is fully described here, not duplicated below

    if (status.error) {
      if (status.retry_at) {
        progressLabel.append("Pull failed: Rate limited by Spotify — retry ");
        progressLabel.appendChild(makeDateSpan(status.retry_at));
      } else {
        const summary = document.createElement("span");
        summary.textContent = `Pull failed: ${status.error}`;
        progressLabel.appendChild(summary);
      }
      return;
    }

    const failed = status.failed_playlists || [];
    const summary = document.createElement("span");
    summary.textContent = failed.length
      ? `Pull finished — ${failed.length} playlist(s) failed:`
      : "Pull finished.";
    progressLabel.appendChild(summary);

    if (failed.length) {
      const list = document.createElement("ul");
      failed.forEach((f) => {
        const li = document.createElement("li");
        li.textContent = `${f.name}: ${f.error}`;
        list.appendChild(li);
      });
      progressLabel.appendChild(list);

      const excludeBtn = document.createElement("button");
      excludeBtn.type = "button";
      excludeBtn.textContent = `Exclude the ${failed.length} playlist${failed.length === 1 ? "" : "s"} that failed`;
      excludeBtn.addEventListener("click", () => {
        if (!window.confirm(`Exclude ${failed.length} playlist${failed.length === 1 ? "" : "s"} from future pulls?`)) {
          return;
        }
        api("/api/snapshot/exclude", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            playlist_ids: failed.map((f) => f.playlist_id),
            excluded: true,
          }),
        }).then(() => window.location.reload());
      });
      progressLabel.appendChild(excludeBtn);
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
    api("/api/snapshot/status").then((status) => {
      setField("playlists_pulled", status.playlists_pulled);
      setField("playlists_total", status.playlists_total);
      setField("playlists_excluded", status.playlists_excluded);
      setFailingField(status.playlists_failing);
      setField("live_memberships", status.live_memberships);
      setDateField("last_full_pull_at", status.last_full_pull_at);
      setDateField("last_refresh_at", status.last_refresh_at);

      setRunning(status.running);

      if (status.running) {
        progressLabel.hidden = false;
        progressLabel.textContent = phaseLabel(status);
        const pct = status.run_total
          ? Math.round((status.run_done / status.run_total) * 100)
          : 0;
        progressFill.style.width = `${pct}%`;
        setTimeout(poll, 1000);
      } else if (status.finished_at) {
        showDone(status);
      }
    }).catch(() => {
      // Transient failure (e.g. dev server restart mid-pull) — keep polling.
      setTimeout(poll, 1000);
    });
  }

  function start(path) {
    errorEl.hidden = true;
    api(path, { method: "POST" })
      .then((data) => {
        if (data.error) {
          errorEl.hidden = false;
          errorEl.textContent = data.error;
          return;
        }
        setRunning(true);
        poll();
      })
      .catch((e) => {
        errorEl.hidden = false;
        errorEl.textContent = `Request failed: ${e}. The dev server may have restarted — try again.`;
      });
  }

  if (pullBtn) pullBtn.addEventListener("click", () => start("/api/snapshot/pull"));
  if (refreshBtn) refreshBtn.addEventListener("click", () => start("/api/snapshot/refresh"));

  // Pick up a pull already running (e.g. kicked off from the Canvas page).
  api("/api/snapshot/status").then((status) => {
    if (status.running) {
      setRunning(true);
      poll();
    }
  });
})();
