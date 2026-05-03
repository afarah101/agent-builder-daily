function submitEmail(e) {
  e.preventDefault();
  const input = e.target.querySelector('.email-input');
  const btn = e.target.querySelector('.email-btn');
  const confirm = document.getElementById('email-confirm');
  const email = input.value.trim();
  if (!email) return;

  btn.disabled = true;
  input.disabled = true;
  confirm.textContent = '⏳ Joining list...';

  // Use URLSearchParams for Google Apps Script compatibility
  const formData = new URLSearchParams();
  formData.append('email', email);

  fetch('https://script.google.com/macros/s/AKfycbxx1BxqfL6EZ0O_XqUHQ4SBYr4MF7YaPH43tw-BN14Q55vxFi6fb_Y3WXpk0A8jecf4qg/exec', {
    method: 'POST',
    mode: 'no-cors',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: formData.toString()
  }).then(() => {
    // In no-cors mode, we can't check response.ok or response.status.
    // If the promise resolves (reaches this block), we assume success.
    confirm.textContent = 'Subscribed! ✅';
    input.value = '';
  }).catch(() => {
    confirm.textContent = '❌ Error. Please try again.';
    btn.disabled = false;
    input.disabled = false;
  });
}
