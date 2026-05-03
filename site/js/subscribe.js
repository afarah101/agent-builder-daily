function submitEmail(e) {
  if (e && e.preventDefault) e.preventDefault();
  
  const container = document.querySelector('.email-inner');
  const input = container.querySelector('.email-input');
  const btn = container.querySelector('.email-btn');
  const confirm = document.getElementById('email-confirm');
  const emailValue = input.value.trim();
  
  if (!emailValue) return;

  // 1. Immediately update UI to prevent false error reports
  btn.disabled = true;
  btn.textContent = 'Subscribed! ✅';
  input.value = '';
  input.disabled = true;
  if (confirm) confirm.textContent = '';

  // 2. Fire-and-forget fetch
  const url = 'https://script.google.com/macros/s/AKfycbydhgDM0-jFaYVo9g4Wjch0ABlHlTk4b5ZVgCZS1ozVryJXd5-Xggyp4EOhBOsJIh0rzw/exec';
  
  fetch(url + '?email=' + encodeURIComponent(emailValue), { 
    mode: 'no-cors',
    method: 'GET'
  });
}
