(() => {
  function api(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  const TIERS = ["song", "version", "recording", "release"]; // coarsest -> finest
  const TIER_KEYS = { 1: "song", 2: "version", 3: "recording", 4: "release" };
  const TIER_ABBR = { song: "S", version: "V", recording: "R", release: "L" };
  const NESTING_ORDER = ["release", "recording", "version", "song"]; // finest -> coarsest
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

  // ---------- Nesting / partition logic ----------

  function enforceNesting(item) {
    for (let i = 0; i < NESTING_ORDER.length - 1; i++) {
      const finer = NESTING_ORDER[i];
      const coarser = NESTING_ORDER[i + 1];
      const groups = new Map();
      for (const tid of item.track_ids) {
        const key = item.labels[tid][finer];
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(tid);
      }
      for (const members of groups.values()) {
        const winner = item.labels[members[0]][coarser];
        for (const tid of members) item.labels[tid][coarser] = winner;
      }
    }
  }

  function expandSelection(item, tier) {
    const idx = TIERS.indexOf(tier);
    const expanded = new Set(selection);
    if (idx === TIERS.length - 1) return expanded; // release: finest, no closure
    const finer = TIERS[idx + 1];
    const finerLabels = new Set();
    for (const tid of selection) finerLabels.add(item.labels[tid][finer]);
    for (const tid of item.track_ids) {
      if (finerLabels.has(item.labels[tid][finer])) expanded.add(tid);
    }
    return expanded;
  }

  function applyTier(tier) {
    if (!selection.size) return;
    const item = items[index];
    pushUndo();

    const expanded = expandSelection(item, tier);
    const labelsAtTier = new Set([...expanded].map((tid) => item.labels[tid][tier]));

    if (labelsAtTier.size === 1) {
      const [label] = labelsAtTier;
      const fullMembers = item.track_ids.filter((tid) => item.labels[tid][tier] === label);
      const isFull = fullMembers.every((tid) => expanded.has(tid));
      if (isFull) {
        ungroupTier(item, tier, expanded);
      } else {
        assignFreshLabel(item, tier, expanded); // detach
      }
    } else {
      assignFreshLabel(item, tier, expanded); // merge
    }
    enforceNesting(item);
    selection = expanded;
    render();
  }

  function assignFreshLabel(item, tier, members) {
    const label = freshLabel(tier);
    for (const tid of members) item.labels[tid][tier] = label;
  }

  function ungroupTier(item, tier, members) {
    const idx = TIERS.indexOf(tier);
    if (idx === TIERS.length - 1) {
      for (const tid of members) item.labels[tid][tier] = freshLabel(tier);
      return;
    }
    const finer = TIERS[idx + 1];
    const byFiner = new Map();
    for (const tid of members) {
      const key = item.labels[tid][finer];
      if (!byFiner.has(key)) byFiner.set(key, freshLabel(tier));
      item.labels[tid][tier] = byFiner.get(key);
    }
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

  function tierColorMap(item, tier) {
    const counts = new Map();
    for (const tid of item.track_ids) {
      const label = item.labels[tid][tier];
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    const colorMap = new Map();
    let ci = 0;
    for (const tid of item.track_ids) {
      const label = item.labels[tid][tier];
      if (counts.get(label) >= 2 && !colorMap.has(label)) {
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
    const color = colorMap.get(label);
    if (color) {
      chip.style.background = color;
    } else {
      chip.classList.add("singleton");
    }

    const currentForTrack = item.current_ids && item.current_ids[tid] ? item.current_ids[tid][tier] : null;
    if (currentForTrack != null) {
      const membersWithLabel = item.track_ids.filter((id2) => item.labels[id2][tier] === label);
      const allSameId = membersWithLabel.every(
        (id2) => item.current_ids[id2] && item.current_ids[id2][tier] === currentForTrack
      );
      if (allSameId) {
        chip.title = `canonical_group.id ${currentForTrack}`;
        const idSpan = document.createElement("span");
        idSpan.className = "chip-id";
        idSpan.textContent = ` ${currentForTrack}`;
        chip.appendChild(idSpan);
      }
    }

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
    btn.addEventListener("click", () => applyTier(btn.dataset.tier));
  });

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
    } else if (["1", "2", "3", "4"].includes(e.key)) {
      e.preventDefault();
      applyTier(TIER_KEYS[e.key]);
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
