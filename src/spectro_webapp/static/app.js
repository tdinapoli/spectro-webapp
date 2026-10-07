// Spectrofluorometer web app frontend. No libraries, so it works offline.
//
// The forms and instrument panels are built from /api/config (MEASUREMENTS
// and the runners' declarations in Python). /api/status is polled every
// 500 ms and only updates values and enabled buttons, so typed values stay.

const $ = (id) => document.getElementById(id);
let config = {};
let lastStatus = null;
let currentInstrument = null;
let currentKind = null;
let requestError = ""; // error from the last instrument request, if any
const formValues = {}; // remembered field values: formValues[kind][param]
const actionValues = {}; // actionValues[instrument][action][param]

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

function defaults(params) {
  return Object.fromEntries(params.map((p) => [p.name, p.default]));
}

function option(value, text) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = text;
  return o;
}

// Number fields for `params`; edits are stored in `values`.
function buildFields(container, params, values) {
  for (const p of params) {
    const label = document.createElement("label");
    label.innerHTML = `<span>${p.label}</span>`;
    const span = document.createElement("span");
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.name = p.name;
    input.required = true;
    input.value = values[p.name];
    input.oninput = () => (values[p.name] = input.value);
    const unit = document.createElement("span");
    unit.className = "unit";
    unit.textContent = p.unit;
    span.append(input, unit);
    label.appendChild(span);
    container.appendChild(label);
  }
}

function fmtValue(v) {
  if (v === null || v === undefined) return "unknown";
  return typeof v === "number" ? +v.toPrecision(6) + "" : v;
}

// ---------- Tabs ----------

function selectTab(tab) {
  for (const b of document.querySelectorAll(".tabs button")) {
    b.classList.toggle("active", b.dataset.tab === tab);
  }
  for (const t of document.querySelectorAll(".tab")) {
    t.hidden = t.id !== `tab-${tab}`;
  }
}
for (const b of document.querySelectorAll(".tabs button")) {
  b.onclick = () => selectTab(b.dataset.tab);
}

// ---------- Instruments ----------

function selectInstrument(name) {
  currentInstrument = name;
  const spec = config.instruments[name];
  requestError = "";
  $("connect").textContent = spec.connect_label;

  const actions = $("actions");
  actions.innerHTML = "";
  let buttonRow = null; // consecutive actions without fields share a row
  for (const a of spec.actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = a.label;
    button.dataset.action = a.name;
    if (a.style) button.className = a.style;
    button.onclick = () => runAction(name, a);
    if (a.params.length) {
      buttonRow = null;
      const row = document.createElement("div");
      row.className = a.params.length > 1 ? "action multi" : "action";
      buildFields(row, a.params, actionValues[name][a.name]);
      row.appendChild(button);
      actions.appendChild(row);
    } else {
      if (!buttonRow) {
        buttonRow = document.createElement("div");
        buttonRow.className = "buttons";
        actions.appendChild(buttonRow);
      }
      buttonRow.appendChild(button);
    }
  }
  if (lastStatus) updateInstrumentPanel(lastStatus);
}

async function instrumentRequest(path, body) {
  requestError = "";
  try {
    await api("POST", path, body);
  } catch (err) {
    requestError = err.message;
  }
  poll();
}

function runAction(name, action) {
  instrumentRequest(`/api/instruments/${name}/${action.name}`, {
    params: actionValues[name][action.name],
  });
}

$("connect").onclick = () => instrumentRequest(`/api/instruments/${currentInstrument}/connect`);

$("instrument-select").onchange = (e) => selectInstrument(e.target.value);

function updateInstrumentPanel(st) {
  const name = currentInstrument;
  const spec = config.instruments[name];
  const inst = st.instruments[name];
  const ready = inst.status === "ready";
  const busy = st.task !== null;

  $("instrument-status").textContent = inst.status;
  $("instrument-error").textContent = requestError || inst.error || "";
  $("connect").disabled = busy;
  for (const b of $("actions").querySelectorAll("button")) b.disabled = busy || !ready;

  const tbody = $("readings");
  tbody.innerHTML = "";
  if (!ready) return;
  for (const r of spec.readings) {
    const tr = document.createElement("tr");
    const v = inst.values[r.name];
    const unit = v === null || v === undefined ? "" : r.unit;
    tr.innerHTML = `<td>${r.label}</td><td>${fmtValue(v)} ${unit}</td>`;
    tbody.appendChild(tr);
  }
}

// When an instrument becomes ready, fill its action fields that have a
// reading of the same name (laser current, frequency, ...) with the values
// the instrument actually has.
function prefillFromReadings(name, values) {
  for (const a of config.instruments[name].actions) {
    for (const p of a.params) {
      const v = values[p.name];
      if (typeof v === "number") actionValues[name][a.name][p.name] = v;
    }
  }
  if (name === currentInstrument) selectInstrument(name);
}

function updateBadges(st) {
  const badges = $("badges");
  badges.innerHTML = "";
  for (const [name, spec] of Object.entries(config.instruments)) {
    const inst = st.instruments[name];
    const span = document.createElement("span");
    span.className = `badge ${inst.status}`;
    span.textContent = `${spec.label}: ${inst.status}`;
    if (name === "laser" && inst.status === "ready") {
      const on = inst.values.output === "ON";
      span.textContent = on ? "LASER ON" : `${spec.label}: off`;
      if (on) span.className = "badge laser-on";
    }
    badges.appendChild(span);
  }
}

// ---------- Measurements ----------

function selectKind(kind) {
  currentKind = kind;
  const fields = $("fields");
  fields.innerHTML = "";
  buildFields(fields, config.measurements[kind].params, formValues[kind]);
}

$("measurement-select").onchange = (e) => selectKind(e.target.value);

$("form").onsubmit = async (e) => {
  e.preventDefault();
  $("message").textContent = "";
  try {
    await api("POST", "/api/start", {
      kind: currentKind,
      sample: $("sample").value,
      params: formValues[currentKind],
    });
    $("logy").checked = !!config.measurements[currentKind].log_y;
    poll();
  } catch (err) {
    $("message").textContent = err.message;
  }
};

$("stop").onclick = () => api("POST", "/api/stop");
$("logy").onchange = () => lastStatus && drawPlot(lastStatus.measurement);

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
  const prev = lastStatus;
  lastStatus = st;
  const m = st.measurement;
  const running = m.status === "running";
  const finished = prev && prev.measurement.status === "running" && !running;

  $("status").textContent = m.status;
  $("status").className = `status ${m.status}`;
  $("task").textContent = st.task && !running ? `Busy: ${st.task}` : "";
  $("start").disabled = st.task !== null;
  $("stop").disabled = !running;
  if (m.error) $("message").textContent = m.error;

  for (const [name, inst] of Object.entries(st.instruments)) {
    if (inst.status === "ready" && (!prev || prev.instruments[name].status !== "ready")) {
      prefillFromReadings(name, inst.values);
    }
  }
  updateBadges(st);
  updateInstrumentPanel(st);
  drawPlot(m);
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
  const spec = config.measurements[st.kind];
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

window.onresize = () => lastStatus && drawPlot(lastStatus.measurement);

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
  for (const [kind, spec] of Object.entries(config.measurements)) {
    formValues[kind] = defaults(spec.params);
    $("measurement-select").appendChild(option(kind, spec.label));
  }
  for (const [name, spec] of Object.entries(config.instruments)) {
    actionValues[name] = Object.fromEntries(spec.actions.map((a) => [a.name, defaults(a.params)]));
    $("instrument-select").appendChild(option(name, spec.label));
  }
  selectTab("instruments");
  selectInstrument(Object.keys(config.instruments)[0]);
  selectKind(Object.keys(config.measurements)[0]);
  loadResults();
  poll();
  setInterval(poll, 500);
})();
