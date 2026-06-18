// In-browser reading-stats dashboard. When a visitor loads their StoryGraph CSV export,
// we run the real storywell.stats Python pipeline in Pyodide (WASM) and render the same
// dashboard the CLI produces — entirely client-side. The CSV is read with the File API and
// never uploaded anywhere; only the Python runtime and the storywell wheel come from the CDN.
// Once loaded, the parsed entries are cached in Pyodide so the year/format filters re-render
// without re-reading the file.
(() => {
  const PYODIDE_VERSION = "0.29.4";
  const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

  const fileInput = document.getElementById("storywell-file");
  const statusEl = document.getElementById("storywell-status");
  const frame = document.getElementById("storywell-dash");
  const filterBar = document.getElementById("storywell-filters");
  const yearSel = document.getElementById("storywell-year");
  const formatSel = document.getElementById("storywell-format");
  if (!fileInput || !frame || !statusEl) return;

  let pyodidePromise = null;
  let renderFn = null; // Python _render(year, fmt) proxy, set once a library is loaded

  function setStatus(message, kind) {
    statusEl.textContent = message;
    statusEl.dataset.kind = kind || "";
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = src;
      el.onload = resolve;
      el.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(el);
    });
  }

  // Load Pyodide and install storywell once, on first use. storywell is installed with
  // deps=False because its wheel metadata lists the sync-side vendors (audible, playwright)
  // that the stats path never imports and that can't run in the browser; jinja2 (the one
  // runtime dependency the dashboard renderer needs) is installed on its own.
  function ensurePyodide() {
    if (pyodidePromise) return pyodidePromise;
    pyodidePromise = (async () => {
      setStatus("Loading the Python runtime (one-time, ~10 MB)…", "busy");
      await loadScript(`${PYODIDE_BASE}pyodide.js`);
      const pyodide = await globalThis.loadPyodide({ indexURL: PYODIDE_BASE });
      setStatus("Setting up the stats engine…", "busy");
      const manifest = await (await fetch("assets/dashboard/manifest.json")).json();
      await pyodide.loadPackage("micropip");
      pyodide.globals.set("wheel_url", new URL(`assets/dashboard/${manifest.wheel}`, location.href).href);
      await pyodide.runPythonAsync(`
import micropip
await micropip.install("jinja2")
await micropip.install(wheel_url, deps=False)
`);
      return pyodide;
    })();
    return pyodidePromise;
  }

  // Parse the export, cache the entries, and define _render(year, fmt) for the filters.
  const SETUP_PY = `
import json
from storywell.stats import load_export, compute_all
from storywell.stats.render import render_dashboard

def _render(year, fmt):
    rows = _entries
    if year is not None:
        rows = [e for e in rows if any(i.finished_year == year for i in e.read_instances)]
    if fmt:
        rows = [e for e in rows if (e.media_format or "unknown") == fmt]
    return render_dashboard(compute_all(rows), title="Your reading stats")

try:
    _entries = load_export("/export.csv")
    _years = sorted(
        {i.finished_year for e in _entries for i in e.read_instances if i.finished_year is not None},
        reverse=True,
    )
    _formats = sorted({(e.media_format or "unknown") for e in _entries if e.is_read})
    _out = {"ok": True, "years": _years, "formats": _formats}
except Exception as exc:
    _out = {"ok": False, "error": str(exc)}
json.dumps(_out)
`;

  function option(value, label) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    return opt;
  }

  function buildFilters(meta) {
    if (!filterBar || !yearSel || !formatSel) return;
    yearSel.replaceChildren(option("", "All years"));
    meta.years.forEach((y) => yearSel.appendChild(option(String(y), String(y))));
    formatSel.replaceChildren(option("", "All formats"));
    meta.formats.forEach((f) => formatSel.appendChild(option(f, f)));
    filterBar.hidden = false;
  }

  function applyFilters() {
    if (!renderFn) return;
    const year = yearSel && yearSel.value ? Number(yearSel.value) : null;
    const fmt = (formatSel && formatSel.value) || null;
    frame.removeAttribute("src");
    frame.srcdoc = renderFn(year, fmt);
  }

  async function loadLibrary(text) {
    const pyodide = await ensurePyodide();
    setStatus("Building your dashboard…", "busy");
    pyodide.FS.writeFile("/export.csv", text, { encoding: "utf8" });
    const meta = JSON.parse(await pyodide.runPythonAsync(SETUP_PY));
    if (!meta.ok) {
      setStatus(meta.error || "That file didn't look like a StoryGraph export.", "error");
      return;
    }
    renderFn = pyodide.globals.get("_render");
    buildFilters(meta);
    applyFilters();
    setStatus("Showing your library — it never left your browser.", "ok");
  }

  if (yearSel) yearSel.addEventListener("change", applyFilters);
  if (formatSel) formatSel.addEventListener("change", applyFilters);

  fileInput.addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      await loadLibrary(await file.text());
    } catch (err) {
      setStatus(`Something went wrong: ${err.message}`, "error");
    } finally {
      fileInput.value = "";
    }
  });
})();
