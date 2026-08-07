(() => {
  // The reworked cross-artist queue (docs/specs/grouping-catch-up-E.md §4).
  // The whole page exists to make one answer -- "no, none of these are
  // related" -- cost a single Enter, while still letting the rare true
  // positive be attached in a couple of keystrokes.

  function api(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  const progressLabel = document.getElementById("progress-label");
  const progressFill = document.getElementById("progress-fill");
  const itemSection = document.getElementById("cross-item");
  const itemTitle = document.getElementById("item-title");
  const newRowsEl = document.getElementById("new-rows");
  const newEmptyEl = document.getElementById("new-empty");
  const groupRowsEl = document.getElementById("group-rows");
  const groupsEmptyEl = document.getElementById("groups-empty");
  const emptyEl = document.getElementById("cross-empty");
  const doneEl = document.getElementById("cross-done");
  const doneCountEl = document.getElementById("done-count");
  const errorEl = document.getElementById("cross-error");
  const helpToggle = document.getElementById("help-toggle");
  const helpPopover = document.getElementById("help-popover");

  let items = [];
  let index = 0;
  let committedCount = 0;
  let pendingCount = 0;

  // trackId -> target key ("g:<song_id>" or "n:<seq>"). Absent = unassigned.
  let assigned = new Map();
  let newGroupSeq = 0;
  let selection = new Set();
  let focus = null;
  let saving = false;

  function showError(msg) {
    errorEl.hidden = false;
    errorEl.textContent = msg;
  }

  // ---------- Loading ----------

  function loadQueue() {
    api("/api/canonical/cross").then((data) => {
      if (data.error) {
        showError(data.detail || data.error);
        return;
      }
      items = data.items || [];
      pendingCount = data.pending_count || 0;
      if (!items.length) {
        emptyEl.hidden = false;
        updateProgress();
        return;
      }
      index = 0;
      loadItem();
    });
  }

  function loadItem() {
    assigned = new Map();
    newGroupSeq = 0;
    selection = new Set();
    saving = false;
    const order = focusOrder();
    focus = order.length ? order[0] : null;
    itemSection.hidden = false;
    emptyEl.hidden = true;
    doneEl.hidden = true;
    render();
  }

  // ---------- Assignment state ----------

  function item() {
    return items[index];
  }

  function assignableIds() {
    return item().new_tracks.map((t) => t.track_id);
  }

  // Visual order, so ArrowDown always lands where the eye expects: the
  // unassigned rows first, then each target's assigned rows in target order.
  function focusOrder() {
    const it = item();
    if (!it) return [];
    const unassigned = assignableIds().filter((tid) => !assigned.has(tid));
    const byTarget = [];
    for (const key of targetKeys()) {
      byTarget.push(...assignableIds().filter((tid) => assigned.get(tid) === key));
    }
    return [...unassigned, ...byTarget];
  }

  function targetKeys() {
    const keys = item().groups.map((g) => `g:${g.song_id}`);
    for (const key of assigned.values()) {
      if (key.startsWith("n:") && !keys.includes(key)) keys.push(key);
    }
    return keys;
  }

  function assignSelectionTo(key) {
    if (!selection.size) return;
    for (const tid of selection) assigned.set(tid, key);
    selection = new Set();
    render();
  }

  function assignToNthGroup(n) {
    const groups = item().groups;
    if (n < 1 || n > groups.length) return;
    assignSelectionTo(`g:${groups[n - 1].song_id}`);
  }

  function groupSelectedTogether() {
    // Only worth a group when there are at least two of them -- one track
    // "grouped" with itself is the state it is already in.
    if (selection.size < 2) return;
    newGroupSeq += 1;
    assignSelectionTo(`n:${newGroupSeq}`);
  }

  function unassignFocused() {
    if (focus && assigned.has(focus)) {
      assigned.delete(focus);
      render();
    }
  }

  function resetAssignments() {
    assigned = new Map();
    selection = new Set();
    render();
  }

  // ---------- Rendering ----------

  function formatDuration(ms) {
    if (ms == null) return "";
    const total = Math.round(ms / 1000);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function truncateList(values, limit) {
    if (!values.length) return "";
    const shown = values.slice(0, limit).join(", ");
    return values.length > limit ? `${shown}…` : shown;
  }

  function coverImg(url) {
    const img = document.createElement("img");
    img.className = "cover";
    if (url) img.src = url;
    return img;
  }

  function span(text, cls) {
    const el = document.createElement("span");
    el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }

  function badge(text, cls) {
    const el = document.createElement("span");
    el.className = `badge ${cls || ""}`.trim();
    el.textContent = text;
    return el;
  }

  function trackRow(t, opts) {
    const row = document.createElement("div");
    row.className = "cross-row";
    row.dataset.trackId = t.track_id;
    if (opts.nested) row.classList.add("nested");
    if (opts.selectable) {
      if (selection.has(t.track_id)) row.classList.add("selected");
      if (t.track_id === focus) row.classList.add("focused");
    }

    if (opts.selectable) {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selection.has(t.track_id);
      checkbox.addEventListener("click", (e) => {
        e.stopPropagation();
        focus = t.track_id;
        toggleSelection(t.track_id);
      });
      row.appendChild(checkbox);
      row.addEventListener("click", () => {
        focus = t.track_id;
        toggleSelection(t.track_id);
      });
    } else {
      row.appendChild(span("↳", "cross-nest-marker"));
    }

    row.appendChild(coverImg(t.album_image_url));
    const title = span(t.title, "cross-title");
    if (t.explicit) {
      title.appendChild(document.createTextNode(" "));
      title.appendChild(badge("E", "explicit"));
    }
    row.appendChild(title);
    row.appendChild(span(t.artists, "cross-artists"));
    row.appendChild(span(t.album || "", "cross-album"));
    row.appendChild(span(formatDuration(t.duration_ms), "muted"));
    row.appendChild(span(t.isrc || "—", "muted"));
    if (!t.live_count) row.appendChild(badge("not in library", "muted"));
    if (opts.state) {
      row.appendChild(
        badge(
          opts.state === "decided" ? "already decided in main" : "already queued in main",
          "muted"
        )
      );
    }
    return row;
  }

  function groupRow(group, n) {
    const row = document.createElement("div");
    row.className = "cross-group-head";
    row.appendChild(span(String(n), "cross-key"));
    row.appendChild(coverImg(group.representative ? group.representative.album_image_url : null));
    row.appendChild(span(group.representative ? group.representative.title : "?", "cross-title"));
    row.appendChild(span(`${group.track_count} track${group.track_count === 1 ? "" : "s"}`, "muted"));
    row.appendChild(span(truncateList(group.artists, 3), "cross-artists"));
    row.appendChild(span(truncateList(group.albums, 3), "cross-album"));
    row.addEventListener("click", () => assignToNthGroup(n));
    return row;
  }

  function render() {
    const it = item();
    if (!it) return;

    itemTitle.textContent = it.base;

    // New songs — only the ones still unassigned.
    newRowsEl.innerHTML = "";
    const unassigned = it.new_tracks.filter((t) => !assigned.has(t.track_id));
    for (const t of unassigned) {
      newRowsEl.appendChild(trackRow(t, { selectable: true }));
    }
    newEmptyEl.hidden = unassigned.length > 0;

    // Existing groups, plus any ad-hoc new groups made with `g`.
    groupRowsEl.innerHTML = "";
    const byId = new Map(it.new_tracks.map((t) => [t.track_id, t]));

    it.groups.forEach((group, i) => {
      const block = document.createElement("div");
      block.className = "cross-group";
      block.appendChild(groupRow(group, i + 1));
      for (const nested of group.nested) {
        block.appendChild(trackRow(nested, { nested: true, state: nested.state }));
      }
      for (const [tid, key] of assigned) {
        if (key === `g:${group.song_id}`) {
          block.appendChild(trackRow(byId.get(tid), { nested: true, selectable: true }));
        }
      }
      groupRowsEl.appendChild(block);
    });

    for (const key of targetKeys().filter((k) => k.startsWith("n:"))) {
      const block = document.createElement("div");
      block.className = "cross-group";
      const head = document.createElement("div");
      head.className = "cross-group-head";
      head.appendChild(span("new", "cross-key"));
      head.appendChild(span("New song group", "cross-title"));
      block.appendChild(head);
      for (const [tid, target] of assigned) {
        if (target === key) {
          block.appendChild(trackRow(byId.get(tid), { nested: true, selectable: true }));
        }
      }
      groupRowsEl.appendChild(block);
    }

    groupsEmptyEl.hidden = it.groups.length > 0;
    updateProgress();
  }

  function updateProgress() {
    const total = items.length;
    progressLabel.textContent = `${committedCount} / ${total} decided`;
    progressFill.style.width = total ? `${(committedCount / total) * 100}%` : "0%";
  }

  // ---------- Selection / focus ----------

  function toggleSelection(tid) {
    if (selection.has(tid)) selection.delete(tid);
    else selection.add(tid);
    render();
  }

  function moveFocus(delta) {
    const order = focusOrder();
    if (!order.length) return;
    let i = order.indexOf(focus);
    if (i === -1) i = 0;
    else i = Math.max(0, Math.min(order.length - 1, i + delta));
    focus = order[i];
    render();
    const row = document.querySelector(`.cross-row[data-track-id="${CSS.escape(focus)}"]`);
    if (row) row.scrollIntoView({ block: "nearest" });
  }

  // ---------- Commit / navigation ----------

  function buildAssignments() {
    const byTarget = new Map();
    for (const [tid, key] of assigned) {
      if (!byTarget.has(key)) byTarget.set(key, []);
      byTarget.get(key).push(tid);
    }
    return [...byTarget].map(([key, trackIds]) => ({
      song_id: key.startsWith("g:") ? parseInt(key.slice(2), 10) : null,
      track_ids: trackIds,
    }));
  }

  function commit() {
    if (saving) return;
    saving = true;
    const it = item();
    api("/api/canonical/cross/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_ids: it.track_ids, assignments: buildAssignments() }),
    }).then((result) => {
      saving = false;
      if (result.error) {
        showError(result.detail || result.error);
        return;
      }
      committedCount += 1;
      pendingCount += buildAssignments().reduce((n, a) => n + a.track_ids.length, 0);
      advance();
    });
  }

  function advance() {
    if (index < items.length - 1) {
      index += 1;
      loadItem();
      updateProgress();
      return;
    }
    finishQueue();
  }

  function finishQueue() {
    // The redirect Finn described: the tier pass is the other half of any
    // assignment made here, and it should not have to be gone looking for.
    if (pendingCount) {
      window.location.href = "/dev/canonical/review?queue=pending";
      return;
    }
    itemSection.hidden = true;
    doneEl.hidden = false;
    doneCountEl.textContent = String(committedCount);
    updateProgress();
  }

  function goBack() {
    if (index === 0) return;
    const previous = items[index - 1];
    // Re-fetch rather than re-rendering the stale copy: the previous bucket
    // was saved, so every pair in it is now reviewed and every track
    // established -- which renders as exactly what the decision produced.
    api("/api/canonical/cross?tracks=" + encodeURIComponent(previous.track_ids.join(","))).then(
      (data) => {
        if (data.error || !data.items || !data.items.length) {
          showError((data && (data.detail || data.error)) || "could not reload previous bucket");
          return;
        }
        const fresh = data.items[0];
        fresh.key = previous.key;
        items[index - 1] = fresh;
        index -= 1;
        committedCount = Math.max(0, committedCount - 1);
        loadItem();
        updateProgress();
      }
    );
  }

  // ---------- Wiring ----------

  helpToggle.addEventListener("click", () => {
    helpPopover.hidden = !helpPopover.hidden;
  });
  document.getElementById("reset-btn").addEventListener("click", () => {
    if (!itemSection.hidden) resetAssignments();
  });
  document.getElementById("back-btn").addEventListener("click", () => {
    if (!itemSection.hidden) goBack();
  });
  document.getElementById("save-btn").addEventListener("click", () => {
    if (!itemSection.hidden) commit();
  });

  document.addEventListener("keydown", (e) => {
    if (itemSection.hidden) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.metaKey || e.ctrlKey) return;

    if (e.key === "ArrowDown" || e.key === "j") {
      e.preventDefault();
      moveFocus(1);
    } else if (e.key === "ArrowUp" || e.key === "k") {
      e.preventDefault();
      moveFocus(-1);
    } else if (e.key === " ") {
      e.preventDefault();
      if (focus) toggleSelection(focus);
    } else if (e.key >= "1" && e.key <= "9") {
      e.preventDefault();
      assignToNthGroup(parseInt(e.key, 10));
    } else if (e.key === "g") {
      e.preventDefault();
      groupSelectedTogether();
    } else if (e.key === "u") {
      e.preventDefault();
      unassignFocused();
    } else if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Backspace") {
      e.preventDefault();
      goBack();
    }
  });

  loadQueue();
})();
