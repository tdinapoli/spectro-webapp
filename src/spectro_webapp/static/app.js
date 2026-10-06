// Spectrofluorometer web app frontend. No libraries, so it works offline.

const $ = (id) => document.getElementById(id);
let config = {};
let currentKind = null;
let lastStatus = null;
const formValues = {}; // remembered values per measurement type

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ---------- Form ----------

function buildTabs() {
  const tabs = $("tabs");
  tabs.innerHTML = "";
  for (const [kind, spec] of Object.entries(config)) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = spec.label;
    b.onclick = () => selectKind(kind);
    b.dataset.kind = kind;
    tabs.appendChild(b);
  }
}

function saveFormValues() {
  if (!currentKind) return;
  for (const input of $("fields").querySelectorAll("input")) {
    formValues[currentKind][input.name] = input.value;
  }
}

function selectKind(kind) {
  saveFormValues();
  currentKind = kind;
  for (const b of $("tabs").children) b.classList.toggle("active", b.dataset.kind === kind);

  const fields = $("fields");
  fields.innerHTML = "";
  for (const p of config[kind].params) {
    const label = document.createElement("label");
    label.innerHTML = `<span>${p.label}</span>`;
    const span = document.createElement("span");
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.name = p.name;
    input.required = true;
    input.value = formValues[kind][p.name];
    const unit = document.createElement("span");
    unit.className = "unit";
    unit.textContent = p.unit;
    span.append(input, unit);
    label.appendChild(span);
    fields.appendChild(label);
  }
}

$("form").onsubmit = async (e) => {
  e.preventDefault();
  saveFormValues();
  $("message").textContent = "";
  try {
    await api("POST", "/api/start", {
      kind: currentKind,
      sample: $("sample").value,
      params: formValues[currentKind],
    });
    $("logy").checked = !!config[currentKind].log_y;
    poll();
  } catch (err) {
    $("message").textContent = err.message;
  }
};

$("stop").onclick = () => api("POST", "/api/stop");

$("initialize").onclick = async () => {
  $("instrument-error").textContent = "";
  try {
    await api("POST", "/api/initialize");
    poll();
  } catch (err) {
    $("instrument-error").textContent = err.message;
  }
};
$("logy").onchange = () => lastStatus && drawPlot(lastStatus);

// ---------- Status polling ----------

async function poll() {
  let st;
  try {
    st = await api("GET", "/api/status");
  } catch {
    $("status").textContent = "disconnected";
    $("status").className = "status error";
    return;
  }
  const finished = lastStatus && lastStatus.status === "running" && st.status !== "running";
  lastStatus = st;

  $("status").textContent = st.status;
  $("status").className = `status ${st.status}`;
  const running = st.status === "running";
  const ready = st.instrument === "ready";
  $("start").disabled = running || !ready;
  $("stop").disabled = !running;
  if (st.error) $("message").textContent = st.error;

  $("instrument").textContent = st.instrument;
  $("instrument-error").textContent = st.instrument_error || "";
  $("initialize").textContent = ready ? "Re-initialize" : "Initialize";
  $("initialize").disabled = running || st.instrument === "initializing";

  drawPlot(st);
  if (finished) loadResults();
}

// ---------- Plot ----------

function niceTicks(min, max, count = 6) {
  const span = max - min || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 1e-9; t += step) ticks.push(t);
  return ticks;
}

function fmt(v) {
  if (v === 0) return "0";
  const a = Math.abs(v);
  return a >= 1e5 || a < 1e-2 ? v.toExponential(0) : +v.toPrecision(4) + "";
}

function drawPlot(st) {
  const canvas = $("plot");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const rows = st.rows || [];
  const spec = config[st.kind];
  $("plot-title").textContent = spec ? `${spec.label} — ${rows.length} points` : "No data";
  if (!spec || rows.length === 0) return;

  const logy = $("logy").checked;
  const ys = rows.map((r) => (logy ? Math.log10(Math.max(r[1], 0.5)) : r[1]));
  const xs = rows.map((r) => r[0]);
  let [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  if (!logy) y0 = Math.min(0, y0);
  if (y0 === y1) y1 = y0 + 1;

  const m = { l: 64, r: 16, t: 12, b: 44 };
  const px = (x) => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r);
  const py = (y) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);

  // Axes and grid
  ctx.font = "12px system-ui, sans-serif";
  ctx.strokeStyle = "#e5e7eb";
  ctx.fillStyle = "#6b7280";
  ctx.lineWidth = 1;
  ctx.textAlign = "center";
  for (const t of niceTicks(x0, x1)) {
    ctx.beginPath(); ctx.moveTo(px(t), m.t); ctx.lineTo(px(t), H - m.b); ctx.stroke();
    ctx.fillText(fmt(t), px(t), H - m.b + 16);
  }
  ctx.textAlign = "right";
  const yTicks = logy
    ? niceTicks(y0, y1).filter((t) => Number.isInteger(t))
    : niceTicks(y0, y1);
  for (const t of yTicks) {
    ctx.beginPath(); ctx.moveTo(m.l, py(t)); ctx.lineTo(W - m.r, py(t)); ctx.stroke();
    ctx.fillText(logy ? fmt(10 ** t) : fmt(t), m.l - 6, py(t) + 4);
  }
  ctx.textAlign = "center";
  ctx.fillStyle = "#1d2330";
  ctx.fillText(spec.columns[0], (W + m.l) / 2, H - 8);
  ctx.save();
  ctx.translate(14, (H - m.b) / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(spec.columns[1], 0, 0);
  ctx.restore();

  // Data
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  rows.forEach((r, i) => {
    const X = px(xs[i]), Y = py(ys[i]);
    i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
  });
  ctx.stroke();
}

window.onresize = () => lastStatus && drawPlot(lastStatus);

// ---------- Saved results ----------

async function loadResults() {
  const files = await api("GET", "/api/results");
  const tbody = $("results");
  tbody.innerHTML = files.length ? "" : "<tr><td>No measurements saved yet.</td></tr>";
  for (const f of files) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${f.name}</td><td>${(f.size / 1024).toFixed(1)} kB</td>
      <td class="actions"><a href="/api/results/${encodeURIComponent(f.name)}">Download</a>
      <button type="button">Delete</button></td>`;
    tr.querySelector("button").onclick = async () => {
      if (!confirm(`Delete ${f.name}?`)) return;
      await api("DELETE", `/api/results/${encodeURIComponent(f.name)}`);
      loadResults();
    };
    tbody.appendChild(tr);
  }
}

// ---------- Start ----------

(async () => {
  config = await api("GET", "/api/config");
  for (const [kind, spec] of Object.entries(config)) {
    formValues[kind] = Object.fromEntries(spec.params.map((p) => [p.name, p.default]));
  }
  buildTabs();
  selectKind(Object.keys(config)[0]);
  loadResults();
  poll();
  setInterval(poll, 500);
})();
