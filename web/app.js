const $ = (id) => document.getElementById(id);
const loginView = $("loginView");
const portalView = $("portalView");
let installPrompt = null;

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.detail || payload?.message || "تعذر تنفيذ الطلب. حاول مرة أخرى.");
  return payload;
}

function showLogin(message = "") {
  loginView.hidden = false;
  portalView.hidden = true;
  $("loginMessage").textContent = message;
}
function showPortal(user) {
  loginView.hidden = true;
  portalView.hidden = false;
  $("employeeName").textContent = user.full_name || "الموظف";
  $("employeeMeta").textContent = [user.job_title, user.branch_name].filter(Boolean).join(" • ");
  $("profileButton").hidden = false;
  $("profileButton").textContent = (user.full_name || "م").trim().charAt(0);
  $("todayLabel").textContent = new Intl.DateTimeFormat("ar-JO", { dateStyle: "full" }).format(new Date());
}

async function loadSession() {
  try {
    const data = await api("/auth/me");
    if (data?.user) showPortal(data.user);
    else showLogin();
  } catch {
    showLogin();
  }
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("loginSubmit");
  button.disabled = true;
  button.textContent = "جارٍ التحقق...";
  $("loginMessage").textContent = "";
  try {
    const data = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        employee_code: $("employeeCode").value.trim(),
        password: $("password").value,
      }),
    });
    if (!data?.user) throw new Error("استجابة تسجيل الدخول غير مكتملة.");
    $("password").value = "";
    showPortal(data.user);
  } catch (error) {
    $("loginMessage").textContent = error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = 'دخول إلى حسابي <span>←</span>';
  }
});

$("togglePassword").addEventListener("click", () => {
  const input = $("password");
  input.type = input.type === "password" ? "text" : "password";
});
$("logoutButton").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST", body: "{}" }); } catch {}
  $("profileButton").hidden = true;
  showLogin("تم تسجيل الخروج.");
});
$("actionGrid").addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  $("portalMessage").textContent = "سيتم تفعيل هذه الخدمة بعد ربط الواجهة بواجهات النظام وقاعدة البيانات.";
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  $("installButton").hidden = false;
});
$("installButton").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("installButton").hidden = true;
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
loadSession();
