// A text/plain POST is a CORS simple request (no preflight). Apps Script answers with a 302 to
// script.googleusercontent.com, which sends Access-Control-Allow-Origin: *, so the reply text is readable.
const SUBSCRIBE_URL = 'https://script.google.com/macros/s/AKfycbydhgDM0-jFaYVo9g4Wjch0ABlHlTk4b5ZVgCZS1ozVryJXd5-Xggyp4EOhBOsJIh0rzw/exec';
const SUBSCRIBE_TIMEOUT_MS = 10000;
const SUBSCRIBE_ERROR = 'Something went wrong, please try again';

// The script replies "Success! Email captured: <email>"; anything else is not a confirmation.
function subscribeConfirmed(text) {
  return /^Success!/.test(String(text).trim());
}

function submitEmail(e) {
  if (e && e.preventDefault) e.preventDefault();

  const container = document.querySelector('.email-inner');
  const input = container.querySelector('.email-input');
  const btn = container.querySelector('.email-btn');
  const confirm = document.getElementById('email-confirm');
  const emailValue = input.value.trim();

  if (!emailValue) return Promise.resolve();

  btn.disabled = true;
  input.disabled = true;
  btn.textContent = 'Submitting…';
  if (confirm) confirm.textContent = '';

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SUBSCRIBE_TIMEOUT_MS);

  return fetch(SUBSCRIBE_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain;charset=utf-8' },
    body: JSON.stringify({ email: emailValue }),
    signal: controller.signal
  })
    .then(res => (res.ok ? res.text() : Promise.reject(new Error('HTTP ' + res.status))))
    .then(text => {
      if (!subscribeConfirmed(text)) throw new Error('reply did not confirm');
      btn.textContent = 'Subscribed';
      if (confirm) confirm.textContent = 'Subscribed';
      input.value = '';
    })
    .catch(() => {  // network error, non-2xx, unconfirmed reply or the 10 s timeout
      btn.disabled = false;
      input.disabled = false;
      btn.textContent = 'Subscribe';
      if (confirm) confirm.textContent = SUBSCRIBE_ERROR;
    })
    .finally(() => clearTimeout(timer));
}
