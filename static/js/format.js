// Site-wide relative-time formatting. Any element written as
// <span data-datetime="2026-07-20T14:32:00Z"></span> gets its text filled
// in on page load; hover shows the exact timestamp via the title attribute.
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function formatRelativeTime(isoString) {
  if (!isoString) return "";
  const date = new Date(isoString);
  if (isNaN(date)) return isoString;

  // Positive diffMs = the past (date already happened); negative = the
  // future (e.g. a "retry after" time). Same buckets, phrased either way.
  const diffMs = Date.now() - date.getTime();
  const future = diffMs < 0;
  const absSec = Math.round(Math.abs(diffMs) / 1000);

  const phrase = (n, unit) => {
    const s = `${n} ${unit}${n === 1 ? "" : "s"}`;
    return future ? `in ${s}` : `${s} ago`;
  };

  if (absSec < 60) return "just now";

  const absMin = Math.round(absSec / 60);
  if (absMin < 60) return phrase(absMin, "min");

  const absHour = Math.round(absMin / 60);
  if (absHour < 24) return phrase(absHour, "hour");

  const absDay = Math.round(absHour / 24);
  if (absDay < 7) return phrase(absDay, "day");
  if (absDay < 14) return `${future ? "this" : "last"} ${WEEKDAYS[date.getDay()]}`;

  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// A ready-made relative-time span, for text built in JS rather than rendered
// by the datetime_span macro. Lives here rather than in a page's IIFE because
// three pages build these into their progress labels.
function makeDateSpan(isoValue) {
  const span = document.createElement("span");
  span.dataset.datetime = isoValue;
  span.title = isoValue;
  span.textContent = formatRelativeTime(isoValue);
  return span;
}

// The exact clock time, in the viewer's own timezone. For the handful of
// values where "in 2 hours" is the wrong shape of answer -- a fixed schedule
// someone wants to check against a clock, rather than a past event whose
// distance is the interesting part.
function formatExactTime(isoString) {
  if (!isoString) return "";
  const date = new Date(isoString);
  if (isNaN(date)) return isoString;
  return date.toLocaleString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    day: "numeric",
    month: "short",
  });
}

// The exact-time counterpart to makeDateSpan, for text built in JS.
function makeExactDateSpan(isoValue) {
  const span = document.createElement("span");
  span.dataset.datetimeExact = isoValue;
  span.title = isoValue;
  span.textContent = formatExactTime(isoValue);
  return span;
}

function applyRelativeTimes(root = document) {
  root.querySelectorAll("[data-datetime]").forEach((el) => {
    const iso = el.dataset.datetime;
    el.textContent = formatRelativeTime(iso);
    if (iso) el.title = iso;
  });
  root.querySelectorAll("[data-datetime-exact]").forEach((el) => {
    const iso = el.dataset.datetimeExact;
    el.textContent = formatExactTime(iso);
    if (iso) el.title = iso;
  });
}

document.addEventListener("DOMContentLoaded", () => applyRelativeTimes());
