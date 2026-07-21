(() => {
  const CARD_W = 98; // 140 * 0.7
  const CARD_H = 122.5; // 175 * 0.7
  const GRID_DIVISOR = 7; // cards are ~7 grid units tall (falls in the 6-8 range)

  const viewportEl = document.getElementById("viewport");
  const worldEl = document.getElementById("world");
  const trayEl = document.getElementById("tray");
  const marqueeEl = document.getElementById("marquee");
  const zoomSlider = document.getElementById("zoom-slider");
  const scaleSlider = document.getElementById("scale-slider");
  const cutoffInput = document.getElementById("cutoff-input");
  const radiusCheckbox = document.getElementById("radius-checkbox");
  const pullBtn = document.getElementById("pull-btn");
  const exportBtn = document.getElementById("export-btn");
  const downloadBtn = document.getElementById("download-btn");
  const statusEl = document.getElementById("status");

  let state = { cards: [], labels: [] };
  let view = { panX: 0, panY: 0, zoom: 1 };
  let intrinsicScale = 1;
  let showRadius = false;
  let selection = new Set(); // "card:<id>" or "label:<id>"
  let spaceHeld = false;

  // ---------- data helpers ----------

  function api(path, options) {
    return fetch(path, options).then((r) => r.json());
  }

  function loadBoard() {
    return api("/api/board").then((data) => {
      state = data;
      renderAll();
    });
  }

  function gridUnit() {
    return (CARD_H * intrinsicScale) / GRID_DIVISOR;
  }

  function snap(v) {
    const unit = gridUnit();
    return Math.round(v / unit) * unit;
  }

  // ---------- coordinate transforms ----------

  function screenToWorld(sx, sy) {
    const rect = viewportEl.getBoundingClientRect();
    return {
      x: (sx - rect.left - view.panX) / view.zoom,
      y: (sy - rect.top - view.panY) / view.zoom,
    };
  }

  function applyViewTransform() {
    worldEl.style.transform = `translate(${view.panX}px, ${view.panY}px) scale(${view.zoom})`;
  }

  // ---------- rendering ----------

  function renderAll() {
    renderTray();
    renderWorld();
  }

  function cardKey(c) {
    return `card:${c.id}`;
  }
  function labelKey(l) {
    return `label:${l.id}`;
  }

  function renderTray() {
    trayEl.innerHTML = "";
    for (const card of state.cards.filter((c) => c.placement === "tray")) {
      const el = document.createElement("div");
      el.className = "card tray-card";
      el.dataset.key = cardKey(card);
      el.style.width = `${CARD_W}px`;
      el.style.height = `${CARD_H}px`;
      el.innerHTML = cardInner(card);
      el.addEventListener("mousedown", (e) => startTrayDrag(e, card));
      trayEl.appendChild(el);
    }
  }

  function cardInner(card) {
    const img = card.image_url ? `<img src="${card.image_url}" alt="">` : "";
    return `${img}<div class="name">${escapeHtml(card.display_name)}</div>`;
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function renderWorld() {
    worldEl.innerHTML = "";
    const w = CARD_W * intrinsicScale;
    const h = CARD_H * intrinsicScale;

    for (const card of state.cards.filter((c) => c.placement === "placed")) {
      const el = document.createElement("div");
      el.className = "card";
      if (selection.has(cardKey(card))) el.classList.add("selected");
      el.dataset.key = cardKey(card);
      el.style.left = `${card.x - w / 2}px`;
      el.style.top = `${card.y - h / 2}px`;
      el.style.width = `${w}px`;
      el.style.height = `${h}px`;
      el.innerHTML = cardInner(card);
      el.addEventListener("mousedown", (e) => startMove(e, cardKey(card)));
      worldEl.appendChild(el);

      if (showRadius) {
        const cutoff = Number(cutoffInput.value) || 0;
        const circle = document.createElement("div");
        circle.className = "radius-circle";
        circle.style.width = `${cutoff * 2}px`;
        circle.style.height = `${cutoff * 2}px`;
        circle.style.left = `${card.x - cutoff}px`;
        circle.style.top = `${card.y - cutoff}px`;
        worldEl.appendChild(circle);
      }
    }

    for (const label of state.labels) {
      const el = document.createElement("div");
      el.className = "label";
      if (selection.has(labelKey(label))) el.classList.add("selected");
      el.dataset.key = labelKey(label);
      el.style.left = `${label.x}px`;
      el.style.top = `${label.y}px`;
      el.style.transform = "translate(-50%, -50%)";
      el.style.fontSize = `${14 * intrinsicScale}px`;
      el.textContent = label.text;
      el.addEventListener("mousedown", (e) => startMove(e, labelKey(label)));
      worldEl.appendChild(el);
    }

    applyViewTransform();
  }

  // ---------- label editing ----------

  function editLabel(el, label) {
    el.contentEditable = "true";
    el.focus();
    document.execCommand("selectAll", false, null);
    const finish = () => {
      el.contentEditable = "false";
      const text = el.textContent;
      el.removeEventListener("blur", finish);
      label.text = text;
      api(`/api/label/${label.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
    };
    el.addEventListener("blur", finish);
  }

  function createLabelAt(x, y) {
    api("/api/label", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x: snap(x), y: snap(y), text: "New label" }),
    }).then((res) => {
      state.labels.push({ id: res.id, board_id: 1, text: "New label", x: snap(x), y: snap(y) });
      renderWorld();
      const el = worldEl.querySelector(`[data-key="label:${res.id}"]`);
      if (el) editLabel(el, state.labels[state.labels.length - 1]);
    });
  }

  function deleteSelectedLabels() {
    const toDelete = [...selection].filter((k) => k.startsWith("label:"));
    for (const key of toDelete) {
      const id = Number(key.split(":")[1]);
      api(`/api/label/${id}`, { method: "DELETE" }).then(() => {
        state.labels = state.labels.filter((l) => l.id !== id);
        selection.delete(key);
        renderWorld();
      });
    }
  }

  // ---------- drag: moving cards/labels on the canvas ----------

  let dragState = null;

  function startMove(e, key) {
    if (e.button !== 0 || spaceHeld) return;
    e.stopPropagation();

    const modifier = e.shiftKey || e.metaKey || e.ctrlKey;
    if (modifier) {
      if (selection.has(key)) selection.delete(key);
      else selection.add(key);
    } else if (!selection.has(key)) {
      selection = new Set([key]);
    }
    renderWorld();

    const startWorld = screenToWorld(e.clientX, e.clientY);
    const origins = new Map();
    for (const k of selection) {
      const item = findItem(k);
      if (item) origins.set(k, { x: item.x, y: item.y });
    }
    dragState = { startWorld, origins, moved: false, modifier, key };

    const onMove = (ev) => {
      const cur = screenToWorld(ev.clientX, ev.clientY);
      const dx = cur.x - dragState.startWorld.x;
      const dy = cur.y - dragState.startWorld.y;
      if (Math.abs(dx) > 1 || Math.abs(dy) > 1) dragState.moved = true;
      for (const [k, origin] of dragState.origins) {
        const item = findItem(k);
        item.x = origin.x + dx;
        item.y = origin.y + dy;
      }
      renderWorld();
    };

    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (dragState.moved) {
        for (const k of dragState.origins.keys()) {
          const item = findItem(k);
          item.x = snap(item.x);
          item.y = snap(item.y);
          persistPosition(k, item);
        }
        renderWorld();
      } else if (!dragState.modifier && dragState.key.startsWith("label:")) {
        const label = findItem(dragState.key);
        const el = worldEl.querySelector(`[data-key="${dragState.key}"]`);
        if (label && el) editLabel(el, label);
      }
      dragState = null;
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  function findItem(key) {
    const [kind, idStr] = key.split(":");
    const id = Number(idStr);
    if (kind === "card") return state.cards.find((c) => c.id === id);
    return state.labels.find((l) => l.id === id);
  }

  function persistPosition(key, item) {
    const [kind, idStr] = key.split(":");
    const id = Number(idStr);
    if (kind === "card") {
      api(`/api/card/${id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ placement: "placed", x: item.x, y: item.y }),
      });
    } else {
      api(`/api/label/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x: item.x, y: item.y }),
      });
    }
  }

  // ---------- drag: from tray onto the canvas ----------

  function startTrayDrag(e, card) {
    if (e.button !== 0) return;
    e.preventDefault();

    // Match the card's actual on-canvas size at the current zoom/intrinsic
    // scale, so the drag preview looks like where/how big it'll land.
    const ghostW = CARD_W * intrinsicScale * view.zoom;
    const ghostH = CARD_H * intrinsicScale * view.zoom;

    const ghost = document.createElement("div");
    ghost.className = "card";
    ghost.style.position = "fixed";
    ghost.style.width = `${ghostW}px`;
    ghost.style.height = `${ghostH}px`;
    ghost.style.zIndex = "1000";
    ghost.style.pointerEvents = "none";
    ghost.innerHTML = cardInner(card);
    document.body.appendChild(ghost);

    const moveGhost = (ev) => {
      ghost.style.left = `${ev.clientX - ghostW / 2}px`;
      ghost.style.top = `${ev.clientY - ghostH / 2}px`;
    };
    moveGhost(e);

    const onMove = (ev) => moveGhost(ev);
    const onUp = (ev) => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      ghost.remove();

      const rect = viewportEl.getBoundingClientRect();
      const inViewport =
        ev.clientX >= rect.left &&
        ev.clientX <= rect.right &&
        ev.clientY >= rect.top &&
        ev.clientY <= rect.bottom;
      if (!inViewport) return;

      const world = screenToWorld(ev.clientX, ev.clientY);
      card.placement = "placed";
      card.x = snap(world.x);
      card.y = snap(world.y);
      persistPosition(cardKey(card), card);
      renderAll();
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // ---------- pan / zoom ----------

  document.addEventListener("keydown", (e) => {
    if (e.code === "Space") {
      spaceHeld = true;
      viewportEl.classList.add("panning");
    } else if (e.key === "Delete" || e.key === "Backspace") {
      if (document.activeElement && document.activeElement.isContentEditable) return;
      deleteSelectedLabels();
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
      spaceHeld = false;
      viewportEl.classList.remove("panning");
    }
  });

  viewportEl.addEventListener("wheel", (e) => {
    e.preventDefault();
    if (e.ctrlKey) {
      // pinch-to-zoom gesture
      const rect = viewportEl.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const worldBefore = {
        x: (sx - view.panX) / view.zoom,
        y: (sy - view.panY) / view.zoom,
      };
      const factor = Math.exp(-e.deltaY * 0.01);
      setZoom(view.zoom * factor);
      view.panX = sx - worldBefore.x * view.zoom;
      view.panY = sy - worldBefore.y * view.zoom;
      applyViewTransform();
      zoomSlider.value = view.zoom;
    } else {
      view.panX -= e.deltaX;
      view.panY -= e.deltaY;
      applyViewTransform();
    }
  }, { passive: false });

  let middlePanning = false;
  let panStart = null;

  viewportEl.addEventListener("mousedown", (e) => {
    if (e.target !== viewportEl && e.target !== worldEl) return; // empty canvas only

    if (e.button === 1 || spaceHeld) {
      middlePanning = true;
      panStart = { x: e.clientX, y: e.clientY, panX: view.panX, panY: view.panY };
      const onMove = (ev) => {
        view.panX = panStart.panX + (ev.clientX - panStart.x);
        view.panY = panStart.panY + (ev.clientY - panStart.y);
        applyViewTransform();
      };
      const onUp = () => {
        middlePanning = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
      return;
    }

    startMarquee(e);
  });

  viewportEl.addEventListener("dblclick", (e) => {
    if (e.target !== viewportEl && e.target !== worldEl) return;
    const world = screenToWorld(e.clientX, e.clientY);
    createLabelAt(world.x, world.y);
  });

  function startMarquee(e) {
    const rect = viewportEl.getBoundingClientRect();
    const startX = e.clientX - rect.left;
    const startY = e.clientY - rect.top;
    let moved = false;

    marqueeEl.hidden = false;
    marqueeEl.style.left = `${startX}px`;
    marqueeEl.style.top = `${startY}px`;
    marqueeEl.style.width = "0px";
    marqueeEl.style.height = "0px";

    const onMove = (ev) => {
      moved = true;
      const curX = ev.clientX - rect.left;
      const curY = ev.clientY - rect.top;
      const left = Math.min(startX, curX);
      const top = Math.min(startY, curY);
      marqueeEl.style.left = `${left}px`;
      marqueeEl.style.top = `${top}px`;
      marqueeEl.style.width = `${Math.abs(curX - startX)}px`;
      marqueeEl.style.height = `${Math.abs(curY - startY)}px`;
    };

    const onUp = (ev) => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      marqueeEl.hidden = true;

      if (!moved) {
        if (!(ev.shiftKey || ev.metaKey || ev.ctrlKey)) selection = new Set();
        renderWorld();
        return;
      }

      const p1 = screenToWorld(rect.left + startX, rect.top + startY);
      const p2 = screenToWorld(ev.clientX, ev.clientY);
      const minX = Math.min(p1.x, p2.x);
      const maxX = Math.max(p1.x, p2.x);
      const minY = Math.min(p1.y, p2.y);
      const maxY = Math.max(p1.y, p2.y);

      if (!(ev.shiftKey || ev.metaKey || ev.ctrlKey)) selection = new Set();
      for (const card of state.cards.filter((c) => c.placement === "placed")) {
        if (card.x >= minX && card.x <= maxX && card.y >= minY && card.y <= maxY) {
          selection.add(cardKey(card));
        }
      }
      for (const label of state.labels) {
        if (label.x >= minX && label.x <= maxX && label.y >= minY && label.y <= maxY) {
          selection.add(labelKey(label));
        }
      }
      renderWorld();
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  function setZoom(z) {
    view.zoom = Math.min(2, Math.max(0.25, z));
  }

  zoomSlider.addEventListener("input", () => {
    setZoom(Number(zoomSlider.value));
    applyViewTransform();
  });

  scaleSlider.addEventListener("input", () => {
    intrinsicScale = Number(scaleSlider.value);
    renderAll();
  });

  radiusCheckbox.addEventListener("change", () => {
    showRadius = radiusCheckbox.checked;
    renderWorld();
  });

  cutoffInput.addEventListener("input", () => {
    if (showRadius) renderWorld();
  });

  // ---------- toolbar actions ----------

  pullBtn.addEventListener("click", () => {
    statusEl.textContent = "Pulling library…";
    api("/api/snapshot/pull", { method: "POST" }).then((data) => {
      state = data;
      renderAll();
      statusEl.textContent = `Pulled ${state.cards.length} playlists.`;
    });
  });

  exportBtn.addEventListener("click", () => {
    const cutoff = Number(cutoffInput.value) || 300;
    api(`/api/export?cutoff=${cutoff}`).then((data) => {
      navigator.clipboard.writeText(data.text).then(() => {
        statusEl.textContent = "Copied to clipboard.";
      });
    });
  });

  downloadBtn.addEventListener("click", () => {
    const cutoff = Number(cutoffInput.value) || 300;
    api(`/api/export?cutoff=${cutoff}`).then((data) => {
      const blob = new Blob([data.text], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "symr-export.md";
      a.click();
      URL.revokeObjectURL(url);
      statusEl.textContent = "Downloaded.";
    });
  });

  // ---------- init ----------

  loadBoard();
  applyViewTransform();
})();
