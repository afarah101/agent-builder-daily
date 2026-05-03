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

  fetch(url + '?email=' + encodeURIComponent(emailValue), { mode: 'no-cors' })
    .then(() => {
      btn.textContent = 'Subscribed! ✅';
      confirm.textContent = '';
      input.value = '';
    })
    .catch((err) => {
      console.error('Subscription error:', err);
      confirm.textContent = '❌ Error';
      btn.disabled = false;
      input.disabled = false;
    });
}
