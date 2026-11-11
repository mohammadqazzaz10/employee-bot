const form=document.getElementById("setupForm");
form.addEventListener("submit",async(event)=>{
 event.preventDefault();
 const button=document.getElementById("setupSubmit"),message=document.getElementById("setupMessage");
 button.disabled=true;message.textContent="";
 try{
  const response=await fetch("/api/setup/bootstrap",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   bootstrap_token:document.getElementById("bootstrapToken").value,
   full_name:document.getElementById("fullName").value.trim(),
   username:document.getElementById("username").value.trim(),
   password:document.getElementById("password").value
  })});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.message||"تعذرت التهيئة.");
  message.style.color="#43845e";message.textContent=data.message+" انتقل الآن إلى صفحة الدخول.";
  form.reset();
 }catch(error){message.textContent=error.message;}
 finally{button.disabled=false;}
});