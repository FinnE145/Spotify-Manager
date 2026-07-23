(() => {
  // GRID is the primitive: the snap step, the dotted-grid spacing, and the unit
  // that card/label sizes are multiples of (all in world units at scale 1).
  const GRID = 17.5;
  // Even cell counts so that, with the midpoint snapped to a grid dot, every
  // edge (±W/2, ±H/2) also lands on a dot. Card is a square cover (6x6) + a
  // 2-cell name strip.
  const CARD_W = 6 * GRID; // 105
  const CARD_H = 8 * GRID; // 140
  const LABEL_FONT_SIZE = 17.5; // 14 * 1.25
  const LABEL_MIN_WIDTH = 7 * GRID; // 122.5 (label box snaps up from here, in grid units)
  const LABEL_PADDING_V = 5.2; // 4 * 1.3
  const LABEL_PADDING_H = 10.4; // 8 * 1.3

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
    return GRID * intrinsicScale;
  }

  function snap(v) {
    const unit = gridUnit();
    return Math.round(v / unit) * unit;
  }

  // Round a label's box up to whole grid cells so it tiles with the dotted grid
  // like the (fixed-size) cards do. Clearing width/height first re-measures the
  // natural, text-driven size before rounding.
  function sizeLabelToGrid(el) {
    // Round up to an even number of cells (like the cards) so a midpoint-snapped
    // label has all four edges on grid dots too.
    const step = gridUnit() * 2;
    el.style.width = "";
    el.style.height = "";
    el.style.width = `${Math.ceil(el.offsetWidth / step) * step}px`;
    el.style.height = `${Math.ceil(el.offsetHeight / step) * step}px`;
  }

  // Set a placed card's height + top. A card with no note keeps the fixed 8-cell
  // size; a card with a note (present as a .note child) grows to fit, rounded up
  // to an even number of cells so its edges still land on the grid. Height grows
  // symmetrically about the (grid-snapped) midpoint, so y stays on a dot.
  function fitCardHeight(el, card) {
    let h;
    if (el.querySelector(".note")) {
      const step = gridUnit() * 2;
      el.style.height = "";
      h = Math.ceil(el.offsetHeight / step) * step;
    } else {
      h = CARD_H * intrinsicScale;
    }
    el.style.height = `${h}px`;
    el.style.top = `${card.y - h / 2}px`;
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
    // Keep the dotted grid locked to world space: dots sit on the snap lattice
    // (world multiples of gridUnit) and pan/zoom with the cards.
    const spacing = gridUnit() * view.zoom;
    viewportEl.style.backgroundSize = `${spacing}px ${spacing}px`;
    viewportEl.style.backgroundPosition = `${view.panX}px ${view.panY}px`;
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
    const img = card.image_url ? `<img src="${escapeHtml(card.image_url)}" alt="">` : "";
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

    for (const card of state.cards.filter((c) => c.placement === "placed")) {
      const el = document.createElement("div");
      el.className = "card";
      if (selection.has(cardKey(card))) el.classList.add("selected");
      el.dataset.key = cardKey(card);
      el.style.left = `${card.x - w / 2}px`;
      el.style.width = `${w}px`;
      el.innerHTML = cardInner(card);
      if (card.note) {
        const noteEl = document.createElement("div");
        noteEl.className = "note";
        noteEl.textContent = card.note;
        el.appendChild(noteEl);
      }
      el.addEventListener("mousedown", (e) => startMove(e, cardKey(card)));
      worldEl.appendChild(el);
      fitCardHeight(el, card); // sets height + top (variable when the card has a note)

      if (showRadius) {
        const cutoff = Number(cutoffInput.value) || 0;
        const circle = document.createElement("div");
        circle.className = "radius-circle";
        circle.dataset.radiusFor = card.id;
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
      el.style.fontSize = `${LABEL_FONT_SIZE * intrinsicScale}px`;
      el.style.minWidth = `${LABEL_MIN_WIDTH * intrinsicScale}px`;
      el.style.padding = `${LABEL_PADDING_V * intrinsicScale}px ${LABEL_PADDING_H * intrinsicScale}px`;
      el.textContent = label.text;
      el.addEventListener("mousedown", (e) => startMove(e, labelKey(label)));
      worldEl.appendChild(el);
      sizeLabelToGrid(el);
    }

    applyViewTransform();
  }

  // Toggle the 'selected' outline on existing elements without rebuilding the
  // DOM. Rebuilding on mousedown would replace the card element between the two
  // clicks of a double-click, so the browser would never fire dblclick on it.
  function updateSelectionClasses() {
    for (const el of worldEl.querySelectorAll("[data-key]")) {
      el.classList.toggle("selected", selection.has(el.dataset.key));
    }
  }

  // Reposition a single placed card/label (and, for a card, its radius circle)
  // in-place. Used during a drag so we don't rebuild the whole world each frame.
  function positionMovedEl(key, item) {
    const el = worldEl.querySelector(`[data-key="${key}"]`);
    if (!el) return;
    if (key.startsWith("card:")) {
      const w = CARD_W * intrinsicScale;
      // The card's height varies with its note; use the height set at render.
      const h = parseFloat(el.style.height) || CARD_H * intrinsicScale;
      el.style.left = `${item.x - w / 2}px`;
      el.style.top = `${item.y - h / 2}px`;
      if (showRadius) {
        const cutoff = Number(cutoffInput.value) || 0;
        const circle = worldEl.querySelector(`.radius-circle[data-radius-for="${item.id}"]`);
        if (circle) {
          circle.style.left = `${item.x - cutoff}px`;
          circle.style.top = `${item.y - cutoff}px`;
        }
      }
    } else {
      el.style.left = `${item.x}px`;
      el.style.top = `${item.y}px`;
    }
  }

  // ---------- label editing ----------

  // Shared inline-text editing for labels and card notes.
  //   original  - text to restore if the edit is cancelled with Esc
  //   onInput   - optional per-keystroke callback (e.g. live-resize a card)
  //   commit    - fn(text) that saves the final/cancelled text to model + server
  //   rerender  - fn() to redraw after the edit closes
  // Enter commits and exits; Shift/Ctrl/⌘+Enter inserts a newline; Esc reverts.
  function beginEdit(el, { original, onInput, commit, rerender }) {
    el.contentEditable = "true";
    el.focus();
    document.execCommand("selectAll", false, null);
    let cancelled = false;

    const onKey = (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        cancelled = true;
        el.blur();
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
          document.execCommand("insertLineBreak");
          if (onInput) onInput();
        } else {
          el.blur();
        }
      }
    };
    const onInputEvt = () => {
      if (onInput) onInput();
    };
    const finish = () => {
      el.contentEditable = "false";
      el.removeEventListener("blur", finish);
      el.removeEventListener("keydown", onKey);
      el.removeEventListener("input", onInputEvt);
      // innerText (not textContent) preserves <br> line breaks as "\n";
      // browsers tack on a trailing newline, so drop one.
      const text = cancelled ? original : el.innerText.replace(/\n$/, "");
      commit(text);
      rerender();
    };

    el.addEventListener("keydown", onKey);
    el.addEventListener("input", onInputEvt);
    el.addEventListener("blur", finish);
  }

  function editLabel(el, label) {
    // Let the box grow freely with the text while editing; it re-snaps to the
    // grid on commit (rerender -> sizeLabelToGrid).
    el.style.width = "";
    el.style.height = "";
    beginEdit(el, {
      original: label.text,
      onInput: null,
      commit: (text) => {
        label.text = text;
        api(`/api/label/${label.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
      },
      rerender: renderWorld,
    });
  }

  function editCardNote(cardEl, card) {
    let noteEl = cardEl.querySelector(".note");
    if (!noteEl) {
      noteEl = document.createElement("div");
      noteEl.className = "note";
      cardEl.appendChild(noteEl);
    }
    fitCardHeight(cardEl, card); // make room for the (possibly empty) editable line
    beginEdit(noteEl, {
      original: card.note || "",
      onInput: () => fitCardHeight(cardEl, card), // grow the card live as you type
      commit: (text) => {
        card.note = text;
        api(`/api/card/${card.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note: text }),
        });
      },
      rerender: renderWorld,
    });
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

  function handleDeleteKey() {
    for (const key of [...selection]) {
      const [kind, idStr] = key.split(":");
      const id = Number(idStr);
      if (kind === "label") {
        state.labels = state.labels.filter((l) => l.id !== id);
        api(`/api/label/${id}`, { method: "DELETE" });
      } else {
        const card = state.cards.find((c) => c.id === id);
        if (card) {
          card.placement = "tray";
          card.x = null;
          card.y = null;
          persistPosition(key, card);
        }
      }
      selection.delete(key);
    }
    renderAll();
  }

  // ---------- drag: moving cards/labels on the canvas ----------

  let dragState = null;

  function startMove(e, key) {
    if (e.button !== 0 || spaceHeld) return;
    // Don't start a drag (which re-renders and would drop the edit) when the
    // click lands in text that's being edited — let the caret move instead.
    if (e.target.isContentEditable) return;
    e.stopPropagation();

    const modifier = e.shiftKey || e.metaKey || e.ctrlKey;
    if (modifier) {
      if (selection.has(key)) selection.delete(key);
      else selection.add(key);
    } else if (!selection.has(key)) {
      selection = new Set([key]);
    }
    updateSelectionClasses();

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
        positionMovedEl(k, item);
      }
    };

    const onUp = (upEvent) => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (dragState.moved) {
        const trayRect = trayEl.getBoundingClientRect();
        const droppedOnTray =
          upEvent.clientX >= trayRect.left &&
          upEvent.clientX <= trayRect.right &&
          upEvent.clientY >= trayRect.top &&
          upEvent.clientY <= trayRect.bottom;

        for (const k of dragState.origins.keys()) {
          const item = findItem(k);
          if (droppedOnTray && k.startsWith("card:")) {
            item.placement = "tray";
            item.x = null;
            item.y = null;
            selection.delete(k); // tray cards don't render a selection outline
          } else {
            item.x = snap(item.x);
            item.y = snap(item.y);
          }
          persistPosition(k, item);
        }
        renderAll();
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
        body: JSON.stringify({ placement: item.placement, x: item.x, y: item.y }),
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
      handleDeleteKey();
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
    const cardEl = e.target.closest(".card");
    if (cardEl && worldEl.contains(cardEl)) {
      const card = findItem(cardEl.dataset.key);
      if (card) editCardNote(cardEl, card);
      return;
    }
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
