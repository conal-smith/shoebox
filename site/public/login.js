const form = document.getElementById('form');
const input = document.getElementById('code');
const error = document.getElementById('error');
input.addEventListener('input', () => {
  input.value = input.value.replace(/\D/g, '').slice(0, 6);
  error.textContent = '';
  if (input.value.length === 6) form.requestSubmit();
});
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const res = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: input.value }) });
    const body = await res.json().catch(() => ({}));
    if (res.ok) return location.reload();
    error.textContent = body.error || 'Something went wrong. Try again.';
    input.value = '';
    input.focus();
  } catch {
    error.textContent = 'Couldn’t connect. Check your internet and try again.';
  } finally {
    button.disabled = false;
  }
});
