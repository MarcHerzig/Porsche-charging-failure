const $ = (id) => document.getElementById(id);

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      btn.classList.add("active");
      $(`tab-${btn.dataset.tab}`).classList.add("active");
    });
  });
}

function fmtTime(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("de-CH");
}

async function refreshLive() {
  const res = await fetch("/api/live");
  const data = await res.json();

  $("pv-watts").textContent = data.pv_watts != null ? Math.round(data.pv_watts) : "–";
  $("consumption-watts").textContent = data.consumption_w != null ? Math.round(data.consumption_w) : "–";
  $("charging-state").textContent =
    data.charging_active === true ? "⚡ laedt (Ziel)" : data.charging_active === false ? "⏸ pausiert (Ziel)" : "–";
  $("easee-state").textContent = data.easee_op_mode
    ? `${data.easee_op_mode}${data.easee_reason ? " / " + data.easee_reason : ""}`
    : "–";
  $("porsche-state").textContent = data.porsche_status
    ? `${data.porsche_status}${data.porsche_battery != null ? " (" + data.porsche_battery + "%)" : ""}`
    : "–";

  const errors = [data.solar_error, data.easee_error, data.porsche_error].filter(Boolean);
  $("live-errors").textContent = errors.join(" | ");
  $("status-dot").style.background = errors.length ? "var(--error)" : "var(--ok)";
  $("status-dot").style.boxShadow = errors.length ? "0 0 8px var(--error)" : "0 0 8px var(--ok)";

  const tbody = document.querySelector("#forecast-table tbody");
  tbody.innerHTML = "";
  (data.forecast || []).forEach((day) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${day.date}</td><td>${day.radiation_kwh_m2}</td><td>${day.sunshine_hours}</td>`;
    tbody.appendChild(tr);
  });
  $("forecast-error").textContent = data.forecast_error || "";
}

async function refreshLog() {
  const res = await fetch("/api/log?limit=15");
  const events = await res.json();
  const tbody = document.querySelector("#log-table tbody");
  tbody.innerHTML = "";
  events.forEach((e) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${fmtTime(e.ts)}</td><td>${e.type}</td><td>${e.message}</td>`;
    tbody.appendChild(tr);
  });
}

function updateSmartVisibility() {
  const mode = document.querySelector('input[name="mode"]:checked')?.value;
  $("smart-settings").style.display = mode === "smart" ? "block" : "none";
}

async function loadSettings() {
  const res = await fetch("/api/settings");
  const s = await res.json();
  document.querySelector(`input[name="mode"][value="${s.mode}"]`).checked = true;
  $("threshold").value = s.threshold_w;
  $("threshold-value").textContent = `${s.threshold_w} W`;
  $("start-debounce").value = s.start_debounce_min;
  $("stop-debounce").value = s.stop_debounce_min;
  $("curfew-enabled").checked = !!s.curfew_enabled;
  $("curfew-start").value = s.curfew_start;
  $("curfew-end").value = s.curfew_end;
  $("reboot-cooldown").value = s.reboot_cooldown_min;
  if (s.lat != null) $("lat").value = s.lat;
  if (s.lon != null) $("lon").value = s.lon;
  $("forecast-location").textContent = s.location_name ? `— ${s.location_name}` : "";
  if (s.location_name) {
    $("location-query").value = s.location_name;
    $("location-resolved").textContent = `Aktuell: ${s.location_name} (${s.lat}, ${s.lon})`;
  }
  updateSmartVisibility();
}

async function saveSettings() {
  const payload = {
    mode: document.querySelector('input[name="mode"]:checked').value,
    threshold_w: Number($("threshold").value),
    start_debounce_min: Number($("start-debounce").value),
    stop_debounce_min: Number($("stop-debounce").value),
    curfew_enabled: $("curfew-enabled").checked,
    curfew_start: $("curfew-start").value,
    curfew_end: $("curfew-end").value,
    reboot_cooldown_min: Number($("reboot-cooldown").value),
  };
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  $("settings-saved").textContent = "Gespeichert ✓";
  setTimeout(() => ($("settings-saved").textContent = ""), 2000);
}

async function loadCredentials() {
  const res = await fetch("/api/credentials");
  const c = await res.json();
  $("porsche-email").value = c.porsche_email || "";
  $("porsche-vin").value = c.porsche_vin || "";
  $("easee-email").value = c.easee_email || "";
  $("easee-charger-id").value = c.easee_charger_id || "";
  $("solar-base-url").value = c.solar_base_url || "";
}

async function saveCredentials() {
  const payload = {
    porsche_email: $("porsche-email").value,
    porsche_password: $("porsche-password").value,
    porsche_vin: $("porsche-vin").value,
    easee_email: $("easee-email").value,
    easee_password: $("easee-password").value,
    easee_charger_id: $("easee-charger-id").value,
    solar_base_url: $("solar-base-url").value,
    solar_api_key: $("solar-api-key").value,
  };
  await fetch("/api/credentials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  $("porsche-password").value = "";
  $("easee-password").value = "";
  $("solar-api-key").value = "";
  $("credentials-saved").textContent = "Gespeichert ✓";
  setTimeout(() => ($("credentials-saved").textContent = ""), 2000);
}

async function doGeocode() {
  const query = $("location-query").value.trim();
  if (!query) return;
  $("location-resolved").textContent = "Suche...";
  try {
    const res = await fetch("/api/geocode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    $("lat").value = body.lat;
    $("lon").value = body.lon;
    $("location-resolved").textContent = `Gefunden: ${body.display_name} (${body.lat}, ${body.lon})`;
    $("forecast-location").textContent = `— ${body.display_name}`;
  } catch (err) {
    $("location-resolved").textContent = "Fehler: " + err.message;
  }
}

async function doReboot() {
  $("reboot-hint").textContent = "Reboot wird ausgeloest...";
  try {
    const res = await fetch("/api/reboot", { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    $("reboot-hint").textContent = "Reboot ausgeloest ✓";
  } catch (err) {
    $("reboot-hint").textContent = "Fehler: " + err.message;
  }
  setTimeout(() => ($("reboot-hint").textContent = ""), 5000);
}

function init() {
  setupTabs();
  loadSettings();
  loadCredentials();
  refreshLive();
  refreshLog();

  $("threshold").addEventListener("input", () => {
    $("threshold-value").textContent = `${$("threshold").value} W`;
  });
  document.querySelectorAll('input[name="mode"]').forEach((el) => el.addEventListener("change", updateSmartVisibility));
  $("save-settings").addEventListener("click", saveSettings);
  $("save-credentials").addEventListener("click", saveCredentials);
  $("reboot-btn").addEventListener("click", doReboot);
  $("geocode-btn").addEventListener("click", doGeocode);

  setInterval(refreshLive, 10000);
  setInterval(refreshLog, 15000);
}

document.addEventListener("DOMContentLoaded", init);
