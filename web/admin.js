/* Additive manager tools. All mutations are sent to the authenticated same-origin API. */
(() => {
  const $ = (id) => document.getElementById(id);
  const csrf = () => decodeURIComponent(document.cookie.split("; ").find(x => x.startsWith("employee_csrf="))?.split("=")[1] || "");
  const api = async (path, method="GET", body) => {
    const response = await fetch("/api" + path, {
      method, credentials:"same-origin",
      headers: {"Content-Type":"application/json", ...(method==="GET"?{}:{"X-CSRF-Token":csrf()})},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    const data = await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(data.message || "تعذر تنفيذ العملية.");
    return data;
  };
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const feedback = (id,msg,bad=false) => { const el=$(id); if(el){el.textContent=msg;el.style.color=bad?"#b45d54":"#4e8b69";} };
  const activeBranch = () => $("settingsBranchSelector")?.value || "";
  let soundEnabled = localStorage.getItem("employee-sound-enabled") === "true", knownNotifications = new Set(), audioContext = null;

  function ensureTools() {
    const nav=$("sideNav"), panel=document.querySelector(".main-panel");
    if(!nav || !panel || $("page-adminTools")) return;
    const item=document.createElement("button");
    item.type="button"; item.className="nav-item admin-only"; item.dataset.page="adminTools";
    item.innerHTML="<span>✦</span> الإدارة المتقدمة";
    nav.appendChild(item);
    const section=document.createElement("section");
    section.className="page-section"; section.id="page-adminTools";
    section.innerHTML=`
      <div class="page-heading"><div><div class="eyebrow">التحكم الإداري</div><h1>الإدارة المتقدمة</h1><p>التعاميم والاجتماعات وإدارة حالة الموظفين والفروع.</p></div></div>
      <div class="content-card">
        <div class="card-title-row"><div><span class="eyebrow">تواصل فوري</span><h2>إرسال تعميم أو تنبيه</h2></div></div>
        <form id="announcementForm" class="settings-grid">
          <label>عنوان التنبيه<input name="title" maxlength="160" required placeholder="تنبيه إداري مهم"></label>
          <label>الفرع<select name="branch_id" id="announcementBranch"></select></label>
          <label>موظف محدد (اختياري)<input name="employee_id" type="number" min="1" placeholder="اتركه فارغًا لإرسال التعميم للفرع"></label>
          <label style="grid-column:1/-1">نص الرسالة<textarea name="body" rows="4" maxlength="5000" required placeholder="اكتب تفاصيل التنبيه..."></textarea></label>
          <div class="settings-actions"><button class="primary-button compact" type="submit">إرسال الآن</button><span id="announcementMessage" class="form-message"></span></div>
        </form>
      </div>
      <div class="content-card">
        <div class="card-title-row"><div><span class="eyebrow">تنظيم العمل</span><h2>جدولة اجتماع</h2></div></div>
        <form id="meetingForm" class="settings-grid">
          <label>عنوان الاجتماع<input name="title" maxlength="160" required></label>
          <label>الفرع<select name="branch_id" id="meetingBranch"></select></label>
          <label>الموعد (بتوقيت جهازك)<input name="starts_at" type="datetime-local" required></label>
          <label>المكان أو رابط الاجتماع<input name="location" maxlength="180" placeholder="قاعة الاجتماعات / رابط"></label>
          <label style="grid-column:1/-1">جدول الأعمال<textarea name="agenda" rows="3" maxlength="5000"></textarea></label>
          <div class="settings-actions"><button class="primary-button compact" type="submit">إنشاء الاجتماع وإشعار الموظفين</button><span id="meetingMessage" class="form-message"></span></div>
        </form>
        <div id="meetingList" class="manager-request-list"></div>
      </div>
      <div class="content-card">
        <div class="card-title-row"><div><span class="eyebrow">تخصيص الفرع</span><h2>تعديل اسم الفرع وحالته</h2></div></div>
        <form id="branchEditForm" class="settings-grid">
          <label>الفرع<select name="branch_id" id="editBranch"></select></label>
          <label>الاسم الجديد<input name="name" maxlength="120" required></label>
          <label class="setting-toggle"><input name="is_active" type="checkbox" checked><span><strong>الفرع نشط</strong><small>إلغاء التفعيل يمنع العمليات الجديدة في الفرع</small></span></label>
          <div class="settings-actions"><button class="primary-button compact" type="submit">حفظ بيانات الفرع</button><span id="branchEditMessage" class="form-message"></span></div>
        </form>
      </div>
      <div class="content-card">
        <div class="card-title-row"><div><span class="eyebrow">التنبيهات الصوتية</span><h2>الصوت وإشعارات المتصفح</h2></div></div>
        <p class="card-subtitle">فعّل الصوت بعد تفاعل مباشر مع الصفحة. يعمل التنبيه الصوتي أثناء فتح البوابة؛ التنبيهات في الخلفية تعتمد على دعم المتصفح وإعدادات الجهاز.</p>
        <div class="request-actions"><button class="primary-button compact" id="soundToggle" type="button">تفعيل صوت التنبيه</button><button class="soft-button" id="browserNotify" type="button">السماح بإشعارات المتصفح</button><span id="soundMessage" class="form-message"></span></div>
      </div>`;
    panel.appendChild(section);
    item.addEventListener("click",()=>changePage("adminTools"));
    $("announcementForm").addEventListener("submit", async e=>{
      e.preventDefault(); const f=new FormData(e.currentTarget);
      const payload={title:f.get("title"),body:f.get("body"),branch_id:f.get("branch_id")||undefined,employee_id:f.get("employee_id")?Number(f.get("employee_id")):undefined};
      try{const r=await api("/admin/announcements","POST",payload);feedback("announcementMessage",r.message);e.currentTarget.reset();}
      catch(err){feedback("announcementMessage",err.message,true);}
    });
    $("meetingForm").addEventListener("submit",async e=>{
      e.preventDefault();const f=new FormData(e.currentTarget);
      const payload={title:f.get("title"),agenda:f.get("agenda"),location:f.get("location"),starts_at:new Date(f.get("starts_at")).toISOString(),branch_id:f.get("branch_id")||undefined};
      try{const r=await api("/admin/meetings","POST",payload);feedback("meetingMessage",r.message);e.currentTarget.reset();await loadMeetings();}
      catch(err){feedback("meetingMessage",err.message,true);}
    });
    $("branchEditForm").addEventListener("submit",async e=>{
      e.preventDefault();const f=new FormData(e.currentTarget);const id=f.get("branch_id");
      try{const r=await api("/admin/branches/"+id,"PATCH",{name:f.get("name"),is_active:f.get("is_active")==="on"});feedback("branchEditMessage",r.message);await loadBranches();}
      catch(err){feedback("branchEditMessage",err.message,true);}
    });
    $("editBranch").addEventListener("change",fillBranchEdit);
    $("soundToggle").addEventListener("click",()=>{
      soundEnabled=!soundEnabled;
      localStorage.setItem("employee-sound-enabled",String(soundEnabled));
      if(soundEnabled){const C=window.AudioContext||window.webkitAudioContext;if(C){audioContext ||= new C();audioContext.resume();beep();}}
      $("soundToggle").textContent=soundEnabled?"إيقاف صوت التنبيه":"تفعيل صوت التنبيه";
      feedback("soundMessage",soundEnabled?"تم تفعيل الصوت أثناء فتح البوابة.":"تم إيقاف الصوت.");
    });
    $("browserNotify").addEventListener("click",async()=>{
      if(!("Notification" in window)){feedback("soundMessage","هذا المتصفح لا يدعم إشعارات النظام.",true);return;}
      const permission=await Notification.requestPermission();
      feedback("soundMessage",permission==="granted"?"تم السماح بإشعارات المتصفح.":"لم يتم منح إذن الإشعارات.",permission!=="granted");
    });
    $("meetingList").addEventListener("click",async e=>{
      const btn=e.target.closest("[data-meeting-cancel]");if(!btn)return;
      try{await api("/admin/meetings/"+btn.dataset.meetingCancel,"PATCH",{status:"cancelled"});await loadMeetings();}
      catch(err){feedback("meetingMessage",err.message,true);}
    });
    $("employeesTable")?.addEventListener("click",async e=>{
      const btn=e.target.closest("[data-employee-status]");if(!btn)return;
      try{const active=btn.dataset.active!=="true";const r=await api("/admin/employees/"+btn.dataset.employeeStatus+"/status","PATCH",{is_active:active});btn.dataset.active=String(active);btn.textContent=active?"حظر الموظف":"إعادة التفعيل";btn.classList.toggle("danger-button",!active);flash(r.message);await loadEmployeesForStatus();}
      catch(err){flash(err.message,true);}
    });
    const activityNav=document.createElement("button");
    activityNav.type="button";activityNav.className="nav-item admin-only";activityNav.dataset.page="employeeActivity";
    activityNav.innerHTML="<span>◉</span> حركة الموظفين";
    nav.appendChild(activityNav);
    const activityPage=document.createElement("section");
    activityPage.className="page-section";activityPage.id="page-employeeActivity";
    activityPage.innerHTML=`
      <div class="page-heading"><div><div class="eyebrow">المتابعة المباشرة</div><h1>حركة الموظفين</h1><p>حالة الاتصال وآخر صفحة نشطة وسجل تغيّر الحالة داخل فرعك. لا يتم جمع الموقع الجغرافي.</p></div><button class="soft-button" id="activityRefresh" type="button">تحديث</button></div>
      <div class="content-card"><div class="card-title-row"><div><span class="eyebrow">نطاق المتابعة</span><h2>الموظفون وحالة الاتصال</h2></div><span class="live-pill"><i></i> تحديث تلقائي</span></div><label id="activityBranchWrap">الفرع<select id="activityBranch"></select></label><div id="employeePresenceList" class="manager-request-list"><div class="empty-cell">جارٍ تحميل حركة الموظفين...</div></div></div>
      <div class="content-card"><div class="card-title-row"><div><span class="eyebrow">سجل الحركة</span><h2>آخر تغيّرات الحالة</h2></div></div><div id="employeeActivityEvents" class="manager-request-list"><div class="empty-cell">لا توجد أحداث بعد.</div></div></div>`;
    panel.appendChild(activityPage);
    $("activityRefresh").addEventListener("click",loadEmployeeActivity);
    $("activityBranch").addEventListener("change",loadEmployeeActivity);
    if(window.currentUser?.role!=="super_admin")$("activityBranchWrap").hidden=true;
    loadBranches();
    loadMeetings();
    enhanceEmployeeRows();
    new MutationObserver(enhanceEmployeeRows).observe($("employeesTable"),{childList:true,subtree:true});
  }
  function beep(){
    if(!soundEnabled||!audioContext)return;
    const osc=audioContext.createOscillator(),gain=audioContext.createGain();
    osc.type="sine";osc.frequency.value=880;gain.gain.value=0.055;
    osc.connect(gain);gain.connect(audioContext.destination);osc.start();
    gain.gain.exponentialRampToValueAtTime(0.001,audioContext.currentTime+0.35);osc.stop(audioContext.currentTime+0.36);
  }
  async function loadBranches(){
    try{
      const data=await api("/admin/branches");
      for(const id of ["announcementBranch","meetingBranch","editBranch","activityBranch"]){
        const el=$(id);if(!el)continue;const previous=el.value;
        el.innerHTML=(data.branches||[]).map(b=>`<option value="${b.id}">${esc(b.name)}${b.is_active?"":" (موقوف)"}</option>`).join("");
        if(previous)el.value=previous;
      }
      fillBranchEdit();
    }catch(err){feedback("branchEditMessage",err.message,true);}
  }
  function fillBranchEdit(){
    const select=$("editBranch"), opt=select?.selectedOptions?.[0];
    if(!opt)return;
    $("branchEditForm").elements.name.value=opt.textContent.replace(/ \(موقوف\)$/,"");
    $("branchEditForm").elements.is_active.checked=!opt.textContent.endsWith("(موقوف)");
  }
  async function loadMeetings(){
    try{
      const data=await api("/admin/meetings"+(activeBranch()?"?branch_id="+encodeURIComponent(activeBranch()):""));
      $("meetingList").innerHTML=(data.meetings||[]).map(m=>`<article class="manager-request"><div><strong>${esc(m.title)}</strong><p>${esc(m.branch_name)} · ${new Date(m.starts_at).toLocaleString("ar-JO")}</p><time>${esc(m.location||"دون مكان محدد")} · ${esc(m.status)}</time></div>${m.status==="scheduled"?`<button class="mini-button danger-button" data-meeting-cancel="${m.id}">إلغاء الاجتماع</button>`:""}</article>`).join("")||'<div class="empty-cell">لا توجد اجتماعات مجدولة.</div>';
    }catch(err){$("meetingList").textContent=err.message;}
  }
  async function loadEmployeesForStatus(){
    const rows=document.querySelectorAll("#employeesTable tr");
    rows.forEach(row=>{const button=row.querySelector("[data-employee-status]");if(button){const active=button.dataset.active==="true";button.textContent=active?"حظر الموظف":"إعادة التفعيل";}});
  }
  function enhanceEmployeeRows(){
    document.querySelectorAll("#employeesTable tr").forEach(row=>{
      const cells=row.querySelectorAll("td");if(cells.length<5||row.querySelector("[data-employee-status]"))return;
      const code=cells[1]?.textContent?.trim(), name=cells[0]?.textContent?.trim();
      const action=cells[4];if(!action||!code)return;
      const idMatch=action.innerHTML.match(/data-(?:reset-password|activation)="(\d+)"/);
      const employeeId=idMatch?.[1];
      if(!employeeId)return;
      const active=!cells[3].textContent.includes("موقوف");
      const btn=document.createElement("button");btn.type="button";btn.className="mini-button "+(active?"danger-button":"");
      btn.dataset.employeeStatus=employeeId;btn.dataset.active=String(active);btn.textContent=active?"حظر الموظف":"إعادة التفعيل";
      action.appendChild(btn);
    });
  }
  async function loadEmployeeActivity(){
    if(!$("employeePresenceList"))return;
    try{
      const branch=$("activityBranch")?.value||"";
      const data=await api("/admin/employee-activity"+(branch?"?branch_id="+encodeURIComponent(branch):""));
      const status={online:"متصل الآن",idle:"نشط مؤخرًا",offline:"غير متصل"};
      const pages={dashboard:"الرئيسية",requests:"الطلبات",notifications:"الإشعارات",workTasks:"المهام",profile:"الملف الشخصي",settings:"الإعدادات"};
      $("employeePresenceList").innerHTML=(data.employees||[]).map(e=>{
        const color=e.presence_status==="online"?"#2f8b5b":e.presence_status==="idle"?"#bd8b35":"#89948d";
        const last=e.last_seen_at?new Date(e.last_seen_at).toLocaleString("ar-JO"):"لم يسجل دخولًا بعد";
        return `<article class="manager-request"><div><strong>${esc(e.full_name)}</strong><p>${esc(e.employee_code||"")} · ${esc(e.job_title||"")}</p><time>آخر ظهور: ${esc(last)} · الصفحة: ${esc(pages[e.current_page]||e.current_page||"—")}</time></div><span class="status-pill" style="color:${color}">${status[e.presence_status]||"غير متصل"}</span></article>`;
      }).join("")||'<div class="empty-cell">لا يوجد موظفون نشطون في هذا النطاق.</div>';
      const labels={online:"بدأ الاتصال",resumed:"عاد إلى النظام",tab_visible:"فتح صفحة النظام",tab_hidden:"أخفى صفحة النظام",page_changed:"تنقّل داخل النظام",employee_request_created:"قدّم طلب استئذان أو استراحة",employee_return_confirmed:"سجّل العودة إلى العمل",supervisor_called:"استدعى المشرف"};
      $("employeeActivityEvents").innerHTML=(data.events||[]).map(e=>`<article class="manager-request"><div><strong>${esc(e.full_name)}</strong><p>${esc(labels[e.event_type]||e.event_type)} · ${esc(e.details?.page||e.details?.request_type||e.details?.to_status||"")}</p><time>${esc(new Date(e.created_at).toLocaleString("ar-JO"))}</time></div></article>`).join("")||'<div class="empty-cell">لا توجد تغيّرات مسجلة بعد.</div>';
    }catch(err){$("employeePresenceList").textContent=err.message;}
  }
  document.addEventListener("click",e=>{
    if(e.target.closest('[data-page="employeeActivity"]'))window.setTimeout(loadEmployeeActivity,120);
  });
  window.setInterval(()=>{if($("page-employeeActivity")?.classList.contains("active"))loadEmployeeActivity();},20000);
  document.addEventListener("click",e=>{
    const nav=e.target.closest('[data-page="employees"]');
    if(nav)window.setTimeout(enhanceEmployeeRows,250);
    const tools=e.target.closest('[data-page="adminTools"]');
    if(tools)window.setTimeout(()=>{loadBranches();loadMeetings();},100);
  });
  const originalFetch=window.fetch.bind(window);
  // Observe notification poll responses without changing the core API client.
  window.fetch=async (...args)=>{
    const response=await originalFetch(...args);
    try{
      const url=typeof args[0]==="string"?args[0]:args[0]?.url;
      if(url?.includes("/api/notifications")&&response.ok){
        const data=await response.clone().json();
        for(const n of data.notifications||[]){
          if(!knownNotifications.has(n.id)){
            if(knownNotifications.size&&soundEnabled)beep();
            if(knownNotifications.size&&"Notification" in window&&Notification.permission==="granted"&&document.hidden){
              new Notification(n.title,{body:n.body,icon:"/icons/icon.svg"});
            }
            knownNotifications.add(n.id);
          }
        }
      }
    }catch(_){}
    return response;
  };
  document.addEventListener("DOMContentLoaded",ensureTools);
  const ready=setInterval(()=>{if($("sideNav")&&$("pageFeedback")){ensureTools();clearInterval(ready);}},250);
})();

/* Follow-up controls: persistent branding, status actions, and due-meeting reminder polling. */
(() => {
  const csrfToken=()=>decodeURIComponent(document.cookie.split("; ").find(x=>x.startsWith("employee_csrf="))?.split("=")[1]||"");
  const call=async(path,method="GET",body)=>{const r=await fetch("/api"+path,{method,credentials:"same-origin",headers:{"Content-Type":"application/json",...(method==="GET"?{}:{"X-CSRF-Token":csrfToken()})},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.message||"تعذر تنفيذ العملية");return d;};
  const esc2=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  let employees=new Map(),loading=false;
  async function installGlobalSettings(){
    const page=document.getElementById("page-adminTools");if(!page||document.getElementById("siteSettingsExtra"))return;
    const card=document.createElement("section");card.className="content-card";card.id="siteSettingsExtra";
    card.innerHTML='<div class="card-title-row"><div><span class="eyebrow">هوية النظام</span><h2>إعدادات الموقع العامة</h2></div></div><form id="siteSettingsExtraForm" class="settings-grid"><label>اسم النظام<input name="site_name" maxlength="80" required></label><label>الوصف المختصر<input name="site_tagline" maxlength="120" required></label><label style="grid-column:1/-1">رسالة المساعدة<input name="support_message" maxlength="240"></label><label style="grid-column:1/-1">إعلان عام<input name="maintenance_notice" maxlength="300"></label><div class="settings-actions"><button class="primary-button compact" type="submit">حفظ إعدادات الموقع</button><span id="siteSettingsExtraMessage" class="form-message"></span></div></form>';
    page.appendChild(card);
    if(document.getElementById("addBranchButton")?.hidden){card.hidden=true;return;}
    try{const data=await call("/admin/site-settings");const form=document.getElementById("siteSettingsExtraForm");for(const [k,v] of Object.entries(data.settings||{})){if(form.elements[k])form.elements[k].value=v;}apply(data.settings||{});}
    catch(e){document.getElementById("siteSettingsExtraMessage").textContent=e.message;}
    document.getElementById("siteSettingsExtraForm").addEventListener("submit",async e=>{
      e.preventDefault();const f=new FormData(e.currentTarget),payload=Object.fromEntries(f.entries());
      try{const r=await call("/admin/site-settings","PATCH",payload);document.getElementById("siteSettingsExtraMessage").textContent=r.message;apply(payload);}
      catch(err){document.getElementById("siteSettingsExtraMessage").textContent=err.message;}
    });
  }
  function apply(s){
    const name=s.site_name||"عمران مول | Emran Mall",tag=s.site_tagline||"الفرع الرئيسي – خريبة السوق";
    const brand=document.querySelector(".brand-copy strong"),sub=document.querySelector(".brand-copy small");
    if(brand)brand.textContent=name;if(sub)sub.textContent=tag;document.title=name+" | بوابة الموظفين";
  }
  async function enhanceEmployees(){
    if(loading)return;loading=true;
    try{const d=await call("/admin/employees");employees=new Map((d.employees||[]).map(e=>[e.employee_code,e]));}
    catch(_){}finally{loading=false;}
    document.querySelectorAll("#employeesTable tr").forEach(row=>{
      const cells=row.querySelectorAll("td");if(cells.length<5)return;
      const emp=employees.get(cells[1].textContent.trim()),actions=cells[4];if(!emp||!actions)return;
      let button=actions.querySelector("[data-employee-status]");
      if(!button){button=document.createElement("button");button.type="button";button.className="mini-button";actions.appendChild(button);}
      button.dataset.employeeStatus=emp.id;button.dataset.active=String(emp.is_active);
      button.textContent=emp.is_active?"حظر الموظف":"إعادة التفعيل";
      button.classList.toggle("danger-button",Boolean(emp.is_active));
    });
  }
  document.addEventListener("click",e=>{
    if(e.target.closest('[data-page="adminTools"]'))setTimeout(installGlobalSettings,150);
    if(e.target.closest('[data-page="employees"]'))setTimeout(enhanceEmployees,250);
  });
  document.getElementById("employeesTable")&&new MutationObserver(()=>enhanceEmployees()).observe(document.getElementById("employeesTable"),{childList:true,subtree:true});
  setInterval(()=>{if(!document.hidden)call("/system/process-meeting-reminders").catch(()=>{});},20000);
  const ready=setInterval(()=>{if(document.getElementById("page-adminTools")){installGlobalSettings();clearInterval(ready);}},300);
})();

/* Employee-wide notification preferences: foreground tone + optional system notification. */
(() => {
  let enabled=localStorage.getItem("employee-sound-enabled")==="true";
  let audio=null, seen=new Set(), initialized=false;
  const tone=()=>{
    enabled=localStorage.getItem("employee-sound-enabled")==="true";
    if(!enabled)return;
    const C=window.AudioContext||window.webkitAudioContext;if(!C)return;
    audio ||= new C();audio.resume();
    [880,660].forEach((frequency,index)=>{
      const osc=audio.createOscillator(),gain=audio.createGain();
      osc.type="sine";osc.frequency.value=frequency;gain.gain.value=0.045;
      osc.connect(gain);gain.connect(audio.destination);const start=audio.currentTime+index*.17;
      osc.start(start);gain.gain.setValueAtTime(0.045,start);
      gain.gain.exponentialRampToValueAtTime(0.001,start+.14);osc.stop(start+.15);
    });
  };
  function addControl(){
    const sidebarBottom=document.querySelector(".sidebar-bottom");if(!sidebarBottom)return;
    let actions=document.getElementById("notificationPreferences");
    if(!actions){actions=document.createElement("div");actions.id="notificationPreferences";actions.className="notification-preferences";actions.setAttribute("aria-label","إعدادات التنبيهات");sidebarBottom.insertBefore(actions,sidebarBottom.firstChild);}
    if(document.getElementById("employeeSoundButton")&&document.getElementById("employeeBrowserNotify"))return;
    if(!document.getElementById("employeeSoundButton")){
      const b=document.createElement("button");b.id="employeeSoundButton";b.type="button";b.className="icon-button";
      b.title="تشغيل أو إيقاف صوت التنبيهات";b.setAttribute("aria-label",b.title);b.textContent=enabled?"♫":"♩";
      b.addEventListener("click",()=>{enabled=!enabled;localStorage.setItem("employee-sound-enabled",String(enabled));b.textContent=enabled?"♫":"♩";if(enabled)tone();});
      actions.appendChild(b);
    }
    if(!document.getElementById("employeeBrowserNotify")){
      const n=document.createElement("button");n.id="employeeBrowserNotify";n.type="button";n.className="icon-button";
      n.title="السماح بإشعارات الجهاز";n.setAttribute("aria-label",n.title);n.textContent="♧";
      n.addEventListener("click",async()=>{if("Notification" in window){const p=await Notification.requestPermission();n.title=p==="granted"?"إشعارات الجهاز مفعّلة":"لم يتم السماح بالإشعارات";}else n.title="المتصفح لا يدعم إشعارات الجهاز";});
      actions.appendChild(n);
    }
  }
  async function poll(){
    // Session cookie is HttpOnly; the authenticated API validates it.
    try{
      const r=await fetch("/api/notifications",{credentials:"same-origin",cache:"no-store"});
      if(!r.ok)return;const d=await r.json();
      for(const item of d.notifications||[]){
        if(!seen.has(item.id)){
          if(initialized&&!item.read_at){tone();if(document.hidden&&"Notification" in window&&Notification.permission==="granted"){new Notification(item.title,{body:item.body,icon:"/icons/icon.svg",tag:"employee-"+item.id,renotify:false});}}
          seen.add(item.id);
        }
      }
      initialized=true;
    }catch(_){}
  }
  const ready=setInterval(()=>{addControl();if(document.getElementById("employeeSoundButton"))clearInterval(ready);},300);
  async function applyPublicSiteInfo(){
    try{
      const response=await fetch("/api/site-info",{credentials:"same-origin",cache:"no-store"});
      if(!response.ok)return;
      const data=await response.json(),s=data.settings||{};
      const brand=document.querySelector(".brand-copy strong"),sub=document.querySelector(".brand-copy small");
      if(brand)brand.textContent=s.site_name||"رفيق العمل";
      if(sub)sub.textContent=s.site_tagline||"منظومة إدارة الموظفين";
      document.title=(s.site_name||"رفيق العمل")+" | بوابة الموظفين";
      const help=document.querySelector(".help-mini small");
      if(help&&s.support_message)help.textContent=s.support_message;
      const old=document.getElementById("globalSiteNotice");if(old)old.remove();
      if(s.maintenance_notice){
        const banner=document.createElement("div");banner.id="globalSiteNotice";
        banner.className="content-card";banner.textContent=s.maintenance_notice;
        document.querySelector(".main-panel")?.prepend(banner);
      }
    }catch(_){}
  }
  applyPublicSiteInfo();
  setInterval(applyPublicSiteInfo,60000);

})();
