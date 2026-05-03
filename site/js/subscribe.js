function submitEmail(e) {
  // If triggered by a button click, we don't necessarily have a form submit event
  if (e && e.preventDefault) e.preventDefault();
  
  const container = document.querySelector('.email-inner');
  const input = container.querySelector('.email-input');
  const btn = container.querySelector('.email-btn');
  const confirm = document.getElementById('email-confirm');
  const emailValue = input.value.trim();
  
  if (!emailValue) return;

  const url = 'https://script.google.com/macros/s/AKfycbxx1BxqfL6EZ0O_XqUHQ4SBYr4MF7YaPH43tw-BN14Q55vxFi6fb_Y3WXpk0A8jecf4qg/exec';
  console.log('Attempting fetch to:', url);
  console.log('Email:', emailValue);

  btn.disabled = true;
  input.disabled = true;
  confirm.textContent = '⏳...';

  // Use URLSearchParams for standard form encoding
  const params = new URLSearchParams();
  params.append('email', emailValue);

  fetch(url, {
    method: 'POST',
    mode: 'no-cors',
    body: params
  }).then(() => {
    console.log('Request sent successfully (Opaque response received)');
    confirm.textContent = 'Subscribed! ✅';
    input.value = '';
    // Re-enable if user wants to subscribe another email, but usually one is enough.
    // Keeping them disabled as per previous pattern.
  }).catch((err) => {
    console.error('Network-level error:', err);
    confirm.textContent = '❌ Error';
    btn.disabled = false;
    input.disabled = false;
  });
}
