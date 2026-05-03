function submitEmail(e) {
  if (e && e.preventDefault) e.preventDefault();
  
  const container = document.querySelector('.email-inner');
  const input = container.querySelector('.email-input');
  const btn = container.querySelector('.email-btn');
  const confirm = document.getElementById('email-confirm');
  const emailValue = input.value.trim();
  
  if (!emailValue) return;

  const scriptUrl = 'https://script.google.com/macros/s/AKfycbydhgDM0-jFaYVo9g4Wjch0ABlHlTk4b5ZVgCZS1ozVryJXd5-Xggyp4EOhBOsJIh0rzw/exec';
  const finalUrl = scriptUrl + '?email=' + encodeURIComponent(emailValue);

  btn.disabled = true;
  input.disabled = true;
  confirm.textContent = '⏳...';

  fetch(finalUrl, {
    method: 'GET',
    mode: 'no-cors'
  }).then(() => {
    // In no-cors mode, the response is opaque. 
    // Reaching this block means the request was successfully dispatched.
    confirm.textContent = 'Subscribed! ✅';
    input.value = '';
  }).catch((err) => {
    console.error('Subscription network error:', err);
    confirm.textContent = '❌ Error';
    btn.disabled = false;
    input.disabled = false;
  });
}
