(() => {
  const btn = document.getElementById("recompute-btn");
  if (!btn) return;

  const statusEl = document.getElementById("recompute-status");
  const errorEl = document.getElementById("recompute-error");

  btn.addEventListener("click", () => {
    btn.disabled = true;
    btn.textContent = "Recomputing…";
    errorEl.hidden = true;
    statusEl.textContent = "Running…";

    fetch("/api/scoring/recompute", { method: "POST" })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.detail || data.error || "recompute failed");

        Object.entries(data.tier_counts).forEach(([tier, count]) => {
          const cell = document.querySelector(`[data-tier-count="${tier}"]`);
          if (cell) cell.textContent = count.toLocaleString();
        });

        statusEl.textContent = "";
        statusEl.appendChild(document.createTextNode("Succeeded · "));
        statusEl.appendChild(makeDateSpan(data.status.finished_at));
        statusEl.appendChild(document.createTextNode(` · took ${data.status.duration_seconds}s`));
      })
      .catch((e) => {
        statusEl.textContent = "Failed";
        errorEl.hidden = false;
        errorEl.textContent = e.message;
      })
      .finally(() => {
        btn.disabled = false;
        btn.textContent = "Recompute scores";
      });
  });
})();
