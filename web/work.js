/* Employee work tracking UI: branch-scoped tasks, group assignment and auditable status updates. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const csrf = () => decodeURIComponent(document.cookie.split("; ").find(x => x.startsWith("employee_csrf="))?.split("=")[1] || "");
  const api = async (path, method = "GET", body) => {
    const response = await fetch("/api" + path, {
      method, credentials: "same-origin",
      headers: {"Content-Type":"application/json", ...(method === "GET" ? {} : {"X-CSRF-Token":csrf()})},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || "تعذر تنفيذ العملية.");
    return data;
  };
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const labels = {pending:"جديدة",in_progress:"قيد التنفيذ",completed:"مكتملة",blocked:"متعذرة",cancelled:"ملغاة"};
  const priorities = {low:"منخفضة",normal:"عادية",high:"عالية",urgent:"عاجلة"};
  let ready = false, busy = false, employeeOptions = [];

  function install() {
    if (!$("sideNav") || !document.querySelector(".main-panel") || $("page-workTasks")) return;
    const nav = document.createElement("button");
    nav.type = "button"; nav.className = "nav-item"; nav.dataset.page = "workTasks";
    nav.innerHTML = "<span>✓</span> المهام ومتابعة العمل";
    $("sideNav").appendChild(nav);
    const page = document.createElement("section");
    page.className = "page-section"; page.id = "page-workTasks";
    page.innerHTML = `
      <div class="page-heading"><div><div class="eyebrow">تنظيم ومتابعة العمل</div><h1 id="workTasksTitle">مهامي</h1><p>متابعة التكليفات وتحديث حالة الإنجاز بشكل موثق.</p></div><button class="soft-button" id="workTasksRefresh" type="button">تحديث</button></div>
      <section class="content-card" id="workTaskCreateCard" hidden>
        <div class="card-title-row"><div><span class="eyebrow">توزيع المسؤوليات</span><h2>إنشاء مهمة وإسنادها</h2></div></div>
        <form id="workTaskForm" class="settings-grid">
          <label>عنوان المهمة<input name="title" maxlength="160" required placeholder="مثال: ترتيب قسم المنظفات"></label>
          <label>الأولوية<select name="priority"><option value="normal">عادية</option><option value="low">منخفضة</option><option value="high">عالية</option><option value="urgent">عاجلة</option></select></label>
          <label>موعد الإنجاز<input name="due_at" type="datetime-local"></label>
          <label id="workTaskBranchWrap" hidden>الفرع<select name="branch_id" id="workTaskBranch"></select></label>
          <label style="grid-column:1/-1">تفاصيل المهمة<textarea name="description" rows="3" maxlength="4000" placeholder="اكتب المطلوب ومعايير الإنجاز..."></textarea></label>
          <label style="grid-column:1/-1">إسناد إلى موظف أو أكثر<select id="workTaskEmployees" multiple required size="5" style="min-height:130px"></select><small>يمكن تحديد أكثر من موظف باستخدام الاختيار المتعدد.</small></label>
          <div class="settings-actions"><button class="primary-button compact" type="submit">إنشاء المهمة وإرسال التنبيه</button><span class="form-message" id="workTaskCreateMessage"></span></div>
        </form>
      </section>
      <section class="content-card">
        <div class="card-title-row"><div><span class="eyebrow">سجل التكليفات</span><h2>المهام المسندة</h2></div><span class="live-pill"><i></i> تتحدث تلقائياً</span></div>
        <div id="workTaskList" class="manager-request-list"><div class="empty-cell">جارٍ تحميل المهام...</div></div>
      </section>`;
    document.querySelector(".main-panel").appendChild(page);
    nav.addEventListener("click", () => loadTasks());
    $("workTasksRefresh").addEventListener("click", loadTasks);
    $("workTaskForm").addEventListener("submit", createTask);
    $("workTaskList").addEventListener("click", handleTaskAction);
    $("workTaskBranch")?.addEventListener("change", () => { loadEmployees(); loadTasks(); });
    ready = true;
    configureRole();
  }

  async function configureRole() {
    if (!ready || !currentUser) return;
    const user = currentUser;
    const manager = user.role !== "employee";
    $("workTasksTitle").textContent = manager ? "متابعة مهام الموظفين" : "مهامي";
    $("workTaskCreateCard").hidden = !manager;
    if (!manager) return;
    if (user.role === "super_admin") {
      $("workTaskBranchWrap").hidden = false;
      try {
        const data = await api("/admin/branches");
        $("workTaskBranch").innerHTML = (data.branches || []).filter(b => b.is_active).map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
      } catch (error) { showMessage("workTaskCreateMessage", error.message, true); }
    } else {
      $("workTaskBranchWrap").hidden = true;
    }
    await loadEmployees();
  }

  async function loadEmployees() {
    const select = $("workTaskEmployees");
    if (!select || !ready) return;
    try {
      const data = await api("/admin/employees");
      const branchId = $("workTaskBranch")?.value;
      employeeOptions = (data.employees || []).filter(e => e.is_active && (!branchId || String(e.branch_id) === String(branchId)));
      select.innerHTML = employeeOptions.map(e => `<option value="${e.id}">${esc(e.full_name)} · ${esc(e.employee_code || "")}</option>`).join("");
      if (!employeeOptions.length) select.innerHTML = '<option disabled>لا يوجد موظفون نشطون في هذا الفرع</option>';
    } catch (error) { showMessage("workTaskCreateMessage", error.message, true); }
  }

  async function createTask(event) {
    event.preventDefault();
    const form = event.currentTarget, data = new FormData(form);
    const employeeIds = Array.from($("workTaskEmployees").selectedOptions).map(option => Number(option.value));
    const payload = {
      title: data.get("title"), description: data.get("description"),
      priority: data.get("priority"), due_at: data.get("due_at") ? new Date(data.get("due_at")).toISOString() : null,
      employee_ids: employeeIds
    };
    if ($("workTaskBranch")?.value) payload.branch_id = Number($("workTaskBranch").value);
    try {
      const result = await api("/admin/tasks", "POST", payload);
      showMessage("workTaskCreateMessage", result.message);
      form.reset();
      await loadTasks();
    } catch (error) { showMessage("workTaskCreateMessage", error.message, true); }
  }

  async function loadTasks() {
    if (!ready || busy || !currentUser) return;
    busy = true;
    try {
      const branch = $("workTaskBranch")?.value;
      const data = await api("/work/tasks" + (currentUser?.role === "super_admin" && branch ? "?branch_id=" + encodeURIComponent(branch) : ""));
      const rows = data.tasks || [];
      $("workTaskList").innerHTML = rows.map(task => {
        const due = task.due_at ? new Date(task.due_at) : null;
        const overdue = due && due.getTime() < Date.now() && !["completed","cancelled"].includes(task.status);
        const manager = currentUser.role !== "employee";
        return `<article class="manager-request" data-task-card="${task.assignment_id}">
          <div style="min-width:0;flex:1">
            <strong>${esc(task.title)}</strong>
            <p>${esc(task.description || "لا توجد تفاصيل إضافية")}</p>
            <time>${manager ? esc(task.employee_name) + " · " : ""}${esc(priorities[task.priority] || task.priority)} · ${due ? "الموعد: " + esc(due.toLocaleString("ar-JO")) : "دون موعد محدد"}</time>
            <small style="display:block;margin-top:5px;color:${overdue ? "#b45d54" : "#718078"}">${overdue ? "متأخرة عن الموعد" : "الحالة: " + esc(labels[task.status] || task.status)}</small>
          </div>
          <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
            <select data-task-status="${task.assignment_id}" aria-label="حالة المهمة">
              ${(manager ? ["pending","in_progress","completed","blocked","cancelled"] : ["in_progress","completed","blocked"]).map(s => `<option value="${s}" ${s===task.status?"selected":""}>${labels[s]}</option>`).join("")}
            </select>
            <button type="button" class="mini-button" data-task-save="${task.assignment_id}">حفظ</button>
            <button type="button" class="mini-button" data-task-history="${task.assignment_id}">السجل</button>
          </div>
          <div data-task-history-box="${task.assignment_id}" hidden style="grid-column:1/-1"></div>
        </article>`;
      }).join("") || '<div class="empty-cell">لا توجد مهام مسندة حالياً.</div>';
    } catch (error) {
      $("workTaskList").textContent = error.message;
    } finally { busy = false; }
  }

  async function handleTaskAction(event) {
    const save = event.target.closest("[data-task-save]");
    if (save) {
      const id = save.dataset.taskSave, status = document.querySelector(`[data-task-status="${id}"]`)?.value;
      save.disabled = true;
      try { const result = await api("/work/tasks/" + id, "PATCH", {status}); showMessage("pageFeedback", result.message); await loadTasks(); }
      catch (error) { showMessage("pageFeedback", error.message, true); }
      finally { save.disabled = false; }
      return;
    }
    const history = event.target.closest("[data-task-history]");
    if (history) {
      const id = history.dataset.taskHistory, box = document.querySelector(`[data-task-history-box="${id}"]`);
      if (!box.hidden) { box.hidden = true; return; }
      try {
        const data = await api("/work/tasks/" + id + "/events");
        box.innerHTML = (data.events || []).map(item => `<p><strong>${esc(item.actor_name || "النظام")}</strong> · ${esc(labels[item.to_status] || item.event_type)} · ${esc(new Date(item.created_at).toLocaleString("ar-JO"))}<br><small>${esc(item.note || "")}</small></p>`).join("") || "<p>لا يوجد سجل.</p>";
        box.hidden = false;
      } catch (error) { box.textContent = error.message; box.hidden = false; }
    }
  }

  function showMessage(id, message, bad = false) {
    const node = $(id);
    if (!node) return;
    node.textContent = message;
    node.style.color = bad ? "#b45d54" : "#4e8b69";
  }

  window.addEventListener("employee-portal:opened", () => {
    install();
    configureRole().then(loadTasks);
  });
  window.addEventListener("employee-portal:refresh", () => {
    if ($("page-workTasks")?.classList.contains("active")) loadTasks();
  });
  document.addEventListener("click", event => {
    if (event.target.closest('[data-page="workTasks"]')) window.setTimeout(loadTasks, 100);
  });
  window.setInterval(() => {
    if (!document.hidden && $("page-workTasks")?.classList.contains("active")) loadTasks();
  }, 15000);
  if (document.readyState !== "loading") install();
  else document.addEventListener("DOMContentLoaded", install);
})();
