// In-browser reading-stats dashboard. When a visitor loads their StoryGraph CSV export,
// we run the real storywell.stats Python pipeline in Pyodide (WASM) and render the same
// dashboard the CLI produces — entirely client-side. The CSV is read with the File API and
// never uploaded anywhere; only the Python runtime and the storywell wheel come from the CDN.
(() => {
  const PYODIDE_VERSION = "0.29.4";
  const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

  const fileInput = document.getElementById("storywell-file");
  const statusEl = document.getElementById("storywell-status");
  const frame = document.getElementById("storywell-dash");
  if (!fileInput || !frame || !statusEl) return;

  let pyodidePromise = null;

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

  const RENDER_PY = `
import json
from storywell.stats import load_export, compute_all
from storywell.stats.render import render_dashboard
try:
    _data = compute_all(load_export("/export.csv"))
    _out = {"ok": True, "html": render_dashboard(_data, title="Your reading stats")}
except Exception as exc:
    _out = {"ok": False, "error": str(exc)}
json.dumps(_out)
`;

  async function renderCsv(text) {
    const pyodide = await ensurePyodide();
    setStatus("Building your dashboard…", "busy");
    pyodide.FS.writeFile("/export.csv", text, { encoding: "utf8" });
    const result = JSON.parse(pyodide.runPython(RENDER_PY));
    if (!result.ok) {
      setStatus(result.error || "That file didn't look like a StoryGraph export.", "error");
      return;
    }
    frame.removeAttribute("src");
    frame.srcdoc = result.html;
    setStatus("Showing your library — it never left your browser.", "ok");
  }

  fileInput.addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      await renderCsv(await file.text());
    } catch (err) {
      setStatus(`Something went wrong: ${err.message}`, "error");
    } finally {
      fileInput.value = "";
    }
  });
})();
