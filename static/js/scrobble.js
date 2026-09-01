(() => {
  const pollBtn = document.getElementById("scrobble-poll-btn");
  if (!pollBtn) return;

  const pauseBtn = document.getElementById("scrobble-pause-btn");
  const resumeBtn = document.getElementById("scrobble-resume-btn");
  const errorEl = document.getElementById("scrobble-error");
  const enabledStateEl = document.getElementById("scrobble-enabled-state");
  const lastPollEl = document.getElementById("scrobble-last-poll");
  const nextPollEl = document.getElementById("scrobble-next-poll");
  const gapStatEl = document.getElementById("gap-warning-stat");
  const playsBodyEl = document.getElementById("scrobble-plays-body");
  const playsCountEl = document.getElementById("plays-count");
  const playsCollapseEl = document.getElementById("plays-body");

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
      span.className = "warn";
      span.textContent = `rate limited, backing off ${lastPoll.retry_after}s`;
      lastPollEl.appendChild(span);
    } else {
      lastPollEl.appendChild(
        document.createTextNode(` · read ${lastPoll.items_read}, stored ${lastPoll.rows_inserted}`)
      );
      if (lastPoll.gap_warning) {
        lastPollEl.appendChild(document.createTextNode(" · "));
        const span = document.createElement("span");
        span.className = "warn";
        span.textContent =
          "gap warning: plays may be missing, re-import an export to recover";
        lastPollEl.appendChild(span);
      }
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
      nextPollEl.textContent = "No poller running in this process.";
      return;
    }
    const when = makeExactDateSpan(data.next_poll_at);
    if (data.enabled) {
      nextPollEl.appendChild(document.createTextNode("Next poll at "));
      nextPollEl.appendChild(when);
    } else {
      nextPollEl.appendChild(document.createTextNode("Paused: the "));
      nextPollEl.appendChild(when);
      nextPollEl.appendChild(document.createTextNode(" wake-up will skip without polling."));
    }
  }

  // The table is server-rendered from the same fragment the page includes,
  // so a poll's rows and a page load's rows cannot drift apart. It is absent
  // until the first play exists, which is the one case the page renders a
  // placeholder paragraph instead of a table.
  function renderPlays(data) {
    if (!playsBodyEl || !data.plays_html) return;
    playsBodyEl.innerHTML = data.plays_html;
    // format.js fills these in on DOMContentLoaded, which has long since
    // fired -- without this the whole When column comes back as raw ISO
    // strings. Its `root` argument exists for precisely this case.
    applyRelativeTimes(playsBodyEl);
    if (playsCountEl) playsCountEl.textContent = data.recent_plays.length;
  }

  function applyStatus(data) {
    setField("total_scrobbles", data.total_scrobbles);
    setField("gap_warning_count", data.gap_warning_count);
    // Warning colour only while there is something to warn about; the
    // template stamps the same class on first render.
    if (gapStatEl) gapStatEl.classList.toggle("warn", data.gap_warning_count > 0);
    renderLastPoll(data.last_poll);
    setEnabledUi(data.enabled);
    renderNextPoll(data);
    renderPlays(data);
  }

  pollBtn.addEventListener("click", () => {
    pollBtn.disabled = true;
    errorEl.hidden = true;
    api("/api/scrobble/poll")
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.detail || data.error || "poll failed");
        applyStatus(data);
        // Only on a poll, not on a toggle: this is the result you asked for.
        // getOrCreateInstance rather than a `show` class, so Bootstrap's own
        // state stays in step with the heading's aria-expanded.
        if (playsCollapseEl && window.bootstrap) {
          bootstrap.Collapse.getOrCreateInstance(playsCollapseEl).show();
        }
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
