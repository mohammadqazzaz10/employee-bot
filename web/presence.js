/* Employee presence heartbeat: page/visibility only, no GPS or device-location collection. */
(() => {
  "use strict";
  let timer = null;
  let sending = false;
  let lastState = "";
  const csrf = () => decodeURIComponent(document.cookie.split("; ").find(x => x.startsWith("employee_csrf="))?.split("=")[1] || "");
  function state() {
    const active = document.querySelector(".page-section.active");
    const page = (active?.id || "page-dashboard").replace(/^page-/, "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 80) || "dashboard";
    return {page, visible: !document.hidden};
  }
  async function send(force = false) {
    if (!window.currentUser || window.currentUser.role !== "employee" || sending) return;
    const value = state();
    const key = value.page + "|" + value.visible;
    if (!force && key === lastState) {
      // Heartbeat still refreshes server-side online status even when the page is unchanged.
    }
    sending = true;
    try {
      const response = await fetch("/api/employee/presence", {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type":"application/json","X-CSRF-Token":csrf()},
        body: JSON.stringify(value), cache: "no-store"
      });
      if (response.ok) lastState = key;
    } catch (_) {
    } finally { sending = false; }
  }
  function start() {
    stop();
    send(true);
    timer = window.setInterval(() => send(), 45000);
  }
  function stop() {
    if (timer) window.clearInterval(timer);
    timer = null;
    lastState = "";
  }
  window.addEventListener("employee-portal:opened", start);
  window.addEventListener("employee-portal:closed", stop);
  document.addEventListener("visibilitychange", () => { if (window.currentUser?.role === "employee") send(true); });
  document.addEventListener("click", event => {
    if (event.target.closest(".nav-item[data-page]") && window.currentUser?.role === "employee") {
      window.setTimeout(() => send(true), 180);
    }
  });
  window.addEventListener("focus", () => { if (window.currentUser?.role === "employee") send(true); });
})();
