/* Reliable foreground notification delivery: polling, sound, toast actions and OS notifications. */
(() => {
  "use strict";
  const SOUND_KEY = "employee-sound-enabled";
  if (localStorage.getItem(SOUND_KEY) === null) localStorage.setItem(SOUND_KEY, "true");
  const seen = new Set();
  let initialized = false;
  let polling = false;
  let eventStream = null;
  let audioContext = null;
  let controlsObserver = null;

  const portalIsOpen = () => {
    const portal = document.getElementById("portalView");
    return Boolean(portal && !portal.hidden);
  };

  function installStyles() {
    if (document.getElementById("notificationRuntimeStyles")) return;
    const style = document.createElement("style");
    style.id = "notificationRuntimeStyles";
    style.textContent = `
      #notificationToastRegion{position:fixed;z-index:9999;top:calc(14px + env(safe-area-inset-top));left:16px;width:min(390px,calc(100vw - 32px));display:grid;gap:10px;pointer-events:none}
      .notification-toast{pointer-events:auto;display:grid;grid-template-columns:1fr auto;gap:8px 12px;align-items:start;padding:15px 16px;background:#fff;border:1px solid #dce9df;border-right:4px solid #39845e;border-radius:15px;box-shadow:0 14px 42px #153d2b24;animation:notificationToastIn .22s ease-out}
      .notification-toast strong{display:block;color:#244b39;font-size:14px;margin-bottom:5px}
      .notification-toast p{margin:0;color:#52685c;font-size:12px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}
      .notification-toast-actions{grid-column:1/-1;display:flex;gap:8px;align-items:center}
      .notification-toast-actions button{border:0;border-radius:9px;padding:8px 12px;font:inherit;font-size:12px;cursor:pointer}
      .notification-toast-open{background:#eaf4ed;color:#285b40;font-weight:700}
      .notification-toast-close{background:#f3f5f3;color:#637168}
      @keyframes notificationToastIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
      @media(prefers-reduced-motion:reduce){.notification-toast{animation:none}}
    `;
    document.head.appendChild(style);
  }

  function toastRegion() {
    let region = document.getElementById("notificationToastRegion");
    if (!region) {
      region = document.createElement("div");
      region.id = "notificationToastRegion";
      region.setAttribute("role", "region");
      region.setAttribute("aria-label", "التنبيهات الجديدة");
      region.setAttribute("aria-live", "polite");
      document.body.appendChild(region);
    }
    return region;
  }

  async function playAlertTone() {
    if (localStorage.getItem(SOUND_KEY) !== "true") return;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    try {
      audioContext ||= new Audio();
      if (audioContext.state === "suspended") await audioContext.resume();
      if (audioContext.state !== "running") return;

      // A brighter, louder four-note chime. The gain is about 3x the old tone,
      // with a compressor to keep peaks controlled on different devices.
      const now = audioContext.currentTime;
      const compressor = audioContext.createDynamicsCompressor();
      compressor.threshold.setValueAtTime(-10, now);
      compressor.knee.setValueAtTime(12, now);
      compressor.ratio.setValueAtTime(8, now);
      compressor.attack.setValueAtTime(0.003, now);
      compressor.release.setValueAtTime(0.16, now);
      compressor.connect(audioContext.destination);

      [784, 988, 1175, 988].forEach((frequency, index) => {
        const start = now + index * 0.17;
        const duration = 0.23;
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.type = "triangle";
        oscillator.frequency.setValueAtTime(frequency, start);
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.22, start + 0.018);
        gain.gain.setValueAtTime(0.22, start + 0.09);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
        oscillator.connect(gain);
        gain.connect(compressor);
        oscillator.start(start);
        oscillator.stop(start + duration + 0.01);
      });
    } catch (_) {}
  }

  function unlockAudioFromGesture() {
    if (localStorage.getItem(SOUND_KEY) !== "true") return;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    try {
      audioContext ||= new Audio();
      if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
    } catch (_) {}
  }

  function showToast(item) {
    const region = toastRegion();
    const card = document.createElement("article");
    card.className = "notification-toast";
    const content = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || "تنبيه جديد";
    const body = document.createElement("p");
    body.textContent = item.body || "لديك إشعار جديد.";
    content.append(title, body);

    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "notification-toast-close";
    dismiss.textContent = "إغلاق";
    dismiss.setAttribute("aria-label", "إغلاق التنبيه");

    const actions = document.createElement("div");
    actions.className = "notification-toast-actions";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "notification-toast-open";
    open.textContent = "عرض الإشعارات";
    open.addEventListener("click", () => {
      if (typeof window.changePage === "function") window.changePage("notifications");
      if (typeof window.loadNotifications === "function") window.loadNotifications();
      card.remove();
    });
    dismiss.addEventListener("click", () => card.remove());
    actions.append(open, dismiss);
    card.append(content, actions);
    region.prepend(card);
    while (region.children.length > 4) region.lastElementChild.remove();
    window.setTimeout(() => card.remove(), 14000);
  }

  async function showSystemNotification(item) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const options = {
      body: item.body || "لديك إشعار جديد.",
      icon: "/icons/icon.svg",
      tag: "employee-notification-" + item.id,
      renotify: false,
      data: { notificationId: item.id }
    };
    try {
      if ("serviceWorker" in navigator) {
        const registration = await navigator.serviceWorker.ready;
        if (registration && registration.showNotification) {
          await registration.showNotification(item.title || "تنبيه جديد", options);
          return;
        }
      }
      const notification = new Notification(item.title || "تنبيه جديد", options);
      notification.onclick = () => {
        window.focus();
        if (typeof window.changePage === "function") window.changePage("notifications");
        notification.close();
      };
    } catch (_) {}
  }

  function updateBadge(rows) {
    const badge = document.getElementById("notificationBadge");
    if (!badge) return;
    const unread = rows.filter(row => !row.read_at).length;
    badge.textContent = String(unread);
    badge.hidden = unread === 0;
  }

  function stopEventStream() {
    if (eventStream) {
      eventStream.close();
      eventStream = null;
    }
  }

  function ensureEventStream() {
    if (!portalIsOpen() || eventStream || !("EventSource" in window)) return;
    eventStream = new EventSource("/api/events/notifications", { withCredentials: true });
    eventStream.addEventListener("refresh", () => {
      pollNotifications();
      window.dispatchEvent(new Event("employee-portal:refresh"));
    });
    // EventSource automatically reconnects after a dropped stream.
  }

  async function pollNotifications() {
    if (!portalIsOpen()) {
      initialized = false;
      seen.clear();
      stopEventStream();
      return;
    }
    ensureEventStream();
    if (polling) return;
    polling = true;
    try {
      const response = await fetch("/api/notifications", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      if (response.status === 401) {
        initialized = false;
        seen.clear();
        stopEventStream();
        return;
      }
      if (!response.ok) return;
      const data = await response.json();
      const rows = Array.isArray(data.notifications) ? data.notifications : [];
      updateBadge(rows);

      if (!initialized) {
        rows.forEach(row => seen.add(String(row.id)));
        initialized = true;
        return;
      }

      const fresh = rows.filter(row => !seen.has(String(row.id)));
      for (const item of fresh) {
        seen.add(String(item.id));
        if (item.read_at) continue;
        showToast(item);
        playAlertTone();
        if (document.hidden) await showSystemNotification(item);
      }
      while (seen.size > 120) seen.delete(seen.values().next().value);
      if (fresh.some(item => !item.read_at) && typeof window.loadNotifications === "function") {
        window.loadNotifications().catch(() => {});
      }
    } catch (_) {
      // A temporary network failure is retried by the next polling cycle.
    } finally {
      polling = false;
    }
  }

  function attachSoundControl() {
    const existing = document.getElementById("employeeSoundButton");
    if (!existing || existing.dataset.runtimeSoundAttached === "true") return;
    // Replace the legacy button to avoid its stale closure toggling a different state.
    const button = existing.cloneNode(true);
    button.dataset.runtimeSoundAttached = "true";
    existing.replaceWith(button);
    const sync = () => {
      const enabled = localStorage.getItem(SOUND_KEY) === "true";
      button.textContent = enabled ? "♫" : "♩";
      button.title = enabled ? "الصوت مفعّل — اضغط لإيقافه أو تشغيل تجربة صوتية" : "الصوت متوقف — اضغط لتفعيل صوت التنبيهات";
      button.setAttribute("aria-label", button.title);
    };
    button.addEventListener("click", () => {
      const nextEnabled = localStorage.getItem(SOUND_KEY) !== "true";
      localStorage.setItem(SOUND_KEY, String(nextEnabled));
      sync();
      if (nextEnabled) {
        unlockAudioFromGesture();
        playAlertTone();
      }
    });
    sync();
  }

  installStyles();
  toastRegion();
  attachSoundControl();
  controlsObserver = new MutationObserver(attachSoundControl);
  controlsObserver.observe(document.querySelector(".topbar-actions") || document.body, { childList: true, subtree: true });

  document.addEventListener("pointerdown", unlockAudioFromGesture, { passive: true });
  document.addEventListener("keydown", unlockAudioFromGesture);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollNotifications();
  });
  window.addEventListener("focus", pollNotifications);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", event => {
      if (event.data?.type === "OPEN_NOTIFICATIONS" && typeof window.changePage === "function") {
        window.changePage("notifications");
      }
    });
  }

  window.setInterval(pollNotifications, 15000);
  window.setTimeout(pollNotifications, 1200);
})();
