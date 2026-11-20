function submitEmail(e) {
  if (e && e.preventDefault) e.preventDefault();

  const container = document.querySelector('.email-inner');
  const input = container.querySelector('.email-input');
  const btn = container.querySelector('.email-btn');
  const confirm = document.getElementById('email-confirm');
  const emailValue = input.value.trim();

  if (!emailValue) return;

  btn.disabled = true;
  input.disabled = true;
  btn.textContent = 'Submitting…';
  if (confirm) confirm.textContent = '';

  const url = 'https://script.google.com/macros/s/AKfycbydhgDM0-jFaYVo9g4Wjch0ABlHlTk4b5ZVgCZS1ozVryJXd5-Xggyp4EOhBOsJIh0rzw/exec';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);

  // no-cors gives an opaque response: resolving means the request was sent, not that the row was saved.
  fetch(url + '?email=' + encodeURIComponent(emailValue), {
    mode: 'no-cors',
    method: 'GET',
    signal: controller.signal
  })
    .then(() => {
      clearTimeout(timer);
      btn.textContent = 'Subscribed!';
      input.value = '';
    })
    .catch(() => {
      clearTimeout(timer);
      btn.disabled = false;
      input.disabled = false;
      btn.textContent = 'Subscribe';
      if (confirm) confirm.textContent = 'Could not subscribe right now. Please try again.';
    });
}
