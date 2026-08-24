(() => {
  const pollBtn = document.getElementById("scrobble-poll-btn");
  if (!pollBtn) return;

  const pauseBtn = document.getElementById("scrobble-pause-btn");
  const resumeBtn = document.getElementById("scrobble-resume-btn");
  const errorEl = document.getElementById("scrobble-error");
  const enabledStateEl = document.getElementById("scrobble-enabled-state");
  const lastPollEl = document.getElementById("scrobble-last-poll");
  const nextPollEl = document.getElementById("scrobble-next-poll");

  function api(path, options) {
    return fetch(path, { method: "POST", ...options }).then((r) =>
      r.json().then((data) => ({ ok: r.ok, data }))
    );
  }

  function setField(name, value) {
    document.querySelectorAll(`[data-field="${name}"]`).forEach((el) => {
      el.textContent = typeof value === "number" ? value.toLocaleString() : value;
    });
    document.querySelectorAll(`[data-plural-for="${name}"]`).forEach((el) => {
      el.textContent = value === 1 ? "" : "s";
    });
  }

  function renderLastPoll(lastPoll) {
    lastPollEl.textContent = "";
    if (!lastPoll) {
      lastPollEl.textContent = "No poll has run yet.";
      return;
    }
    lastPollEl.appendChild(document.createTextNode("Last poll "));
    lastPollEl.appendChild(makeDateSpan(lastPoll.started_at));
    if (lastPoll.error) {
      lastPollEl.appendChild(document.createTextNode(" · "));
      const span = document.createElement("span");
      span.className = "error";
      span.textContent = `failed: ${lastPoll.error}`;
      lastPollEl.appendChild(span);
    } else if (lastPoll.retry_after) {
      lastPollEl.appendChild(document.createTextNode(" · "));
      const span = document.createElement("span");
      span.className = "error";
      span.textContent = `rate limited, backing off ${lastPoll.retry_after}s`;
      lastPollEl.appendChild(span);
    } else {
      lastPollEl.appendChild(
        document.createTextNode(` · read ${lastPoll.items_read}, stored ${lastPoll.rows_inserted}`)
      );
      if (lastPoll.gap_warning) {
        lastPollEl.appendChild(document.createTextNode(" · "));
        const span = document.createElement("span");
        span.className = "error";
        span.textContent = "gap warning — some plays may be missing; re-import an export to recover";
        lastPollEl.appendChild(span);
      }
    }
    if (lastPoll.rows_inserted) {
      const link = document.createElement("a");
      link.href = "#";
      link.textContent = ` Reload to see the ${lastPoll.rows_inserted} new play(s)`;
      link.addEventListener("click", (e) => {
        e.preventDefault();
        window.location.reload();
      });
      lastPollEl.appendChild(link);
    }
  }

  function setEnabledUi(enabled) {
    enabledStateEl.textContent = enabled ? "Enabled" : "Paused";
    pauseBtn.hidden = !enabled;
    resumeBtn.hidden = enabled;
  }

  // Three states, matching the template: no thread scheduled in this process
  // (the laptop's permanent case), a real forthcoming poll, or a scheduled
  // wake-up that will skip because scrobbling is paused. The time is exact
  // rather than relative -- it is a fixed sleep, known to the second.
  function renderNextPoll(data) {
    nextPollEl.textContent = "";
    if (!data.next_poll_at) {
      nextPollEl.textContent =
        "No poller running in this process — polls happen on the deployed server only.";
      return;
    }
    const when = makeExactDateSpan(data.next_poll_at);
    if (data.enabled) {
      nextPollEl.appendChild(document.createTextNode("Next poll at "));
      nextPollEl.appendChild(when);
    } else {
      nextPollEl.appendChild(document.createTextNode("Paused — the "));
      nextPollEl.appendChild(when);
      nextPollEl.appendChild(document.createTextNode(" wake-up will skip without polling."));
    }
  }

  function applyStatus(data) {
    setField("total_scrobbles", data.total_scrobbles);
    setField("gap_warning_count", data.gap_warning_count);
    renderLastPoll(data.last_poll);
    setEnabledUi(data.enabled);
    renderNextPoll(data);
  }

  pollBtn.addEventListener("click", () => {
    pollBtn.disabled = true;
    errorEl.hidden = true;
    api("/api/scrobble/poll")
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.detail || data.error || "poll failed");
        applyStatus(data);
      })
      .catch((e) => {
        errorEl.hidden = false;
        errorEl.textContent = e.message;
      })
      .finally(() => {
        pollBtn.disabled = false;
      });
  });

  function setEnabled(enabled, button) {
    button.disabled = true;
    errorEl.hidden = true;
    api("/api/scrobble/toggle", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    })
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.detail || data.error || "toggle failed");
        // The toggle returns the same status payload the poll does, so the
        // next-poll line can re-render with it -- pausing changes what that
        // line says, not just which button shows.
        applyStatus(data);
      })
      .catch((e) => {
        errorEl.hidden = false;
        errorEl.textContent = e.message;
      })
      .finally(() => {
        button.disabled = false;
      });
  }

  pauseBtn.addEventListener("click", () => setEnabled(false, pauseBtn));
  resumeBtn.addEventListener("click", () => setEnabled(true, resumeBtn));
})();
