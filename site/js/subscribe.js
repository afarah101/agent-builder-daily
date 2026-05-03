function submitEmail(e) {
  if (e && e.preventDefault) e.preventDefault();
  
  const container = document.querySelector('.email-inner');
  const input = container.querySelector('.email-input');
  const btn = container.querySelector('.email-btn');
  const confirm = document.getElementById('email-confirm');
  const emailValue = input.value.trim();
  
  if (!emailValue) return;

  const url = 'https://script.google.com/macros/s/AKfycbydhgDM0-jFaYVo9g4Wjch0ABlHlTk4b5ZVgCZS1ozVryJXd5-Xggyp4EOhBOsJIh0rzw/exec';

  btn.disabled = true;
  input.disabled = true;
  confirm.textContent = '⏳...';

  const params = new URLSearchParams({ email: emailValue });

  fetch(url, {
    method: 'POST',
    mode: 'no-cors',
    body: params
  }).then(() => {
    // In no-cors mode, we cannot read the response body or status.
    // If the promise resolves, we assume the server received the data.
    confirm.textContent = 'Subscribed! ✅';
    input.value = '';
  }).catch((err) => {
    console.error('Subscription error:', err);
    confirm.textContent = '❌ Error';
    btn.disabled = false;
    input.disabled = false;
  });
}
