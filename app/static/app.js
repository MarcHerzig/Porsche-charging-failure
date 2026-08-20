const $ = (id) => document.getElementById(id);
let LAST_LIVE = null;

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      btn.classList.add("active");
      $(`tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "credentials") testConnections();
    });
  });
}

function fmtTime(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("de-CH");
}

function porscheIcon(status, isError) {
  if (isError) return "🚨";
  if (!status) return "🚗";
  if (status.includes("CHARGING") || status === "INITIALISING") return "🔌⚡";
  if (status === "COMPLETED") return "🔋";
  return "🚗";
}

function weatherIcon(day) {
  if (day.precipitation_mm >= 1) return "🌧️";
  if (day.sunshine_hours >= 7) return "☀️";
  if (day.sunshine_hours >= 3) return "⛅";
  return "☁️";
}

function renderForecast(days) {
  const strip = $("forecast-strip");
  strip.innerHTML = "";
  days.forEach((day, i) => {
    const date = new Date(day.date);
    const label = i === 0 ? "Heute" : date.toLocaleDateString("de-CH", { weekday: "short" });
    const el = document.createElement("div");
    el.className = "forecast-day";
    el.innerHTML = `
      <span class="fc-label">${label}</span>
      <span class="fc-icon">${weatherIcon(day)}</span>
      <span class="fc-value">${day.radiation_kwh_m2} kWh/m²</span>
      <span class="fc-sub">${day.sunshine_hours} h Sonne</span>
      <span class="fc-sun">🌅 ${day.sunrise || "–"} · 🌇 ${day.sunset || "–"}</span>
    `;
    strip.appendChild(el);
  });
}

async function refreshLive() {
  const res = await fetch("/api/live");
  const data = await res.json();
  LAST_LIVE = data;
  updateCurfewSolarPreview();

  $("pv-watts").textContent = data.pv_watts != null ? Math.round(data.pv_watts) : "–";
  $("consumption-watts").textContent = data.consumption_w != null ? Math.round(data.consumption_w) : "–";
  $("charging-state").textContent =
    data.charging_active === true ? "⚡ laedt (Ziel)" : data.charging_active === false ? "⏸ pausiert (Ziel)" : "–";
  $("easee-state").textContent = data.easee_op_mode
    ? `${data.easee_op_mode}${data.easee_reason ? " / " + data.easee_reason : ""}`
    : "–";
  $("porsche-state").textContent = data.porsche_captcha_pending
    ? "🧩 Captcha noetig -- im Zugangsdaten-Tab loesen"
    : data.porsche_status
      ? `${porscheIcon(data.porsche_status, !!data.porsche_error)} ${data.porsche_status}`
      : `${porscheIcon(null, !!data.porsche_error)} –`;

  $("porsche-battery-stat").textContent = data.porsche_battery != null ? `${Math.round(data.porsche_battery)}%` : "–";

  const led = $("porsche-led");
  if (data.porsche_connected === true) {
    led.className = "led ok";
    led.title = "Porsche Connect verbunden";
  } else if (data.porsche_connected === false) {
    led.className = "led fail";
    led.title = data.porsche_error || "Verbindung fehlgeschlagen";
  } else {
    led.className = "led";
    led.title = "Noch keine Daten";
  }

  if (data.porsche_is_home === true) {
    $("porsche-home-icon").textContent = "🏠";
    $("porsche-location").textContent = "Zuhause";
  } else if (data.porsche_is_home === false) {
    $("porsche-home-icon").textContent = "📍";
    $("porsche-location").textContent = `${data.porsche_distance_km} km entfernt`;
  } else {
    $("porsche-home-icon").textContent = "📍";
    $("porsche-location").textContent = "Unbekannt";
  }

  const errors = [data.solar_error, data.easee_error, data.porsche_error].filter(Boolean);
  $("live-errors").textContent = errors.join(" | ");
  $("status-dot").style.background = errors.length ? "var(--error)" : "var(--ok)";
  $("status-dot").style.boxShadow = errors.length ? "0 0 8px var(--error)" : "0 0 8px var(--ok)";

  renderForecast(data.forecast || []);
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

function updateCurfewModeVisibility() {
  const coupled = $("curfew-solar-coupled").checked;
  $("curfew-start").disabled = coupled;
  $("curfew-end").disabled = coupled;
  updateCurfewSolarPreview();
}

function updateCurfewSolarPreview() {
  const preview = $("curfew-solar-preview");
  if (!$("curfew-solar-coupled").checked) {
    preview.textContent = "";
    return;
  }
  const today = (LAST_LIVE?.forecast || [])[0];
  if (!today || !today.sunrise || !today.sunset) {
    preview.textContent = "Berechnete Zeiten erscheinen, sobald der Forecast geladen ist.";
    return;
  }
  const offset = Number($("curfew-solar-offset").value) || 0;
  const shift = (hhmm, deltaMin) => {
    const [h, m] = hhmm.split(":").map(Number);
    const d = new Date(2000, 0, 1, h, m + deltaMin);
    return d.toTimeString().slice(0, 5);
  };
  const start = shift(today.sunset, -offset);
  const end = shift(today.sunrise, offset);
  preview.textContent = `Heute: Sperrzone ${start} – ${end} (Sonnenuntergang ${today.sunset}, Sonnenaufgang ${today.sunrise})`;
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
  $("curfew-solar-coupled").checked = !!s.curfew_solar_coupled;
  $("curfew-solar-offset").value = s.curfew_solar_offset_min;
  $("reboot-cooldown").value = s.reboot_cooldown_min;
  if (s.lat != null) $("lat").value = s.lat;
  if (s.lon != null) $("lon").value = s.lon;
  $("forecast-location").textContent = s.location_name ? `— ${s.location_name}` : "";
  if (s.location_name) {
    $("location-query").value = s.location_name;
    $("location-resolved").textContent = `Aktuell: ${s.location_name} (${s.lat}, ${s.lon})`;
  }
  updateSmartVisibility();
  updateCurfewModeVisibility();
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
    curfew_solar_coupled: $("curfew-solar-coupled").checked,
    curfew_solar_offset_min: Number($("curfew-solar-offset").value),
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
  $("solar-manager-id").value = c.solar_manager_id || "";
}

async function saveCredentials() {
  const payload = {
    porsche_email: $("porsche-email").value,
    porsche_password: $("porsche-password").value,
    porsche_vin: $("porsche-vin").value,
    porsche_session: $("porsche-session").value,
    easee_email: $("easee-email").value,
    easee_password: $("easee-password").value,
    easee_charger_id: $("easee-charger-id").value,
    solar_manager_id: $("solar-manager-id").value,
    solar_api_key: $("solar-api-key").value,
  };
  const res = await fetch("/api/credentials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    $("credentials-saved").textContent = "Fehler: " + (body.detail || `HTTP ${res.status}`);
    $("credentials-saved").style.color = "var(--error)";
    return;
  }

  $("porsche-password").value = "";
  $("porsche-session").value = "";
  $("easee-password").value = "";
  $("solar-api-key").value = "";
  $("credentials-saved").style.color = "var(--ok)";
  $("credentials-saved").textContent = "Gespeichert ✓";
  setTimeout(() => ($("credentials-saved").textContent = ""), 2000);
  testConnections();
}

async function testOne(ledId, endpoint) {
  const led = $(ledId);
  led.className = "led pending";
  led.title = "Teste Verbindung...";
  try {
    const res = await fetch(endpoint, { method: "POST" });
    const body = await res.json();
    led.className = "led " + (body.ok ? "ok" : "fail");
    led.title = body.ok ? "Verbindung OK" : body.detail || "Verbindung fehlgeschlagen";
  } catch (err) {
    led.className = "led fail";
    led.title = "Fehler: " + err.message;
  }
}

function testConnections() {
  testOne("led-porsche", "/api/test/porsche").then(checkPorscheCaptcha);
  testOne("led-easee", "/api/test/easee");
  testOne("led-solar", "/api/test/solar");
}

async function checkPorscheCaptcha() {
  const res = await fetch("/api/porsche/captcha");
  const body = await res.json();
  showCaptcha(body.image);
}

function showCaptcha(image) {
  const box = $("captcha-box");
  if (image) {
    box.style.display = "block";
    $("captcha-image").src = image;
  } else {
    box.style.display = "none";
  }
}

async function submitCaptcha() {
  const code = $("captcha-code").value.trim();
  if (!code) return;
  $("captcha-hint").textContent = "Wird geprueft...";
  try {
    const res = await fetch("/api/porsche/captcha", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const body = await res.json();
    if (body.ok) {
      $("captcha-hint").textContent = "Login erfolgreich ✓";
      $("captcha-code").value = "";
      showCaptcha(null);
      testConnections();
    } else if (body.captcha_needed) {
      $("captcha-hint").textContent = "Falscher Code -- neues Captcha, bitte erneut versuchen.";
      $("captcha-code").value = "";
      showCaptcha(body.image);
    } else {
      $("captcha-hint").textContent = "Fehler: " + (body.detail || "unbekannt");
    }
  } catch (err) {
    $("captcha-hint").textContent = "Fehler: " + err.message;
  }
}

async function doRefresh() {
  const btn = $("refresh-btn");
  btn.classList.add("spinning");
  try {
    await fetch("/api/refresh", { method: "POST" });
  } catch (err) {
    // ignoriert -- refreshLive() zeigt danach ohnehin den aktuellen Fehlerstatus
  }
  await refreshLive();
  btn.classList.remove("spinning");
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
  $("curfew-solar-coupled").addEventListener("change", updateCurfewModeVisibility);
  $("curfew-solar-offset").addEventListener("input", updateCurfewSolarPreview);
  $("save-settings").addEventListener("click", saveSettings);
  $("save-credentials").addEventListener("click", saveCredentials);
  $("reboot-btn").addEventListener("click", doReboot);
  $("geocode-btn").addEventListener("click", doGeocode);
  $("refresh-btn").addEventListener("click", doRefresh);
  $("captcha-submit-btn").addEventListener("click", submitCaptcha);
  $("captcha-code").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitCaptcha();
  });

  setInterval(refreshLive, 10000);
  setInterval(refreshLog, 15000);
}

document.addEventListener("DOMContentLoaded", init);
