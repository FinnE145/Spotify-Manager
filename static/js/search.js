// docs/specs/better-search-L.md §7/§6.2. Loaded site-wide from base.html's
// <head>, so -- same trap generation_confirm.js documents -- everything
// here has to wait for DOMContentLoaded: at parse time the <nav> (and any
// /search page content) below it in <body> doesn't exist yet, and a plain
// top-level `document.getElementById` would capture null forever.
document.addEventListener("DOMContentLoaded", () => {
  // -- The navbar dropdown (§7) -------------------------------------------
  // The navbar (and its search box) renders on every page including the
  // three immersive ones.

  const MIN_QUERY_LEN = 2;
  const DEBOUNCE_MS = 150;

  const form = document.getElementById("nav-search-form");
  const input = document.getElementById("nav-search-input");
  const dropdown = document.getElementById("nav-search-dropdown");

  if (form && input && dropdown) {
    let debounceTimer = null;
    // Every fetch gets the next sequence number; a reply only renders if it
    // is still the most recently issued one -- otherwise a slow reply for
    // an earlier keystroke can arrive after a fast one for a later, longer
    // query and overwrite the correct results with stale ones.
    let seq = 0;
    let rows = [];
    let highlighted = -1;

    const closeDropdown = () => {
      seq += 1; // invalidate any in-flight request
      dropdown.hidden = true;
      dropdown.innerHTML = "";
      rows = [];
      highlighted = -1;
    };

    const highlight = (index) => {
      rows.forEach((r) => r.classList.remove("highlighted"));
      highlighted = index;
      if (highlighted >= 0 && highlighted < rows.length) {
        rows[highlighted].classList.add("highlighted");
      }
    };

    const render = (html) => {
      dropdown.innerHTML = html;
      const allRows = Array.from(dropdown.querySelectorAll("tbody tr"));
      // The "No matches." row is a <tr> with no link -- present so the
      // dropdown still shows something, but not a navigable result.
      rows = allRows.filter((tr) => tr.querySelector("a"));
      highlighted = -1;
      dropdown.hidden = allRows.length === 0;
    };

    const fetchResults = (q) => {
      const mySeq = (seq += 1);
      fetch("/api/search?q=" + encodeURIComponent(q))
        .then((r) => r.json())
        .then((data) => {
          if (mySeq !== seq) return;
          render(data.html);
        })
        .catch(() => {
          if (mySeq !== seq) return;
          closeDropdown();
        });
    };

    const scheduleSearch = () => {
      clearTimeout(debounceTimer);
      const q = input.value.trim();
      if (q.length < MIN_QUERY_LEN) {
        closeDropdown();
        return;
      }
      debounceTimer = setTimeout(() => fetchResults(q), DEBOUNCE_MS);
    };

    input.addEventListener("input", scheduleSearch);

    input.addEventListener("keydown", (e) => {
      if (dropdown.hidden) return; // let a bare Enter submit the form normally
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (rows.length) highlight((highlighted + 1) % rows.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (rows.length) highlight((highlighted - 1 + rows.length) % rows.length);
      } else if (e.key === "Enter") {
        if (highlighted >= 0 && highlighted < rows.length) {
          e.preventDefault();
          const link = rows[highlighted].querySelector("a");
          if (link) window.location.href = link.href;
        }
        // else: nothing highlighted -- fall through to the form's own submit.
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });

    document.addEventListener("click", (e) => {
      if (!form.contains(e.target)) closeDropdown();
    });
  }

  // -- The /search page: See more per section ------------------------------
  // No-ops when the buttons are absent (an empty query, or a page other than
  // /search), same convention as generation_confirm.js.

  document.querySelectorAll(".search-see-more").forEach((btn) => {
    btn.addEventListener("click", () => {
      const type = btn.dataset.type;
      const q = new URLSearchParams(window.location.search).get("q") || "";
      const body = document.getElementById("search-" + type + "-body");
      const section = document.getElementById("search-" + type + "-section");
      if (!body) return;
      btn.disabled = true;
      btn.textContent = "Loading…";
      fetch("/api/search/more?q=" + encodeURIComponent(q) + "&type=" + encodeURIComponent(type))
        .then((r) => r.json())
        .then((data) => {
          body.innerHTML = data.html;
          if (section) section.classList.add("scrollable");
          btn.remove();
        })
        .catch(() => {
          btn.disabled = false;
          btn.textContent = "See more";
        });
    });
  });
});
