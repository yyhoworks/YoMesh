'use strict';
(() => {
  const form = document.querySelector('#login-form');
  const user = document.querySelector('#login-user');
  const password = document.querySelector('#login-password');
  const submit = document.querySelector('#login-submit');
  const retry = document.querySelector('#login-retry');
  const reveal = document.querySelector('#login-reveal');
  const status = document.querySelector('#login-status');
  const error = document.querySelector('#login-error');
  // Only return to the workspace's own hash routes, never a supplied URL.
  const destination = '/' + (/^#[a-z][a-z0-9-]*$/i.test(location.hash) ? location.hash : '');
  const expired = new URLSearchParams(location.search).get('reason') === 'expired';
  let ready = false;
  let busy = false;

  function showError(message) {
    error.textContent = message;
    error.hidden = !message;
  }
  function setBusy(value) {
    busy = value;
    form.setAttribute('aria-busy', String(value));
    submit.disabled = value || !ready;
    retry.disabled = value;
    submit.textContent = value && ready ? '正在验证…' : '登录工作台';
  }
  async function request(path, body) {
    const response = await fetch(path, {
      credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000),
      ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})
    });
    const result = await response.json();
    if (!response.ok) {
      const failure = new Error(result.error || '请求失败，请稍后重试。');
      failure.unavailable = response.status === 503;
      throw failure;
    }
    return result;
  }
  async function checkStatus() {
    ready = false;
    setBusy(true);
    showError('');
    retry.hidden = true;
    status.textContent = '正在检查登录状态…';
    try {
      const result = await request('/api/auth/status');
      if (result.authenticated) {
        location.replace(destination);
        return;
      }
      ready = true;
      status.textContent = expired ? '登录已过期，请重新登录。' : '';
      if (!user.value) user.value = result.username || '';
      // Do not steal focus when the visitor has already begun typing.
      if (document.activeElement === document.body) (user.value ? password : user).focus();
    } catch (failure) {
      status.textContent = '';
      showError(failure.unavailable ? failure.message : '无法连接登录服务。请检查网络和服务状态后重试。');
      retry.hidden = false;
    } finally { setBusy(false); }
  }
  reveal.addEventListener('click', () => {
    const visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    reveal.textContent = visible ? '隐藏' : '显示';
    reveal.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    reveal.setAttribute('aria-pressed', String(visible));
  });
  retry.addEventListener('click', checkStatus);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    showError('');
    status.textContent = '';
    try {
      await request('/api/auth/login', {username: user.value.trim(), password: password.value});
      password.value = '';
      location.replace(destination);
    } catch (failure) {
      showError(failure instanceof TypeError || failure.name === 'TimeoutError' ? '无法连接登录服务，请检查网络后重试。' : failure.message);
      if (failure.unavailable) {
        ready = false;
        retry.hidden = false;
      }
      password.focus();
      password.select();
    } finally { setBusy(false); }
  });
  // Recheck sessions on history restoration; never resurrect a cached form state.
  window.addEventListener('pageshow', event => { if (event.persisted) checkStatus(); });
  checkStatus();
})();
