"""HTML del dashboard (página única, sin build). Chart.js se carga por CDN."""
from __future__ import annotations

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Panel de Métricas | Clínica</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #f4f6fb;
      --card: #ffffff;
      --ink: #1f2a44;
      --muted: #6b7793;
      --line: #e6eaf2;
      --brand: #2f6fed;
      --brand-soft: #eaf1ff;
      --green: #18a974;
      --amber: #e0a200;
      --red: #e25555;
      --violet: #7a5cf0;
      --shadow: 0 6px 24px rgba(31, 42, 68, 0.08);
      --radius: 16px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    .hidden { display: none !important; }

    /* Login */
    .login-wrap {
      min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px;
    }
    .login-card {
      background: var(--card); width: 100%; max-width: 380px; border-radius: var(--radius);
      box-shadow: var(--shadow); padding: 36px 32px;
    }
    .login-card h1 { font-size: 22px; margin: 0 0 4px; }
    .login-card p { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
    .field { margin-bottom: 16px; }
    .field label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
    .field input {
      width: 100%; padding: 11px 13px; border: 1px solid var(--line); border-radius: 10px;
      font-size: 15px; outline: none; transition: border .15s;
    }
    .field input:focus { border-color: var(--brand); }
    .btn {
      background: var(--brand); color: #fff; border: 0; border-radius: 10px; padding: 12px 16px;
      font-size: 15px; font-weight: 600; cursor: pointer; width: 100%; transition: filter .15s;
    }
    .btn:hover { filter: brightness(1.05); }
    .btn.secondary { background: transparent; color: var(--muted); border: 1px solid var(--line); width: auto; }
    .error-msg { color: var(--red); font-size: 13px; margin-top: 10px; min-height: 18px; }

    /* App */
    .topbar {
      background: var(--card); border-bottom: 1px solid var(--line); padding: 14px 24px;
      display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
      position: sticky; top: 0; z-index: 10;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand .dot { width: 34px; height: 34px; border-radius: 9px; background: var(--brand-soft);
      display: grid; place-items: center; color: var(--brand); font-weight: 700; }
    .brand h2 { font-size: 17px; margin: 0; }
    .brand span { font-size: 12px; color: var(--muted); }
    .controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    select {
      padding: 9px 12px; border: 1px solid var(--line); border-radius: 10px; background: #fff;
      font-size: 14px; color: var(--ink); cursor: pointer;
    }
    .seg { display: inline-flex; background: var(--bg); border-radius: 10px; padding: 3px; }
    .seg button {
      border: 0; background: transparent; padding: 7px 14px; border-radius: 8px; cursor: pointer;
      font-size: 13px; color: var(--muted); font-weight: 600;
    }
    .seg button.active { background: #fff; color: var(--brand); box-shadow: var(--shadow); }

    .container { padding: 24px; max-width: 1240px; margin: 0 auto; }
    .kpi-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 16px; margin-bottom: 22px;
    }
    .kpi {
      background: var(--card); border-radius: var(--radius); padding: 18px 18px; box-shadow: var(--shadow);
      border: 1px solid var(--line);
    }
    .kpi .label { font-size: 13px; color: var(--muted); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
    .kpi .value { font-size: 28px; font-weight: 700; letter-spacing: -0.5px; }
    .pill { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
    .p-brand { background: var(--brand); } .p-green { background: var(--green); }
    .p-amber { background: var(--amber); } .p-red { background: var(--red); }
    .p-violet { background: var(--violet); } .p-muted { background: var(--muted); }

    .panel-grid { display: grid; grid-template-columns: 1.6fr 1fr; gap: 16px; margin-bottom: 16px; }
    @media (max-width: 900px) { .panel-grid { grid-template-columns: 1fr; } }
    .panel {
      background: var(--card); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow);
      border: 1px solid var(--line);
    }
    .panel h3 { font-size: 15px; margin: 0 0 4px; }
    .panel .sub { font-size: 12px; color: var(--muted); margin: 0 0 14px; }
    .chart-box { position: relative; height: 300px; }
    .chart-box.sm { height: 260px; }

    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); }
    th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; cursor: pointer; }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    tbody tr:hover { background: var(--bg); }
    .table-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
    .empty { color: var(--muted); font-size: 14px; padding: 24px 0; text-align: center; }
    .table-scroll { max-height: 360px; overflow: auto; }
    .footnote { color: var(--muted); font-size: 12px; margin-top: 18px; line-height: 1.5; }
    .loading { color: var(--muted); font-size: 13px; }
    .range-hint { font-size: 13px; color: var(--muted); margin: 0 0 18px; }
    .panel-full { margin-bottom: 16px; }
    select:disabled { opacity: 0.55; cursor: not-allowed; }
  </style>
</head>
<body>
  <!-- LOGIN -->
  <div id="loginView" class="login-wrap">
    <form id="loginForm" class="login-card">
      <h1>Panel de Métricas</h1>
      <p>Acceso para la clínica y su equipo.</p>
      <div class="field">
        <label for="username">Usuario</label>
        <input id="username" name="username" autocomplete="username" required />
      </div>
      <div class="field">
        <label for="password">Contraseña</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required />
      </div>
      <button class="btn" type="submit">Entrar</button>
      <div id="loginError" class="error-msg"></div>
    </form>
  </div>

  <!-- APP -->
  <div id="appView" class="hidden">
    <div class="topbar">
      <div class="brand">
        <div class="dot">+</div>
        <div>
          <h2 id="clinicName">Clínica</h2>
          <span>Panel de métricas</span>
        </div>
      </div>
      <div class="controls">
        <select id="yearSelect" title="Año"></select>
        <select id="monthSelect" title="Mes"><option value="0">Todos los meses</option></select>
        <select id="daySelect" title="Día" disabled><option value="0">Todos los días</option></select>
        <div class="seg" id="granSeg">
          <button data-g="day" class="active">Día</button>
          <button data-g="week">Semana</button>
          <button data-g="month">Mes</button>
        </div>
        <button class="btn secondary" id="logoutBtn">Salir</button>
      </div>
    </div>

    <div class="container">
      <p class="range-hint" id="rangeHint">Mostrando: —</p>
      <div class="kpi-grid" id="kpiGrid"></div>

      <div class="panel-grid">
        <div class="panel">
          <h3>Personas escribiendo vs. Citas agendadas</h3>
          <p class="sub">Pacientes distintos que contactan la clínica y cuántas citas se concretan.</p>
          <div class="chart-box"><canvas id="tsChart"></canvas></div>
        </div>
        <div class="panel">
          <h3>Estado de las citas</h3>
          <p class="sub">Agendadas, canceladas y reagendadas.</p>
          <div class="chart-box sm"><canvas id="statusChart"></canvas></div>
        </div>
      </div>

      <div class="panel panel-full">
        <h3>Volumen de mensajes</h3>
        <p class="sub">Cuántas veces escribieron los pacientes (la misma persona puede enviar varios mensajes).</p>
        <div class="chart-box sm"><canvas id="msgChart"></canvas></div>
        <div id="msgEmpty" class="empty hidden">Sin mensajes en el período seleccionado.</div>
      </div>

      <div class="panel-grid">
        <div class="panel">
          <div class="table-head">
            <div><h3>Detalle por período</h3><p class="sub">Mensajes, personas únicas, citas y ratio.</p></div>
            <button class="btn secondary" id="csvBtn">Exportar CSV</button>
          </div>
          <div class="table-scroll">
            <table id="detailTable">
              <thead>
                <tr>
                  <th>Período</th>
                  <th class="num">Mensajes</th>
                  <th class="num">Personas</th>
                  <th class="num">Citas</th>
                  <th class="num">Msg/Cita</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
          <div id="detailEmpty" class="empty hidden">Sin datos en el período seleccionado.</div>
        </div>
        <div class="panel">
          <h3>Citas por servicio</h3>
          <p class="sub">Motivos de cita más frecuentes.</p>
          <div class="chart-box sm"><canvas id="serviceChart"></canvas></div>
          <div id="serviceEmpty" class="empty hidden">Sin citas en el período.</div>
        </div>
      </div>

      <p class="footnote">
        Las fechas usan la creación del evento (zona horaria de El Salvador). Personas = teléfonos
        distintos que escribieron. El ratio Msg/Cita indica cuántos mensajes se reciben por cada cita.
        Las reagendaciones hechas directamente en Google Calendar no se cuentan aquí.
      </p>
    </div>
  </div>

  <script>
    const MONTH_NAMES = [
      "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
      "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ];
    const state = { start: null, end: null, granularity: "day", detail: [] };

    function pad2(n) { return String(n).padStart(2, "0"); }
    function fmt(n) { return (n === null || n === undefined) ? "-" : n.toLocaleString("es-SV"); }
    function fmtRatio(n) { return (n === null || n === undefined) ? "-" : n.toLocaleString("es-SV"); }
    function daysInMonth(year, month1to12) { return new Date(year, month1to12, 0).getDate(); }

    function populateMonthSelect() {
      const sel = document.getElementById("monthSelect");
      const prev = sel.value || "0";
      sel.innerHTML = '<option value="0">Todos los meses</option>';
      MONTH_NAMES.forEach((name, i) => {
        const o = document.createElement("option");
        o.value = String(i + 1);
        o.textContent = name;
        sel.appendChild(o);
      });
      sel.value = ["0"].concat(MONTH_NAMES.map((_, i) => String(i + 1))).includes(prev) ? prev : "0";
    }

    function populateDaySelect() {
      const monthSel = document.getElementById("monthSelect");
      const daySel = document.getElementById("daySelect");
      const y = parseInt(document.getElementById("yearSelect").value, 10);
      const m = parseInt(monthSel.value, 10);
      const prevDay = daySel.value || "0";
      daySel.innerHTML = '<option value="0">Todos los días</option>';
      if (!m) {
        daySel.disabled = true;
        daySel.value = "0";
        return;
      }
      daySel.disabled = false;
      const max = daysInMonth(y, m);
      for (let d = 1; d <= max; d++) {
        const o = document.createElement("option");
        o.value = String(d);
        o.textContent = String(d);
        daySel.appendChild(o);
      }
      daySel.value = (parseInt(prevDay, 10) >= 1 && parseInt(prevDay, 10) <= max) ? prevDay : "0";
    }

    function syncDateRange() {
      const y = parseInt(document.getElementById("yearSelect").value, 10);
      const m = parseInt(document.getElementById("monthSelect").value, 10);
      const d = parseInt(document.getElementById("daySelect").value, 10);
      if (!m) {
        state.start = y + "-01-01";
        state.end = y + "-12-31";
      } else if (!d) {
        const last = daysInMonth(y, m);
        state.start = y + "-" + pad2(m) + "-01";
        state.end = y + "-" + pad2(m) + "-" + pad2(last);
      } else {
        const iso = y + "-" + pad2(m) + "-" + pad2(d);
        state.start = iso;
        state.end = iso;
      }
      updateRangeHint();
    }

    function updateRangeHint() {
      const y = parseInt(document.getElementById("yearSelect").value, 10);
      const m = parseInt(document.getElementById("monthSelect").value, 10);
      const d = parseInt(document.getElementById("daySelect").value, 10);
      let text = "Mostrando: ";
      if (!m) {
        text += "todo " + y;
      } else if (!d) {
        text += MONTH_NAMES[m - 1] + " " + y;
      } else {
        text += d + " de " + MONTH_NAMES[m - 1] + " " + y;
      }
      document.getElementById("rangeHint").textContent = text;
    }

    async function api(path, opts) {
      const res = await fetch(path, Object.assign({ credentials: "same-origin" }, opts || {}));
      return res;
    }

    function showLogin(msg) {
      document.getElementById("appView").classList.add("hidden");
      document.getElementById("loginView").classList.remove("hidden");
      if (msg) document.getElementById("loginError").textContent = msg;
    }
    function showApp() {
      document.getElementById("loginView").classList.add("hidden");
      document.getElementById("appView").classList.remove("hidden");
    }

    document.getElementById("loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      document.getElementById("loginError").textContent = "";
      const username = document.getElementById("username").value;
      const password = document.getElementById("password").value;
      const res = await api("/dashboard/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) { await boot(); }
      else if (res.status === 401) { document.getElementById("loginError").textContent = "Usuario o contraseña incorrectos."; }
      else { document.getElementById("loginError").textContent = "No se pudo iniciar sesión. Intenta más tarde."; }
    });

    document.getElementById("logoutBtn").addEventListener("click", async () => {
      await api("/dashboard/logout", { method: "POST" });
      showLogin("");
    });

    document.querySelectorAll("#granSeg button").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#granSeg button").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        state.granularity = b.dataset.g;
        loadTimeseries();
      });
    });

    document.getElementById("yearSelect").addEventListener("change", () => {
      document.getElementById("monthSelect").value = "0";
      populateDaySelect();
      syncDateRange();
      loadAll();
    });

    document.getElementById("monthSelect").addEventListener("change", () => {
      populateDaySelect();
      syncDateRange();
      loadAll();
    });

    document.getElementById("daySelect").addEventListener("change", () => {
      syncDateRange();
      loadAll();
    });

    document.getElementById("csvBtn").addEventListener("click", exportCsv);

    let tsChart, statusChart, serviceChart, msgChart;

    function kpiCard(label, value, pill) {
      return '<div class="kpi"><div class="label"><span class="pill ' + pill + '"></span>' + label +
        '</div><div class="value">' + value + '</div></div>';
    }

    function personasPorCita(personas, citas) {
      if (!citas || citas <= 0) return null;
      return Math.round((personas / citas) * 100) / 100;
    }

    async function loadSummary() {
      const res = await api("/dashboard/api/summary?start=" + state.start + "&end=" + state.end);
      if (res.status === 401) { showLogin(""); return; }
      const json = await res.json();
      const d = (json && json.data) || {};
      const ppc = personasPorCita(d.personas_unicas || 0, d.agendadas || 0);
      document.getElementById("kpiGrid").innerHTML =
        kpiCard("Citas agendadas", fmt(d.agendadas), "p-brand") +
        kpiCard("Canceladas", fmt(d.canceladas), "p-red") +
        kpiCard("Reagendadas", fmt(d.reagendadas), "p-amber") +
        kpiCard("Derivaciones", fmt(d.derivaciones), "p-violet") +
        kpiCard("Personas únicas", fmt(d.personas_unicas), "p-muted") +
        kpiCard("Mensajes", fmt(d.mensajes_usuario), "p-green") +
        kpiCard("Personas por cita", fmtRatio(ppc), "p-brand") +
        kpiCard("Msg por cita", fmtRatio(d.ratio_mensajes_por_cita), "p-green");
      renderStatusChart(d);
    }

    function renderStatusChart(d) {
      const ctx = document.getElementById("statusChart");
      const data = [d.agendadas || 0, d.canceladas || 0, d.reagendadas || 0];
      if (statusChart) statusChart.destroy();
      statusChart = new Chart(ctx, {
        type: "doughnut",
        data: {
          labels: ["Agendadas", "Canceladas", "Reagendadas"],
          datasets: [{ data, backgroundColor: ["#2f6fed", "#e25555", "#e0a200"], borderWidth: 0 }],
        },
        options: { plugins: { legend: { position: "bottom" } }, cutout: "62%", maintainAspectRatio: false },
      });
    }

    function renderMessagesChart(labels, mensajes) {
      const ctx = document.getElementById("msgChart");
      const empty = document.getElementById("msgEmpty");
      if (msgChart) msgChart.destroy();
      const total = mensajes.reduce((a, b) => a + b, 0);
      if (!total) {
        empty.classList.remove("hidden");
        return;
      }
      empty.classList.add("hidden");
      msgChart = new Chart(ctx, {
        type: "bar",
        data: {
          labels,
          datasets: [{
            label: "Mensajes",
            data: mensajes,
            backgroundColor: "rgba(24,169,116,0.75)",
            borderRadius: 6,
          }],
        },
        options: {
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                afterLabel: (ctx) => {
                  const p = state.detail[ctx.dataIndex];
                  if (p && p.personas > 0) {
                    const mpp = Math.round((p.mensajes / p.personas) * 10) / 10;
                    return "Prom. " + mpp + " msg/persona";
                  }
                  return "";
                },
              },
            },
          },
          scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
        },
      });
    }

    async function loadTimeseries() {
      const res = await api("/dashboard/api/timeseries?start=" + state.start + "&end=" + state.end + "&granularity=" + state.granularity);
      if (res.status === 401) { showLogin(""); return; }
      const json = await res.json();
      const rows = (json && json.data) || [];
      state.detail = rows;
      const labels = rows.map((r) => r.period);
      const personas = rows.map((r) => r.personas);
      const citas = rows.map((r) => r.citas);
      const mensajes = rows.map((r) => r.mensajes);
      const ctx = document.getElementById("tsChart");
      if (tsChart) tsChart.destroy();
      tsChart = new Chart(ctx, {
        type: "line",
        data: {
          labels,
          datasets: [
            { label: "Personas escribiendo", data: personas, borderColor: "#18a974", backgroundColor: "rgba(24,169,116,.12)", fill: true, tension: .3, borderWidth: 2, pointRadius: 2 },
            { label: "Citas agendadas", data: citas, borderColor: "#2f6fed", backgroundColor: "rgba(47,111,237,.12)", fill: true, tension: .3, borderWidth: 2, pointRadius: 2 },
          ],
        },
        options: {
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: { legend: { position: "bottom" } },
          scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
        },
      });
      renderMessagesChart(labels, mensajes);
      renderDetailTable(rows);
    }

    function renderDetailTable(rows) {
      const tbody = document.querySelector("#detailTable tbody");
      const empty = document.getElementById("detailEmpty");
      tbody.innerHTML = "";
      if (!rows.length) { empty.classList.remove("hidden"); return; }
      empty.classList.add("hidden");
      rows.forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML = "<td>" + r.period + "</td>" +
          '<td class="num">' + fmt(r.mensajes) + "</td>" +
          '<td class="num">' + fmt(r.personas) + "</td>" +
          '<td class="num">' + fmt(r.citas) + "</td>" +
          '<td class="num">' + fmtRatio(r.ratio_mensajes_por_cita) + "</td>";
        tbody.appendChild(tr);
      });
    }

    async function loadByService() {
      const res = await api("/dashboard/api/by-service?start=" + state.start + "&end=" + state.end);
      if (res.status === 401) { showLogin(""); return; }
      const json = await res.json();
      const rows = ((json && json.data) || []).slice(0, 8);
      const empty = document.getElementById("serviceEmpty");
      const ctx = document.getElementById("serviceChart");
      if (serviceChart) serviceChart.destroy();
      if (!rows.length) { empty.classList.remove("hidden"); return; }
      empty.classList.add("hidden");
      serviceChart = new Chart(ctx, {
        type: "bar",
        data: {
          labels: rows.map((r) => r.servicio),
          datasets: [{ label: "Citas", data: rows.map((r) => r.total), backgroundColor: "#7a5cf0", borderRadius: 6 }],
        },
        options: {
          indexAxis: "y", maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
        },
      });
    }

    function exportCsv() {
      const rows = state.detail || [];
      const head = ["periodo", "mensajes", "personas", "citas", "msg_por_cita"];
      const lines = [head.join(",")];
      rows.forEach((r) => lines.push([r.period, r.mensajes, r.personas, r.citas, r.ratio_mensajes_por_cita == null ? "" : r.ratio_mensajes_por_cita].join(",")));
      const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "metricas_" + state.start + "_" + state.end + ".csv";
      a.click();
      URL.revokeObjectURL(a.href);
    }

    function loadAll() { loadSummary(); loadTimeseries(); loadByService(); }

    async function boot() {
      const res = await api("/dashboard/api/me");
      if (res.status === 401 || res.status === 503) { showLogin(""); return; }
      const me = await res.json();
      document.getElementById("clinicName").textContent = me.clinic_name || "Clínica";
      const sel = document.getElementById("yearSelect");
      sel.innerHTML = "";
      (me.years || []).forEach((y) => {
        const o = document.createElement("option");
        o.value = y; o.textContent = y; sel.appendChild(o);
      });
      sel.value = String(new Date(me.default_start).getUTCFullYear());
      populateMonthSelect();
      document.getElementById("monthSelect").value = "0";
      populateDaySelect();
      syncDateRange();
      showApp();
      loadAll();
    }

    boot();
  </script>
</body>
</html>
"""


def render_dashboard_page() -> str:
    return _DASHBOARD_HTML


__all__ = ["render_dashboard_page"]
