(() => {
  function api(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  const TIERS = ["song", "version", "recording", "release"]; // coarsest -> finest
  const TIER_ABBR = { song: "S", version: "V", recording: "R", release: "L" };
  const COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777"];

  const headerProgress = document.getElementById("progress-label");
  const progressFill = document.getElementById("progress-fill");
  const queueNameEl = document.getElementById("queue-name");
  const itemSection = document.getElementById("review-item");
  const itemTitle = document.getElementById("item-title");
  const draggedInNoteEl = document.getElementById("dragged-in-note");
  const rowsBody = document.getElementById("item-rows");
  const emptyEl = document.getElementById("review-empty");
  const doneEl = document.getElementById("review-done");
  const doneCountEl = document.getElementById("done-count");
  const errorEl = document.getElementById("review-error");
  const helpToggle = document.getElementById("help-toggle");
  const helpPopover = document.getElementById("help-popover");

  let items = [];
  let index = 0;
  let committedCount = 0;
  const committedKeys = new Set();

  let selection = new Set();
  let focus = null;
  let pinnedTrackId = null;
  let draggedInNote = [];
  let undoStack = [];
  let awaitingAck = false;
  let freshCounter = 0;

  function freshLabel(prefix) {
    freshCounter += 1;
    return `__fresh_${prefix}_${freshCounter}`;
  }

  function queryParams() {
    return new URLSearchParams(window.location.search);
  }

  function queueUrl() {
    const params = queryParams();
    if (params.has("tracks")) {
      return "/api/canonical/queue?tracks=" + encodeURIComponent(params.get("tracks"));
    }
    if (params.get("queue") === "cross-artist") {
      return "/api/canonical/queue?queue=cross-artist";
    }
    return "/api/canonical/queue";
  }

  function showError(msg) {
    errorEl.hidden = false;
    errorEl.textContent = msg;
  }

  // ---------- Loading ----------

  function loadQueue() {
    api(queueUrl()).then((data) => {
      if (data.error) {
        showError(data.detail || data.error);
        return;
      }
      items = data.items;
      queueNameEl.textContent =
        data.queue === "cross-artist" ? "Cross-artist queue" : data.queue === "ad-hoc" ? "Ad-hoc item" : "Main queue";
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
    const item = items[index];
    selection = new Set(item.track_ids);
    focus = item.track_ids[0];
    pinnedTrackId = null;
    draggedInNote = [];
    undoStack = [];
    awaitingAck = false;
    itemSection.hidden = false;
    emptyEl.hidden = true;
    doneEl.hidden = true;
    render();
  }

  // ---------- Partition logic ----------

  // Sets the exact relationship for the current selection in one shot:
  // level 1 = not even the same song (fully separate at all 4 tiers),
  // level 2 = same song, 3 = same version, 4 = same recording, 5 = same
  // release. Tiers at or coarser than the chosen level are shared across
  // the whole selection; every tier finer than that is split back out to
  // a fresh, individually-unique label per track. This only ever touches
  // the selected tracks -- it never reaches out to unselected neighbors,
  // and the server's own closure (on commit) handles any DB-side nesting
  // consequences beyond this item.
  function applyLevel(level) {
    if (!selection.size) return;
    const item = items[index];
    pushUndo();

    const sharedThroughIndex = level - 2; // -1 (level 1) .. 3 (level 5)
    const members = [...selection];

    TIERS.forEach((tier, i) => {
      if (i <= sharedThroughIndex) {
        const label = freshLabel(tier);
        for (const tid of members) item.labels[tid][tier] = label;
      } else {
        for (const tid of members) item.labels[tid][tier] = freshLabel(tier);
      }
    });

    render();
  }

  function isGrouped(item, tid) {
    return TIERS.some((tier) => {
      const label = item.labels[tid][tier];
      return item.track_ids.some((other) => other !== tid && item.labels[other][tier] === label);
    });
  }

  function pinRepresentative() {
    if (!focus) return;
    const item = items[index];
    if (!isGrouped(item, focus)) return; // nothing to represent -- still a singleton everywhere
    pushUndo();
    pinnedTrackId = focus;
    render();
  }

  function clearAll() {
    const item = items[index];
    pushUndo();
    for (const tid of item.track_ids) {
      for (const tier of TIERS) item.labels[tid][tier] = freshLabel(tier);
    }
    pinnedTrackId = null;
    render();
  }

  function pushUndo() {
    const item = items[index];
    undoStack.push({
      labels: JSON.parse(JSON.stringify(item.labels)),
      pinnedTrackId,
    });
  }

  function undo() {
    if (!undoStack.length) return;
    const snap = undoStack.pop();
    items[index].labels = snap.labels;
    pinnedTrackId = snap.pinnedTrackId;
    render();
  }

  // ---------- Selection / focus ----------

  function setFocus(tid) {
    focus = tid;
  }

  function toggleSelection(tid) {
    if (selection.has(tid)) selection.delete(tid);
    else selection.add(tid);
    render();
  }

  function selectRange(tid) {
    const item = items[index];
    const ids = item.track_ids;
    const a = ids.indexOf(focus);
    const b = ids.indexOf(tid);
    if (a === -1 || b === -1) {
      toggleSelection(tid);
      return;
    }
    const [lo, hi] = a < b ? [a, b] : [b, a];
    for (let i = lo; i <= hi; i++) selection.add(ids[i]);
    focus = tid;
    render();
  }

  function selectAll() {
    const item = items[index];
    for (const tid of item.track_ids) selection.add(tid);
    render();
  }

  function moveFocus(delta) {
    const item = items[index];
    const ids = item.track_ids;
    let i = ids.indexOf(focus);
    if (i === -1) i = 0;
    i = Math.max(0, Math.min(ids.length - 1, i + delta));
    focus = ids[i];
    render();
    const tr = rowsBody.querySelector(`tr[data-track-id="${CSS.escape(focus)}"]`);
    if (tr) tr.scrollIntoView({ block: "nearest" });
  }

  // ---------- Commit / navigation ----------

  function commit() {
    if (awaitingAck) {
      awaitingAck = false;
      advance();
      return;
    }
    const item = items[index];
    const payload = {
      track_ids: item.track_ids,
      labels: item.labels,
      pin_representative: pinnedTrackId,
    };
    api("/api/canonical/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((result) => {
      if (result.error) {
        showError(result.detail || result.error);
        return;
      }
      committedCount += 1;
      committedKeys.add(item.key);
      if (result.dragged_in && result.dragged_in.length) {
        draggedInNote = result.dragged_in;
        awaitingAck = true;
        render();
        updateProgress();
      } else {
        advance();
      }
    });
  }

  function advance() {
    if (index < items.length - 1) {
      index += 1;
      loadItem();
      updateProgress();
      return;
    }
    recomputeTail();
  }

  function recomputeTail() {
    const params = queryParams();
    if (params.has("tracks")) {
      finishQueue();
      return;
    }
    api(queueUrl()).then((data) => {
      const existingKeys = new Set(items.map((it) => it.key));
      const fresh = (data.items || []).filter(
        (it) => !existingKeys.has(it.key) && !committedKeys.has(it.key)
      );
      if (fresh.length) {
        items = items.concat(fresh);
        index += 1;
        loadItem();
        updateProgress();
      } else {
        finishQueue();
      }
    });
  }

  function finishQueue() {
    itemSection.hidden = true;
    doneEl.hidden = false;
    doneCountEl.textContent = String(committedCount);
  }

  function goBack() {
    if (index === 0) return;
    const prevItem = items[index - 1];
    const idsParam = prevItem.track_ids.join(",");
    api("/api/canonical/queue?tracks=" + encodeURIComponent(idsParam)).then((data) => {
      if (data.error || !data.items || !data.items.length) {
        showError((data && (data.detail || data.error)) || "could not reload previous item");
        return;
      }
      const fresh = data.items[0];
      fresh.key = prevItem.key;
      items[index - 1] = fresh;
      committedKeys.delete(prevItem.key);
      committedCount = Math.max(0, committedCount - 1);
      index -= 1;
      loadItem();
      updateProgress();
    });
  }

  function updateProgress() {
    const total = items.length;
    headerProgress.textContent = `${committedCount} / ${total} decided`;
    progressFill.style.width = total ? `${(committedCount / total) * 100}%` : "0%";
  }

  // ---------- Rendering ----------

  function displayNumbers(item, tier) {
    const seen = new Map();
    let n = 0;
    for (const tid of item.track_ids) {
      const label = item.labels[tid][tier];
      if (!seen.has(label)) {
        n += 1;
        seen.set(label, n);
      }
    }
    return seen;
  }

  // Every distinct group at this tier gets a color, in order of first
  // appearance, cycling through the palette -- this reads as "here are
  // the groups" rather than "here's what's selected/merged".
  function tierColorMap(item, tier) {
    const colorMap = new Map();
    let ci = 0;
    for (const tid of item.track_ids) {
      const label = item.labels[tid][tier];
      if (!colorMap.has(label)) {
        colorMap.set(label, COLORS[ci % COLORS.length]);
        ci += 1;
      }
    }
    return colorMap;
  }

  function isrcColorMap(item) {
    const counts = new Map();
    for (const tid of item.track_ids) {
      const isrc = item.tracks[tid].isrc;
      if (isrc) counts.set(isrc, (counts.get(isrc) || 0) + 1);
    }
    const colorMap = new Map();
    let ci = 0;
    for (const tid of item.track_ids) {
      const isrc = item.tracks[tid].isrc;
      if (isrc && counts.get(isrc) >= 2 && !colorMap.has(isrc)) {
        colorMap.set(isrc, COLORS[ci % COLORS.length]);
        ci += 1;
      }
    }
    return colorMap;
  }

  function formatDuration(ms) {
    if (ms == null) return "";
    const totalSec = Math.round(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function formatIsrc(isrc) {
    if (!isrc) return "—";
    if (isrc.length <= 8) return isrc;
    return `${isrc.slice(0, 2)}...${isrc.slice(-3)}`;
  }

  function suffixLabel(t) {
    if (t.suffix_class === "base") return "base";
    return `${t.suffix_class}: ${t.suffix}`;
  }

  function textCell(text, cls) {
    const td = document.createElement("td");
    td.textContent = text;
    if (cls) td.className = cls;
    return td;
  }

  function chipCell(item, tid, tier, displayNums, colorMap) {
    const td = document.createElement("td");
    td.className = "chip-cell";
    const label = item.labels[tid][tier];
    const chip = document.createElement("span");
    chip.className = "tier-chip";
    chip.textContent = `${TIER_ABBR[tier]}${displayNums.get(label)}`;
    chip.style.background = colorMap.get(label);

    td.appendChild(chip);
    return td;
  }

  function render() {
    const item = items[index];
    if (!item) return;

    const artists = [...new Set(item.track_ids.map((tid) => item.tracks[tid].artists))].join(" / ");
    itemTitle.textContent = `${item.base} — ${artists}`;

    if (draggedInNote.length) {
      draggedInNoteEl.hidden = false;
      draggedInNoteEl.textContent = "Also pulled in by closure: " + draggedInNote.join(", ");
    } else {
      draggedInNoteEl.hidden = true;
    }

    const displayNums = {};
    const colorMaps = {};
    for (const tier of TIERS) {
      displayNums[tier] = displayNumbers(item, tier);
      colorMaps[tier] = tierColorMap(item, tier);
    }
    const isrcColors = isrcColorMap(item);

    rowsBody.innerHTML = "";
    for (const tid of item.track_ids) {
      const t = item.tracks[tid];
      const tr = document.createElement("tr");
      tr.dataset.trackId = tid;
      if (selection.has(tid)) tr.classList.add("selected");
      if (tid === focus) tr.classList.add("focused");

      const selectTd = document.createElement("td");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selection.has(tid);
      checkbox.addEventListener("click", (e) => {
        e.stopPropagation();
        setFocus(tid);
        toggleSelection(tid);
      });
      selectTd.appendChild(checkbox);
      tr.appendChild(selectTd);

      const coverTd = document.createElement("td");
      if (t.album_image_url) {
        const img = document.createElement("img");
        img.className = "cover";
        img.src = t.album_image_url;
        coverTd.appendChild(img);
      }
      tr.appendChild(coverTd);

      tr.appendChild(textCell(t.title + (tid === pinnedTrackId ? " ★" : "")));
      tr.appendChild(textCell(t.artists));
      tr.appendChild(textCell(t.album || ""));
      tr.appendChild(textCell(formatDuration(t.duration_ms)));

      const isrcTd = textCell(formatIsrc(t.isrc));
      if (t.isrc) isrcTd.title = t.isrc;
      if (t.isrc && isrcColors.has(t.isrc)) {
        isrcTd.style.borderLeft = `4px solid ${isrcColors.get(t.isrc)}`;
      }
      tr.appendChild(isrcTd);

      tr.appendChild(textCell(String(t.live_count)));
      tr.appendChild(textCell(suffixLabel(t), "muted"));

      for (const tier of TIERS) {
        tr.appendChild(chipCell(item, tid, tier, displayNums[tier], colorMaps[tier]));
      }

      tr.addEventListener("click", (e) => {
        if (e.shiftKey) {
          selectRange(tid);
        } else {
          setFocus(tid);
          toggleSelection(tid);
        }
      });

      rowsBody.appendChild(tr);
    }
    updateProgress();
  }

  // ---------- Wiring ----------

  helpToggle.addEventListener("click", () => {
    helpPopover.hidden = !helpPopover.hidden;
  });

  document.querySelectorAll(".tier-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (itemSection.hidden) return;
      applyLevel(parseInt(btn.dataset.level, 10));
    });
  });

  document.getElementById("clear-btn").addEventListener("click", () => {
    if (!itemSection.hidden) clearAll();
  });
  document.getElementById("back-btn").addEventListener("click", () => {
    if (!itemSection.hidden) goBack();
  });
  document.getElementById("save-btn").addEventListener("click", () => {
    if (!itemSection.hidden) commit();
  });

  // Display keys 0-4 map to the internal 1-5 "relationship level" scale
  // (0 None = level 1 .. 4 Release = level 5); see applyLevel().
  const KEY_TO_LEVEL = { 0: 1, 1: 2, 2: 3, 3: 4, 4: 5 };

  document.addEventListener("keydown", (e) => {
    if (itemSection.hidden) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;

    if (e.key === "ArrowDown" || e.key === "j") {
      e.preventDefault();
      moveFocus(1);
    } else if (e.key === "ArrowUp" || e.key === "k") {
      e.preventDefault();
      moveFocus(-1);
    } else if (e.key === " ") {
      e.preventDefault();
      if (focus) toggleSelection(focus);
    } else if (e.key === "a") {
      e.preventDefault();
      selectAll();
    } else if (e.key in KEY_TO_LEVEL) {
      e.preventDefault();
      applyLevel(KEY_TO_LEVEL[e.key]);
    } else if (e.key === "r") {
      e.preventDefault();
      pinRepresentative();
    } else if (e.key === "Escape") {
      e.preventDefault();
      clearAll();
    } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      undo();
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
