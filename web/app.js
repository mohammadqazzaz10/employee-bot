const $ = (id) => document.getElementById(id);
let currentUser = null;
let installPrompt = null;
let timerInterval = null;
let branchesCache = [];

function readCookie(name) {
  const prefix = name + "=";
  const item = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}
async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && path !== "/auth/login" && path !== "/auth/activate" && path !== "/setup/bootstrap") {
    headers["X-CSRF-Token"] = readCookie("employee_csrf");
  }
  const response = await fetch("/api" + path, {
    credentials: "same-origin",
    ...options,
    method,
    headers,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || "تعذر تنفيذ العملية. حاول مرة أخرى.");
  return data;
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("ar-JO", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function statusLabel(status) {
  return ({pending:"قيد الانتظار",approved:"مقبول",rejected:"مرفوض"})[status] || status;
}
function statusClass(status) { return "status-" + (["pending","approved","rejected"].includes(status) ? status : "account"); }
function flash(message, isError = false) {
  $("pageFeedback").textContent = message;
  $("pageFeedback").style.color = isError ? "#b45d54" : "#4e8b69";
  window.setTimeout(() => { if ($("pageFeedback").textContent === message) $("pageFeedback").textContent = ""; }, 5000);
}
function showLogin(message = "") {
  currentUser = null;
  $("loginView").hidden = false;
  $("portalView").hidden = true;
  $("profileButton").hidden = true;
  $("loginMessage").textContent = message;
  if (timerInterval) window.clearInterval(timerInterval);
}
function showPortal(user) {
  currentUser = user;
  $("loginView").hidden = true;
  $("portalView").hidden = false;
  $("profileButton").hidden = false;
  $("profileButton").textContent = (user.full_name || "م").trim().charAt(0);
  $("sideAvatar").textContent = (user.full_name || "م").trim().charAt(0);
  $("sideName").textContent = user.full_name || "المستخدم";
  const roleNames = {employee:"موظف",supervisor:"مشرف",branch_manager:"مدير فرع",super_admin:"مدير عام"};
  $("sideRole").textContent = roleNames[user.role] || "مستخدم";
  $("employeeName").textContent = user.full_name || "الموظف";
  $("employeeMeta").textContent = [user.job_title, user.branch_name].filter(Boolean).join(" • ") || "مرحبًا بك في مساحة العمل.";
  $("todayLabel").textContent = "مساحة العمل";
  $("dateLabel").textContent = new Intl.DateTimeFormat("ar-JO", { dateStyle: "full" }).format(new Date());
  const isAdmin = user.role !== "employee";
  document.querySelectorAll(".admin-only").forEach((el) => el.hidden = !isAdmin);
  document.querySelectorAll(".super-only").forEach((el) => el.hidden = user.role !== "super_admin");
  const employeeRequestsNav = document.querySelector('[data-page="requests"]');
  if (employeeRequestsNav) employeeRequestsNav.hidden = isAdmin;
  $("addEmployeeButton").hidden = !["super_admin", "branch_manager"].includes(user.role);
  $("requestTypesCard").hidden = isAdmin;
  $("timerCard").hidden = isAdmin;
  $("quickActions").hidden = isAdmin;
  $("adminOverview").hidden = !isAdmin;
  $("statOneLabel").textContent = isAdmin ? "دورك في النظام" : "حالة الحساب";
  $("statOne").textContent = roleNames[user.role] || "نشط";
  $("statOne").style.fontSize = isAdmin ? "14px" : "";
  if (isAdmin) loadAdminDashboard();
  else loadEmployeeDashboard();
  loadNotifications();
}
async function loadSession() {
  try {
    const data = await api("/auth/me");
    if (data.user) showPortal(data.user);
    else showLogin();
  } catch { showLogin(); }
}
async function loadEmployeeDashboard() {
  try {
    const [summary, types] = await Promise.all([api("/employee/summary"), api("/employee/request-types")]);
    const profile = summary.profile || {};
    $("employeeName").textContent = profile.full_name || currentUser.full_name;
    $("employeeMeta").textContent = [profile.job_title, profile.branch_name].filter(Boolean).join(" • ");
    $("statTwo").textContent = summary.pending_count ?? 0;
    renderTimer(summary.timer);
    $("requestTypes").innerHTML = (types.request_types || []).length
      ? types.request_types.map((type, index) => `<button class="request-type" data-request-type="${escapeHtml(type.code)}"><span class="type-symbol">${["◷","✧","▣","♧"][index % 4]}</span><strong>${escapeHtml(type.label)}</strong><small>المدة ${Number(type.duration_minutes)} دقيقة • يتطلب موافقة</small></button>`).join("")
      : '<div class="empty-cell">لا توجد أنواع طلبات مفعّلة لفرعك حاليًا.</div>';
    $("requestTypes").querySelectorAll("[data-request-type]").forEach((button) => {
      button.addEventListener("click", () => submitRequest(button.dataset.requestType, button));
    });
    $("requestTypes").querySelectorAll("button").forEach((button) => button.disabled = Boolean(summary.timer));
    renderRequests(summary.recent_requests || []);
    $("statThree").textContent = summary.timer ? "نشطة" : "لا يوجد";
  } catch (error) { flash(error.message, true); }
}
function renderTimer(timer) {
  if (timerInterval) window.clearInterval(timerInterval);
  if (!timer) {
    $("timerValue").textContent = "--:--";
    $("timerCaption").textContent = "لا توجد استراحة نشطة";
    $("timerTitle").textContent = "أنت على رأس العمل";
    $("timerDescription").textContent = "عند الموافقة على طلبك سيظهر الوقت المتبقي هنا.";
    $("timerRing").style.background = "conic-gradient(#4d9970 0deg,#e7efe9 0deg)";
    $("returnButton").hidden = true;
    return;
  }
  const end = new Date(timer.ends_at).getTime();
  const start = new Date(timer.started_at).getTime();
  const total = Math.max(1, end - start);
  $("timerTitle").textContent = "استراحتك جارية";
  $("timerDescription").textContent = "يرجى العودة إلى عملك عند انتهاء الوقت وتأكيد العودة.";
  $("timerCaption").textContent = "الوقت المتبقي";
  $("returnButton").hidden = true;
  const tick = () => {
    const remaining = Math.max(0, end - Date.now());
    const seconds = Math.floor(remaining / 1000);
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    $("timerValue").textContent = String(minutes).padStart(2,"0") + ":" + String(rest).padStart(2,"0");
    const progress = Math.min(360, Math.max(0, (Date.now() - start) / total * 360));
    $("timerRing").style.background = `conic-gradient(#4d9970 ${progress}deg,#e7efe9 ${progress}deg)`;
    if (remaining <= 0) {
      $("timerTitle").textContent = "انتهت مدة الاستراحة";
      $("timerDescription").textContent = "يرجى تأكيد عودتك إلى العمل.";
      $("timerCaption").textContent = "انتهى الوقت";
      $("returnButton").hidden = false;
      window.clearInterval(timerInterval);
    }
  };
  tick();
  timerInterval = window.setInterval(tick, 1000);
}
function renderRequests(rows) {
  $("myRequests").innerHTML = rows.length ? rows.map((row) => `<tr><td>${escapeHtml(row.request_label || row.request_type)}</td><td>${formatDate(row.requested_at)}</td><td><span class="status-pill ${statusClass(row.status)}">${statusLabel(row.status)}</span></td><td>${escapeHtml(row.notes || "—")}</td></tr>`).join("") : '<tr><td colspan="4" class="empty-cell">لا توجد طلبات حتى الآن.</td></tr>';
}
async function submitRequest(code, button) {
  button.disabled = true;
  try {
    const result = await api("/employee/requests", {method:"POST", body:JSON.stringify({request_type:code})});
    flash(result.message || "تم إرسال الطلب.");
    await loadEmployeeDashboard();
  } catch (error) { flash(error.message, true); button.disabled = false; }
}
async function loadAdminDashboard() {
  try {
    const data = await api("/admin/overview");
    const stats = data.stats || {};
    $("statTwo").textContent = stats.pending_requests ?? 0;
    $("statThree").textContent = stats.active_timers ?? 0;
    $("adminStats").innerHTML = [
      ["إجمالي الموظفين",stats.employees],["طلبات بانتظارك",stats.pending_requests],
      ["استراحات نشطة",stats.active_timers],["إشعارات غير مقروءة",stats.unread_notifications]
    ].map(([label,value]) => `<div class="admin-stat"><strong>${Number(value || 0)}</strong><small>${label}</small></div>`).join("");
    $("statTwoLabel").textContent = "طلبات معلّقة";
    $("statThreeLabel").textContent = "استراحات نشطة";
    await loadManagerRequests("pending");
  } catch (error) { flash(error.message, true); }
}
async function loadManagerRequests(status = "pending") {
  try {
    const data = await api("/admin/requests?status=" + encodeURIComponent(status));
    const rows = data.requests || [];
    $("managerRequests").innerHTML = rows.length ? rows.map((row) => `
      <article class="manager-request"><div><strong>${escapeHtml(row.full_name)} · ${escapeHtml(row.request_label || row.request_type)}</strong><p>${escapeHtml(row.job_title || "")} · ${escapeHtml(row.branch_name || "")} · ${formatDate(row.requested_at)}</p><time>${escapeHtml(row.notes || "لا توجد ملاحظات")} · ${statusLabel(row.status)}</time></div>
      ${row.status === "pending" ? `<div class="request-actions"><button class="approve-button" data-decision="approved" data-id="${row.id}">موافقة</button><button class="reject-button" data-decision="rejected" data-id="${row.id}">رفض</button></div>` : ""}</article>`).join("") : '<div class="content-card empty-cell">لا توجد طلبات في هذا القسم.</div>';
  } catch (error) { flash(error.message, true); }
}
async function loadEmployees() {
  try {
    const [data, branches] = await Promise.all([api("/admin/employees"), api("/admin/branches")]);
    branchesCache = branches.branches || [];
    $("employeesTable").innerHTML = (data.employees || []).length ? data.employees.map((employee) => `<tr><td>${escapeHtml(employee.full_name)}<br><small>${escapeHtml(employee.job_title || "")}</small></td><td>${escapeHtml(employee.employee_code)}</td><td>${escapeHtml(employee.branch_name)}</td><td><span class="status-pill ${employee.has_web_account ? "status-approved" : "status-pending"}">${employee.has_web_account ? "مفعّل" : "غير مفعّل"}</span></td><td><button class="mini-button" data-activation="${employee.id}" ${employee.has_web_account ? "disabled" : ""}>رمز تفعيل</button></td></tr>`).join("") : '<tr><td colspan="5" class="empty-cell">لا يوجد موظفون مسجلون.</td></tr>';
    fillBranchOptions();
  } catch (error) { flash(error.message, true); }
}
function fillBranchOptions() {
  const options = branchesCache.map((branch) => `<option value="${branch.id}">${escapeHtml(branch.name)}</option>`).join("");
  $("employeeBranch").innerHTML = options;
  $("adminBranch").innerHTML = options;
  $("branchField").hidden = currentUser?.role !== "super_admin";
}
async function loadAdmins() {
  try {
    const [users, branches] = await Promise.all([api("/admin/users"), api("/admin/branches")]);
    branchesCache = branches.branches || [];
    const names = Object.fromEntries(branchesCache.map((b) => [b.id,b.name]));
    $("adminsTable").innerHTML = (users.users || []).map((user) => `<tr><td>${escapeHtml(user.full_name)}</td><td>${escapeHtml(user.username)}</td><td>${user.role === "branch_manager" ? "مدير فرع" : "مشرف"}</td><td>${escapeHtml(names[user.branch_id] || "—")}</td><td><span class="status-pill ${user.is_active ? "status-approved" : "status-rejected"}">${user.is_active ? "نشط" : "موقوف"}</span></td></tr>`).join("") || '<tr><td colspan="5" class="empty-cell">لا توجد حسابات مشرفين.</td></tr>';
    fillBranchOptions();
  } catch (error) { flash(error.message, true); }
}
async function loadNotifications() {
  try {
    const data = await api("/notifications");
    const rows = data.notifications || [];
    const unread = rows.filter((row) => !row.read_at).length;
    $("notificationBadge").hidden = unread === 0;
    $("notificationBadge").textContent = unread;
    $("notificationList").innerHTML = rows.length ? rows.map((row) => `<article class="notification-item ${row.read_at ? "" : "unread"}"><div><strong>${escapeHtml(row.title)}</strong><p>${escapeHtml(row.body)}</p><time>${formatDate(row.created_at)}</time></div>${row.read_at ? "" : `<button class="mini-button" data-read="${row.id}">تمت القراءة</button>`}</article>`).join("") : '<div class="content-card empty-cell">لا توجد إشعارات جديدة.</div>';
  } catch {}
}
async function changePage(page) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".page-section").forEach((section) => section.classList.toggle("active", section.id === "page-" + page));
  $("sidebar").classList.remove("open");
  if (page === "requests" && currentUser.role === "employee") {
    try { const data = await api("/employee/requests"); renderRequests(data.requests || []); } catch (e) { flash(e.message,true); }
  }
  if (page === "employees") loadEmployees();
  if (page === "manageRequests") loadManagerRequests($("requestFilter").value);
  if (page === "admins") loadAdmins();
  if (page === "notifications") loadNotifications();
}
$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("loginSubmit");
  button.disabled = true;
  button.querySelector("span").textContent = "جارٍ التحقق...";
  $("loginMessage").textContent = "";
  try {
    const data = await api("/auth/login", {method:"POST",body:JSON.stringify({
      employee_code:$("employeeCode").value.trim(),password:$("password").value
    })});
    $("password").value = "";
    showPortal(data.user);
  } catch (error) { $("loginMessage").textContent = error.message; }
  finally { button.disabled = false; button.querySelector("span").textContent = "تسجيل الدخول"; }
});
$("togglePassword").addEventListener("click", () => {
  $("password").type = $("password").type === "password" ? "text" : "password";
});
$("showActivation").addEventListener("click", () => {
  $("loginForm").hidden = true; $("activationForm").hidden = false;
});
$("showLogin").addEventListener("click", () => {
  $("activationForm").hidden = true; $("loginForm").hidden = false;
});
$("activationForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("newPassword").value !== $("confirmPassword").value) {
    $("activationMessage").textContent = "كلمتا المرور غير متطابقتين."; return;
  }
  const button = $("activateSubmit"); button.disabled = true;
  try {
    const data = await api("/auth/activate", {method:"POST",body:JSON.stringify({
      activation_code:$("activationCode").value.trim(),password:$("newPassword").value
    })});
    $("activationMessage").style.color = "#4e8b69";
    $("activationMessage").textContent = data.message + " رقمك الوظيفي: " + data.employee_code;
    $("employeeCode").value = data.employee_code;
    $("activationForm").reset();
    window.setTimeout(() => { $("activationForm").hidden = true; $("loginForm").hidden = false; }, 1800);
  } catch (error) { $("activationMessage").style.color = "#b24e48"; $("activationMessage").textContent = error.message; }
  finally { button.disabled = false; }
});
$("logoutButton").addEventListener("click", async () => {
  try { await api("/auth/logout",{method:"POST",body:"{}"}); } catch {}
  showLogin("تم تسجيل الخروج بأمان.");
});
$("sideNav").addEventListener("click", (event) => {
  const item = event.target.closest("[data-page]");
  if (item) changePage(item.dataset.page);
});
$("viewRequests").addEventListener("click", () => changePage("requests"));
$("callSupervisor").addEventListener("click", async () => {
  const message = window.prompt("اكتب سبب طلب التواصل (اختياري):", "يرجى التواصل معي.");
  if (message === null) return;
  try { const data = await api("/employee/call-supervisor",{method:"POST",body:JSON.stringify({message})}); flash(data.message); }
  catch (error) { flash(error.message,true); }
});
$("returnButton").addEventListener("click", async () => {
  try { const data = await api("/employee/timer/return",{method:"POST",body:"{}"}); flash(data.message); await loadEmployeeDashboard(); }
  catch (error) { flash(error.message,true); }
});
$("managerRequests").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-decision]");
  if (!button) return;
  button.disabled = true;
  try {
    const data = await api("/admin/requests/" + button.dataset.id + "/decision",{
      method:"POST",body:JSON.stringify({decision:button.dataset.decision})
    });
    flash(data.message); await loadManagerRequests($("requestFilter").value); await loadAdminDashboard();
  } catch (error) { flash(error.message,true); button.disabled = false; }
});
$("requestFilter").addEventListener("change", () => loadManagerRequests($("requestFilter").value));
$("employeesTable").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-activation]");
  if (!button || button.disabled) return;
  button.disabled = true;
  try {
    const data = await api("/admin/employees/" + button.dataset.activation + "/activation",{method:"POST",body:"{}"});
    $("activationResult").textContent = data.activation_code;
    $("activationModal").showModal();
    await loadEmployees();
  } catch (error) { flash(error.message,true); button.disabled = false; }
});
$("addEmployeeButton").addEventListener("click", async () => {
  if (!branchesCache.length) { const data = await api("/admin/branches"); branchesCache = data.branches || []; fillBranchOptions(); }
  $("employeeModal").showModal();
});
$("employeeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  if (!payload.branch_id) delete payload.branch_id;
  try {
    const data = await api("/admin/employees",{method:"POST",body:JSON.stringify(payload)});
    $("employeeModal").close(); event.currentTarget.reset(); flash("تم إنشاء الموظف. أنشئ له رمز تفعيل من قائمة الموظفين.");
    await loadEmployees();
  } catch (error) { $("employeeFormMessage").textContent = error.message; }
});
$("addAdminButton").addEventListener("click", async () => {
  const data = await api("/admin/branches"); branchesCache = data.branches || []; fillBranchOptions(); $("adminModal").showModal();
});
$("adminForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    await api("/admin/users",{method:"POST",body:JSON.stringify(payload)});
    $("adminModal").close(); event.currentTarget.reset(); flash("تم إنشاء حساب المشرف.");
    await loadAdmins();
  } catch (error) { $("adminFormMessage").textContent = error.message; }
});
$("notificationList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-read]");
  if (!button) return;
  try { await api("/notifications/" + button.dataset.read + "/read",{method:"POST",body:"{}"}); await loadNotifications(); }
  catch (error) { flash(error.message,true); }
});
$("copyActivation").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("activationResult").textContent); $("copyActivation").firstChild.textContent = "تم النسخ "; }
  catch { flash("تعذر النسخ التلقائي. اضغط مطولًا على الرمز لنسخه.",true); }
});
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => $(button.dataset.close).close()));
$("mobileMenu").addEventListener("click", () => $("sidebar").classList.toggle("open"));
$("profileButton").addEventListener("click", () => changePage("home"));
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault(); installPrompt = event; $("installButton").hidden = false;
});
$("installButton").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; $("installButton").hidden = true;
});
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
loadSession();
