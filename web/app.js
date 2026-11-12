const $ = (id) => document.getElementById(id);
let currentUser = null;
let installPrompt = null;
let timerInterval = null;
let renderedTimerKey = null;
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
  window.dispatchEvent(new Event("employee-portal:closed"));
  $("profileButton").hidden = true;
  $("loginMessage").textContent = message;
  if (timerInterval) window.clearInterval(timerInterval);
  if (liveSyncTimer) window.clearInterval(liveSyncTimer);
  renderedTimerKey = null;
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
  if (user.must_change_password) {
    $("passwordModal").showModal();
    return;
  }
  if (isAdmin) loadAdminDashboard();
  else loadEmployeeDashboard();
  loadNotifications();
  const pendingNotificationId = new URLSearchParams(window.location.search).get("openNotification");
  if (pendingNotificationId) {
    api("/notifications").then(async (data) => {
      const notification = (data.notifications || []).find((row) => Number(row.id) === Number(pendingNotificationId));
      if (notification) {
        if (!notification.read_at) await api("/notifications/" + notification.id + "/read", {method:"POST",body:"{}"});
        await loadNotifications();
        await openEmployeeNotification(notification);
      }
    }).catch(() => {});
    const cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete("openNotification");
    window.history.replaceState({}, "", cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
  }
  startLiveSync();
  window.dispatchEvent(new Event("employee-portal:opened"));
}
let liveSyncTimer = null;
let liveSyncBusy = false;
let portalMutationBusy = false;
let lastRequestTypesRefresh = 0;

async function syncPortalData() {
  if (!currentUser || document.hidden || liveSyncBusy || portalMutationBusy) return;
  const portal = $("portalView");
  if (!portal || portal.hidden || currentUser.must_change_password) return;
  liveSyncBusy = true;
  try {
    if (currentUser.role === "employee") {
      const refreshTypes = Date.now() - lastRequestTypesRefresh >= 30000;
      await loadEmployeeDashboard({ silent: true, refreshTypes });
    } else {
      // The overview endpoint is refreshed independently from the request list.
      // Only fetch the selected request filter while that page is actually open.
      await loadAdminDashboard({ silent: true, loadRequests: false });
      const activePage = document.querySelector(".page-section.active")?.id;
      if (activePage === "page-manageRequests") {
        await loadManagerRequests($("requestFilter")?.value || "pending", { silent: true });
      }
    }
  } finally {
    liveSyncBusy = false;
  }
}

function startLiveSync() {
  if (liveSyncTimer) window.clearInterval(liveSyncTimer);
  liveSyncTimer = window.setInterval(syncPortalData, 3000);
}
window.addEventListener("employee-portal:refresh", () => {
  syncPortalData();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && currentUser) {
    syncPortalData();
    loadNotifications();
  }
});

async function loadSession() {
  try {
    const data = await api("/auth/me");
    if (data.user) showPortal(data.user);
    else showLogin();
  } catch { showLogin(); }
}
async function loadEmployeeDashboard({ silent = false, refreshTypes = true } = {}) {
  try {
    const requests = [api("/employee/summary")];
    if (refreshTypes) requests.push(api("/employee/request-types"));
    const [summary, types] = await Promise.all(requests);
    const profile = summary.profile || {};
    $("employeeName").textContent = profile.full_name || currentUser.full_name;
    $("employeeMeta").textContent = [profile.job_title, profile.branch_name].filter(Boolean).join(" • ");
    $("statTwo").textContent = summary.pending_count ?? 0;
    renderTimer(summary.timer);
    if (types) {
      lastRequestTypesRefresh = Date.now();
      $("requestTypes").innerHTML = (types.request_types || []).length
        ? types.request_types.map((type, index) => `<button class="request-type" data-request-type="${escapeHtml(type.code)}"><span class="type-symbol">${["◷","✧","▣","♧"][index % 4]}</span><strong>${escapeHtml(type.label)}</strong><small>المدة ${Number(type.duration_minutes)} دقيقة • يتطلب موافقة</small></button>`).join("")
        : '<div class="empty-cell">لا توجد أنواع طلبات مفعّلة لفرعك حاليًا.</div>';
      $("requestTypes").querySelectorAll("[data-request-type]").forEach((button) => {
        button.addEventListener("click", () => submitRequest(button.dataset.requestType, button));
      });
    }
    $("requestTypes").querySelectorAll("button").forEach((button) => button.disabled = Boolean(summary.timer));
    renderRequests(summary.recent_requests || []);
    $("statThree").textContent = summary.timer ? "نشطة" : "لا يوجد";
  } catch (error) {
    if (!silent) flash(error.message, true);
  }
}
function renderTimer(timer) {
  const timerKey = timer ? [timer.id ?? "", timer.started_at ?? "", timer.ends_at ?? ""].join("|") : "none";
  if (timerKey === renderedTimerKey) return;
  renderedTimerKey = timerKey;
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
  $("myRequests").innerHTML = rows.length ? rows.map((row) => `<tr class="interactive-row" data-request-id="${Number(row.id)}" tabindex="0" title="اضغط لتحديد الطلب"><td>${escapeHtml(row.request_label || row.request_type)}</td><td>${formatDate(row.requested_at)}</td><td><span class="status-pill ${statusClass(row.status)}">${statusLabel(row.status)}</span></td><td>${escapeHtml(row.notes || "—")}</td></tr>`).join("") : '<tr><td colspan="4" class="empty-cell">لا توجد طلبات حتى الآن.</td></tr>';
}
async function submitRequest(code, button) {
  if (portalMutationBusy) return;
  portalMutationBusy = true;
  button.disabled = true;
  try {
    const result = await api("/employee/requests", {method:"POST", body:JSON.stringify({request_type:code})});
    flash(result.message || "تم إرسال الطلب.");
    await loadEmployeeDashboard();
  } catch (error) {
    flash(error.message, true);
    button.disabled = false;
  } finally {
    portalMutationBusy = false;
  }
}
async function loadAdminDashboard({ silent = false, loadRequests = true } = {}) {
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
    if (loadRequests) await loadManagerRequests("pending");
  } catch (error) { if (!silent) flash(error.message, true); }
}
async function loadManagerRequests(status = "pending", { silent = false } = {}) {
  try {
    const data = await api("/admin/requests?status=" + encodeURIComponent(status));
    const rows = data.requests || [];
    $("managerRequests").innerHTML = rows.length ? rows.map((row) => `
      <article class="manager-request" data-request-id="${Number(row.id)}"><div><strong>${escapeHtml(row.full_name)} · ${escapeHtml(row.request_label || row.request_type)}</strong><p>${escapeHtml(row.job_title || "")} · ${escapeHtml(row.branch_name || "")} · ${formatDate(row.requested_at)}</p><time>${escapeHtml(row.notes || "لا توجد ملاحظات")} · ${statusLabel(row.status)}</time></div>
      ${row.status === "pending" ? `<div class="request-actions"><button class="approve-button" data-decision="approved" data-id="${row.id}">موافقة</button><button class="reject-button" data-decision="rejected" data-id="${row.id}">رفض</button></div>` : ""}</article>`).join("") : '<div class="content-card empty-cell">لا توجد طلبات في هذا القسم.</div>';
  } catch (error) { if (!silent) flash(error.message, true); }
}
async function loadEmployees() {
  try {
    const [data, branches] = await Promise.all([api("/admin/employees"), api("/admin/branches")]);
    branchesCache = branches.branches || [];
    $("employeesTable").innerHTML = (data.employees || []).length ? data.employees.map((employee) => `<tr data-employee-id="${Number(employee.id)}"><td>${escapeHtml(employee.full_name)}<br><small>${escapeHtml(employee.job_title || "")}</small></td><td>${escapeHtml(employee.employee_code)}</td><td>${escapeHtml(employee.branch_name)}</td><td><span class="status-pill ${employee.has_web_account ? "status-approved" : "status-pending"}">${employee.has_web_account ? "مفعّل" : "غير مفعّل"}</span></td><td>${["super_admin","branch_manager"].includes(currentUser.role) ? (employee.has_web_account ? `<button class="mini-button" data-reset-password="${employee.id}">إعادة تعيين</button>` : `<button class="mini-button" data-activation="${employee.id}">رمز تفعيل</button>`) : "—"}</td></tr>`).join("") : '<tr><td colspan="5" class="empty-cell">لا يوجد موظفون مسجلون.</td></tr>';
    fillBranchOptions();
  } catch (error) { flash(error.message, true); }
}
function fillBranchOptions() {
  const options = branchesCache.map((branch) => `<option value="${branch.id}">${escapeHtml(branch.name)}</option>`).join("");
  $("employeeBranch").innerHTML = options;
  $("adminBranch").innerHTML = options;
  $("branchField").hidden = currentUser?.role !== "super_admin";
}
async function loadSettings() {
  try {
    const branchData = await api("/admin/branches");
    branchesCache = branchData.branches || [];
    const selector = $("settingsBranchSelector");
    if (currentUser.role === "super_admin") {
      selector.innerHTML = branchesCache.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join("");
      selector.hidden = false;
      if (!selector.value && branchesCache.length) selector.value = String(branchesCache[0].id);
    }
    const branchId = currentUser.role === "super_admin" ? selector.value : currentUser.branch_id;
    if (!branchId) throw new Error("لا يوجد فرع نشط لإدارة إعداداته.");
    const query = currentUser.role === "super_admin" ? "?branch_id=" + encodeURIComponent(branchId) : "";
    const [settingsData, typesData] = await Promise.all([
      api("/admin/settings" + query), api("/admin/request-types" + query)
    ]);
    const form = $("branchSettingsForm"), values = settingsData.settings;
    for (const name of ["smoke_enabled","lunch_enabled"]) form.elements[name].checked = Boolean(values[name]);
    for (const name of ["smoke_daily_limit","smoke_duration_minutes","smoke_start_time","smoke_end_time","smoke_gap_minutes","lunch_duration_minutes","lunch_start_time","lunch_end_time"]) {
      form.elements[name].value = values[name] ?? "";
    }
    renderAdminRequestTypes(typesData.request_types || []);
    fillBranchOptions();
  } catch (error) { $("settingsMessage").textContent = error.message; }
}
function renderAdminRequestTypes(rows) {
  $("requestTypesAdmin").innerHTML = rows.length ? rows.map((row) => `<tr>
    <td>${escapeHtml(row.label)}</td>
    <td><input type="number" min="1" max="240" value="${Number(row.duration_minutes)}" data-type-duration="${row.id}" style="width:82px;height:32px;border:1px solid #e5ece7;border-radius:7px;padding:0 7px"></td>
    <td><span class="status-pill ${row.is_active ? "status-approved" : "status-rejected"}">${row.is_active ? "مفعّل" : "معطّل"}</span></td>
    <td><div class="request-actions"><button class="mini-button" data-save-type="${row.id}">حفظ</button><button class="mini-button" data-toggle-type="${row.id}" data-active="${row.is_active ? "true" : "false"}">${row.is_active ? "تعطيل" : "تفعيل"}</button></div></td>
  </tr>`).join("") : '<tr><td colspan="4" class="empty-cell">لا توجد أنواع طلبات في هذا الفرع.</td></tr>';
}
function selectedSettingsBranch() {
  return currentUser?.role === "super_admin" ? $("settingsBranchSelector").value : currentUser?.branch_id;
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
    $("notificationList").innerHTML = rows.length ? rows.map((row) => `<article class="notification-item ${row.read_at ? "" : "unread"}" data-notification-id="${Number(row.id)}"><div><strong>${escapeHtml(row.title)}</strong><p>${escapeHtml(row.body)}</p><time>${formatDate(row.created_at)}</time><details class="notification-detail"><summary>عرض التفاصيل</summary><p>${escapeHtml(row.body)}</p></details></div><button class="notification-open" type="button" data-notification-open="${Number(row.id)}">فتح الإشعار ←</button></article>`).join("") : '<div class="content-card empty-cell">لا توجد إشعارات جديدة.</div>';
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
  if (page === "settings") loadSettings();
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
    const activationCode = $("activationCode").value
      .replace(/[\s\u200B-\u200F\u202A-\u202E\u2060\uFEFF]/g, "");
    const password = $("newPassword").value;
    if (activationCode.length < 24) {
      $("activationMessage").style.color = "#b24e48";
      $("activationMessage").textContent = "رمز التفعيل غير مكتمل. انسخه كاملاً من الإدارة.";
      return;
    }
    if (password.length < 10 || password.length > 256) {
      $("activationMessage").style.color = "#b24e48";
      $("activationMessage").textContent = "كلمة المرور يجب أن تكون بين 10 و256 خانة.";
      return;
    }
    const data = await api("/auth/activate", {method:"POST",body:JSON.stringify({
      activation_code:activationCode,password
    })});
    $("activationMessage").style.color = "#4e8b69";
    $("activationMessage").textContent = data.message + " رقمك الوظيفي: " + data.employee_code;
    $("employeeCode").value = data.employee_code;
    $("activationForm").reset();
    window.setTimeout(() => { $("activationForm").hidden = true; $("loginForm").hidden = false; }, 1800);
  } catch (error) { $("activationMessage").style.color = "#b24e48"; $("activationMessage").textContent = error.message; }
  finally { button.disabled = false; }
});
$("passwordForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const current = form.elements.current_password.value;
  const next = form.elements.new_password.value;
  const confirm = form.elements.confirm_password.value;
  if (next !== confirm) { $("passwordFormMessage").textContent = "كلمتا المرور الجديدتان غير متطابقتين."; return; }
  try {
    const data = await api("/auth/change-password", {method:"POST",body:JSON.stringify({current_password:current,new_password:next})});
    currentUser.must_change_password = false;
    $("passwordModal").close();
    form.reset();
    flash(data.message || "تم تغيير كلمة المرور.");
    if (currentUser.role === "employee") await loadEmployeeDashboard();
    else await loadAdminDashboard();
    await loadNotifications();
    startLiveSync();
    window.dispatchEvent(new Event("employee-portal:opened"));
  } catch (error) { $("passwordFormMessage").textContent = error.message; }
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
  if (!button || portalMutationBusy) return;
  portalMutationBusy = true;
  button.disabled = true;
  try {
    const data = await api("/admin/requests/" + button.dataset.id + "/decision",{
      method:"POST",body:JSON.stringify({decision:button.dataset.decision})
    });
    flash(data.message);
    await loadManagerRequests($("requestFilter").value);
    await loadAdminDashboard({ loadRequests: false });
  } catch (error) {
    flash(error.message,true);
    button.disabled = false;
  } finally {
    portalMutationBusy = false;
  }
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
$("employeesTable").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-reset-password]");
  if (!button) return;
  button.disabled = true;
  try {
    const data = await api("/admin/employees/" + button.dataset.resetPassword + "/reset-password",{method:"POST",body:"{}"});
    $("temporaryPasswordResult").textContent = data.temporary_password;
    $("temporaryPasswordModal").showModal();
    flash(data.message);
  } catch (error) { flash(error.message,true); }
  finally { button.disabled = false; }
});
$("addBranchButton").addEventListener("click", () => $("branchModal").showModal());
$("branchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const data = await api("/admin/branches",{method:"POST",body:JSON.stringify(payload)});
    $("branchModal").close();
    form.reset();
    flash(data.message || "تم إنشاء الفرع.");
    await loadSettings();
  } catch (error) { $("branchFormMessage").textContent = error.message; }
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
$("settingsBranchSelector").addEventListener("change", loadSettings);
$("branchSettingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    smoke_enabled: form.elements.smoke_enabled.checked,
    smoke_daily_limit: Number(form.elements.smoke_daily_limit.value),
    smoke_duration_minutes: Number(form.elements.smoke_duration_minutes.value),
    smoke_start_time: form.elements.smoke_start_time.value,
    smoke_end_time: form.elements.smoke_end_time.value || null,
    smoke_gap_minutes: Number(form.elements.smoke_gap_minutes.value),
    lunch_enabled: form.elements.lunch_enabled.checked,
    lunch_duration_minutes: Number(form.elements.lunch_duration_minutes.value),
    lunch_start_time: form.elements.lunch_start_time.value || null,
    lunch_end_time: form.elements.lunch_end_time.value || null
  };
  if (currentUser.role === "super_admin") payload.branch_id = selectedSettingsBranch();
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const data = await api("/admin/settings",{method:"PATCH",body:JSON.stringify(payload)});
    $("settingsMessage").style.color = "#4e8b69";
    $("settingsMessage").textContent = data.message;
    await loadSettings();
  } catch (error) { $("settingsMessage").style.color = "#b45d54"; $("settingsMessage").textContent = error.message; }
  finally { button.disabled = false; }
});
$("requestTypeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {label:form.elements.label.value.trim(),duration_minutes:Number(form.elements.duration_minutes.value)};
  if (currentUser.role === "super_admin") payload.branch_id = selectedSettingsBranch();
  try {
    const data = await api("/admin/request-types",{method:"POST",body:JSON.stringify(payload)});
    $("requestTypeMessage").style.color = "#4e8b69";
    $("requestTypeMessage").textContent = data.message;
    form.reset(); form.elements.duration_minutes.value = 10;
    await loadSettings();
  } catch (error) { $("requestTypeMessage").style.color = "#b45d54"; $("requestTypeMessage").textContent = error.message; }
});
$("requestTypesAdmin").addEventListener("click", async (event) => {
  const save = event.target.closest("[data-save-type]");
  const toggle = event.target.closest("[data-toggle-type]");
  if (!save && !toggle) return;
  const id = (save || toggle).dataset.saveType || (save || toggle).dataset.toggleType;
  const payload = {};
  if (save) payload.duration_minutes = Number(document.querySelector('[data-type-duration="' + id + '"]').value);
  if (toggle) payload.is_active = toggle.dataset.active !== "true";
  if (currentUser.role === "super_admin") payload.branch_id = selectedSettingsBranch();
  try {
    await api("/admin/request-types/" + id,{method:"PATCH",body:JSON.stringify(payload)});
    flash("تم تحديث نوع الطلب.");
    await loadSettings();
  } catch (error) { flash(error.message,true); }
});
async function focusPortalRecord(selector) {
  window.setTimeout(() => {
    const target = document.querySelector(selector);
    if (!target) return;
    target.classList.add("focused-record");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => target.classList.remove("focused-record"), 5000);
  }, 180);
}
async function openEmployeeNotification(notification) {
  if (!notification) return;
  const type = notification.notification_type || "";
  const requestId = Number(notification.request_id || 0);
  const employeeId = Number(notification.employee_id || 0);
  if (type === "new_request" && requestId && currentUser && currentUser.role !== "employee") {
    $("requestFilter").value = "all";
    await changePage("manageRequests");
    await loadManagerRequests("all");
    focusPortalRecord(`#managerRequests [data-request-id="${requestId}"]`);
    return;
  }
  if ((type === "request_approved" || type === "request_rejected") && requestId && currentUser?.role === "employee") {
    await changePage("requests");
    focusPortalRecord(`#myRequests [data-request-id="${requestId}"]`);
    return;
  }
  if (type === "employee_call" && employeeId && currentUser && currentUser.role !== "employee") {
    await changePage("employees");
    await loadEmployees();
    focusPortalRecord(`#employeesTable [data-employee-id="${employeeId}"]`);
    return;
  }
  const card = document.querySelector(`[data-notification-id="${Number(notification.id)}"]`);
  if (card) {
    const details = card.querySelector("details");
    if (details) details.open = true;
    card.classList.remove("unread");
    const action = card.querySelector("[data-notification-open]");
    if (action) action.textContent = "تم فتح الإشعار ✓";
  } else {
    await changePage("notifications");
    await loadNotifications();
    const target = document.querySelector(`[data-notification-id="${Number(notification.id)}"] details`);
    if (target) target.open = true;
  }
}
window.openEmployeeNotification = openEmployeeNotification;
$("notificationList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-notification-open]");
  if (!button) return;
  const id = Number(button.dataset.notificationOpen);
  try {
    const row = (await api("/notifications")).notifications?.find((item) => Number(item.id) === id);
    if (!row) throw new Error("هذا الإشعار لم يعد متاحًا.");
    if (!row.read_at) await api("/notifications/" + id + "/read",{method:"POST",body:"{}"});
    await loadNotifications();
    await openEmployeeNotification(row);
  } catch (error) { flash(error.message,true); }
});
$("myRequests").addEventListener("click", (event) => {
  const row = event.target.closest("[data-request-id]");
  if (row) focusPortalRecord(`#myRequests [data-request-id="${Number(row.dataset.requestId)}"]`);
});
$("myRequests").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("[data-request-id]");
  if (row) { event.preventDefault(); focusPortalRecord(`#myRequests [data-request-id="${Number(row.dataset.requestId)}"]`); }
});
navigator.serviceWorker?.addEventListener("message", async (event) => {
  if (event.data?.type === "OPEN_NOTIFICATION" && window.openEmployeeNotification) {
    const rows = (await api("/notifications")).notifications || [];
    const item = rows.find((row) => Number(row.id) === Number(event.data.notificationId)) || { id:event.data.notificationId, notification_type:event.data.notificationType, request_id:event.data.requestId, employee_id:event.data.employeeId };
    if (item.id && !item.read_at) await api("/notifications/" + item.id + "/read", {method:"POST",body:"{}"});
    await loadNotifications();
    await changePage("notifications");
    await openEmployeeNotification(item);
  }
});
$("copyTemporaryPassword").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("temporaryPasswordResult").textContent); $("copyTemporaryPassword").firstChild.textContent = "تم النسخ "; }
  catch { flash("تعذر النسخ التلقائي. اضغط مطولًا على كلمة المرور لنسخها.",true); }
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
window.setInterval(() => {
  if (!currentUser || document.hidden) return;
  loadNotifications();
  const activePage = document.querySelector(".page-section.active")?.id;
  if (currentUser.role === "employee" && activePage === "page-home") loadEmployeeDashboard();
  if (currentUser.role !== "employee" && activePage === "page-home") loadAdminDashboard();
  if (currentUser.role !== "employee" && activePage === "page-manageRequests") loadManagerRequests($("requestFilter").value);
}, 20000);
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
loadSession();
