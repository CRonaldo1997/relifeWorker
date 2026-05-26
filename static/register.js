/**
 * register.js — handles the user-registration form.
 *
 * POSTs to /api/auth/register. On success, redirects to /login so the user
 * must explicitly sign in. On any error, shows a message and re-enables
 * the submit button.
 */
(function () {
  'use strict';

  var form = document.getElementById('register-form');
  if (!form) return;

  var errEl = document.getElementById('reg-err');
  var btn = form.querySelector('button[type=submit]');
  var btnDefaultText = btn ? btn.textContent : '立即注册';

  function showErr(msg) {
    errEl.style.color = '';
    errEl.style.background = '';
    errEl.style.borderColor = '';
    errEl.textContent = msg;
    errEl.style.display = 'block';
  }

  function showInfo(msg) {
    errEl.style.color = '#0057A3';
    errEl.style.background = 'rgba(0,87,163,.06)';
    errEl.style.borderColor = 'rgba(0,87,163,.2)';
    errEl.textContent = msg;
    errEl.style.display = 'block';
  }

  function clearErr() {
    errEl.style.display = 'none';
    errEl.textContent = '';
  }

  function resetButton() {
    if (!btn) return;
    btn.disabled = false;
    btn.textContent = btnDefaultText;
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    clearErr();

    var usernameEl = document.getElementById('reg-username');
    var pwEl = document.getElementById('reg-pw');
    var pw2El = document.getElementById('reg-pw2');

    var username = usernameEl ? usernameEl.value.trim() : '';
    var pw = pwEl ? pwEl.value : '';
    var pw2 = pw2El ? pw2El.value : '';

    if (!username) { showErr('请输入用户名'); return; }
    if (username.length < 2) { showErr('用户名至少需要 2 个字符'); return; }
    if (!pw) { showErr('请输入密码'); return; }
    if (pw.length < 4) { showErr('密码至少需要 4 个字符'); return; }
    if (pw !== pw2) { showErr('两次输入的密码不一致'); return; }

    if (btn) {
      btn.disabled = true;
      btn.textContent = '注册中…';
    }

    fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username, password: pw }),
      credentials: 'same-origin',
    })
      .then(function (r) {
        return r.json().then(function (d) { return { status: r.status, data: d }; });
      })
      .then(function (res) {
        if (!(res.data && res.data.ok)) {
          showErr((res.data && (res.data.error || res.data.detail)) || '注册失败，请重试');
          resetButton();
          return null;
        }
        showInfo('注册成功，即将跳转至登录页…');
        setTimeout(function () { window.location.href = '/login'; }, 1200);
        return null;
      })
      .catch(function () {
        showErr('网络错误，请重试');
        resetButton();
      });
  });
})();
