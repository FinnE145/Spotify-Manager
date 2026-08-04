(() => {
  const errorEl = document.getElementById("artists-error");

  function post(path, body, onFail) {
    errorEl.hidden = true;
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        window.location.reload();
      })
      .catch((e) => {
        errorEl.hidden = false;
        errorEl.textContent = `Request failed: ${e.message}`;
        onFail();
      });
  }

  document.querySelectorAll(".artist-pair").forEach((row) => {
    const buttons = row.querySelectorAll("button");
    const status = row.querySelector("[data-artist-pair-status]");

    function decide(same, label) {
      // Disable both buttons for the round trip so a double-click can't
      // send two verdicts for the same pair.
      buttons.forEach((b) => (b.disabled = true));
      status.textContent = label;
      post(
        "/api/artists/alias",
        { artist_id_a: row.dataset.artistA, artist_id_b: row.dataset.artistB, same },
        () => {
          buttons.forEach((b) => (b.disabled = false));
          status.textContent = "";
        }
      );
    }

    row.querySelector("[data-artist-same]").addEventListener("click", () => decide(true, "Merging…"));
    row.querySelector("[data-artist-not-same]").addEventListener("click", () => decide(false, "Saving…"));
  });

  document.querySelectorAll("[data-artist-merged]").forEach((row) => {
    const btn = row.querySelector("[data-artist-unmerge]");
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Unmerging…";
      post("/api/artists/unmerge", { artist_id: row.dataset.artistMerged }, () => {
        btn.disabled = false;
        btn.textContent = "Unmerge";
      });
    });
  });
})();
