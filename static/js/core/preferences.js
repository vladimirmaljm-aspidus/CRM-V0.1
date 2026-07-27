/* =========================================================================
 *  ASPIDUS — USER PROFILE & PREFERENCES panel
 * =========================================================================
 * Otvara se kroz avatar/username u sidebaru ili keyboard shortcut Cmd+,
 * Nudi:
 *   • Ime, email, telefon (za trenutnog usera)
 *   • Menjanje lozinke
 *   • Jezik (SR / EN)
 *   • Tema (Auto / Light / Dark)
 *   • Density (Comfortable / Compact)
 *   • Notification preferences
 *   • Personalni potpis + pečat (permission-based)
 *   • Session info (last login, IP, device)
 *
 * Svi ovi se čuvaju u localStorage + PATCH-uju backend za trajno.
 */

(function() {
    'use strict';

    // --- Utility: prefs storage layer ---
    const PREF_KEY = 'aspidus.prefs.v1';
    function getPrefs() {
        try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}'); }
        catch(_) { return {}; }
    }
    function setPrefs(patch) {
        const cur = getPrefs();
        const next = Object.assign({}, cur, patch);
        localStorage.setItem(PREF_KEY, JSON.stringify(next));
        applyPrefs(next);
        return next;
    }
    function applyPrefs(p) {
        const html = document.documentElement;
        // Theme
        if (p.theme === 'dark') html.setAttribute('data-theme', 'dark');
        else if (p.theme === 'light') html.setAttribute('data-theme', 'light');
        else html.removeAttribute('data-theme'); // auto
        // Density
        html.setAttribute('data-density', p.density || 'comfortable');
        // Language — deferred to i18n system if exists
        if (p.language && typeof window.setLanguage === 'function') {
            try { window.setLanguage(p.language); } catch(_) {}
        }
    }
    // Apply on load
    applyPrefs(getPrefs());

    // --- Preferences modal ---
    function openPreferences() {
        const cur = getPrefs();
        const user = (window.state && window.state.user) || {};

        const html = `
        <div class="pref-modal-overlay" onclick="if(event.target===this)closePreferences()">
          <div class="pref-modal">
            <div class="pref-head">
              <div>
                <div class="pref-title">👤 Profile & Preferences</div>
                <div class="pref-subtitle">${escapeHtml(user.username || 'unknown')} · ${escapeHtml(user.role || 'user')}</div>
              </div>
              <button class="pref-close" onclick="closePreferences()">×</button>
            </div>
            <div class="pref-tabs">
              <button class="pref-tab active" data-tab="profile">Profile</button>
              <button class="pref-tab" data-tab="security">Security</button>
              <button class="pref-tab" data-tab="appearance">Appearance</button>
              <button class="pref-tab" data-tab="notifications">Notifications</button>
              <button class="pref-tab" data-tab="session">Session</button>
            </div>
            <div class="pref-body">
              <!-- PROFILE -->
              <div class="pref-pane active" data-pane="profile">
                <div class="pref-field">
                  <label>Username</label>
                  <input type="text" value="${escapeHtml(user.username || '')}" disabled>
                  <p class="pref-hint">Username se ne može menjati.</p>
                </div>
                <div class="pref-field">
                  <label>Full name</label>
                  <input type="text" id="pref-fullname" value="${escapeHtml(user.fullName || '')}" placeholder="Vladimir Maljković">
                </div>
                <div class="pref-field">
                  <label>Email</label>
                  <input type="email" id="pref-email" value="${escapeHtml(user.email || '')}" placeholder="vladimir@aspidus.co">
                </div>
                <div class="pref-field">
                  <label>Phone</label>
                  <input type="tel" id="pref-phone" value="${escapeHtml(user.phone || '')}" placeholder="+381 60 000 0000">
                </div>
                <div class="pref-actions">
                  <button class="pref-btn pref-btn-primary" onclick="saveProfile()">Save Profile</button>
                </div>
              </div>

              <!-- SECURITY -->
              <div class="pref-pane" data-pane="security">
                <div class="pref-field">
                  <label>Current password</label>
                  <input type="password" id="pref-cur-pwd" placeholder="••••••••">
                </div>
                <div class="pref-field">
                  <label>New password</label>
                  <input type="password" id="pref-new-pwd" placeholder="•••••••• (min. 8 karaktera)">
                </div>
                <div class="pref-field">
                  <label>Confirm new password</label>
                  <input type="password" id="pref-conf-pwd" placeholder="••••••••">
                </div>
                <div class="pref-actions">
                  <button class="pref-btn pref-btn-primary" onclick="saveCrmPassword()">Change Password</button>
                </div>
                <hr style="margin:16px 0;border:none;border-top:1px solid #e5e7eb">
                <div class="pref-field">
                  <label style="display:flex;align-items:center;gap:8px">
                    <span>Two-Factor Authentication (TOTP)</span>
                    <span id="pref-2fa-status" class="pref-badge">checking...</span>
                  </label>
                  <p class="pref-hint">Google Authenticator ili Authy app. Preporučeno za admin naloge.</p>
                  <button class="pref-btn pref-btn-secondary" onclick="open2FA()">Manage 2FA</button>
                </div>
              </div>

              <!-- APPEARANCE -->
              <div class="pref-pane" data-pane="appearance">
                <div class="pref-field">
                  <label>Theme</label>
                  <div class="pref-radio-group">
                    <label class="pref-radio">
                      <input type="radio" name="theme" value="auto" ${!cur.theme || cur.theme === 'auto' ? 'checked' : ''}>
                      <span>🖥 Auto (follow system)</span>
                    </label>
                    <label class="pref-radio">
                      <input type="radio" name="theme" value="light" ${cur.theme === 'light' ? 'checked' : ''}>
                      <span>☀️ Light</span>
                    </label>
                    <label class="pref-radio">
                      <input type="radio" name="theme" value="dark" ${cur.theme === 'dark' ? 'checked' : ''}>
                      <span>🌙 Dark</span>
                    </label>
                  </div>
                </div>
                <div class="pref-field">
                  <label>Density</label>
                  <div class="pref-radio-group">
                    <label class="pref-radio">
                      <input type="radio" name="density" value="comfortable" ${!cur.density || cur.density === 'comfortable' ? 'checked' : ''}>
                      <span>Comfortable (default)</span>
                    </label>
                    <label class="pref-radio">
                      <input type="radio" name="density" value="compact" ${cur.density === 'compact' ? 'checked' : ''}>
                      <span>Compact (više informacija na ekranu)</span>
                    </label>
                  </div>
                </div>
                <div class="pref-field">
                  <label>Language</label>
                  <select id="pref-language">
                    <option value="sr" ${cur.language === 'sr' ? 'selected' : ''}>🇷🇸 Srpski</option>
                    <option value="en" ${cur.language === 'en' || !cur.language ? 'selected' : ''}>🇬🇧 English</option>
                  </select>
                </div>
                <div class="pref-actions">
                  <button class="pref-btn pref-btn-primary" onclick="saveAppearance()">Apply</button>
                </div>
              </div>

              <!-- NOTIFICATIONS -->
              <div class="pref-pane" data-pane="notifications">
                <div class="pref-field">
                  <label style="display:flex;align-items:center;gap:8px">
                    <input type="checkbox" id="pref-notif-portal" ${cur.notifPortal !== false ? 'checked' : ''}>
                    <span>Portal events (KYC, RFQ, ponude)</span>
                  </label>
                </div>
                <div class="pref-field">
                  <label style="display:flex;align-items:center;gap:8px">
                    <input type="checkbox" id="pref-notif-deals" ${cur.notifDeals !== false ? 'checked' : ''}>
                    <span>Deal updates (nova ponuda, promena statusa)</span>
                  </label>
                </div>
                <div class="pref-field">
                  <label style="display:flex;align-items:center;gap:8px">
                    <input type="checkbox" id="pref-notif-email" ${cur.notifEmail === true ? 'checked' : ''}>
                    <span>Email digest (dnevni sažetak umesto svake notifikacije)</span>
                  </label>
                </div>
                <div class="pref-field">
                  <label style="display:flex;align-items:center;gap:8px">
                    <input type="checkbox" id="pref-notif-sound" ${cur.notifSound === true ? 'checked' : ''}>
                    <span>Zvuk kada dodje nova notifikacija</span>
                  </label>
                </div>
                <div class="pref-actions">
                  <button class="pref-btn pref-btn-primary" onclick="saveNotifications()">Save Preferences</button>
                </div>
              </div>

              <!-- SESSION -->
              <div class="pref-pane" data-pane="session">
                <div class="pref-info-block">
                  <div class="pref-info-row"><b>Login time:</b> <span id="sess-login">—</span></div>
                  <div class="pref-info-row"><b>Last activity:</b> <span id="sess-activity">—</span></div>
                  <div class="pref-info-row"><b>Session TTL:</b> <span id="sess-ttl">—</span></div>
                  <div class="pref-info-row"><b>IP:</b> <span id="sess-ip">—</span></div>
                  <div class="pref-info-row"><b>Device:</b> <span id="sess-device">${escapeHtml(navigator.userAgent.substring(0,60))}...</span></div>
                </div>
                <div class="pref-actions" style="gap:8px">
                  <button class="pref-btn pref-btn-secondary" onclick="refreshSession()">🔄 Refresh</button>
                  <button class="pref-btn pref-btn-danger" onclick="logoutNow()">Sign out</button>
                </div>
              </div>
            </div>
          </div>
        </div>
        `;
        const wrap = document.createElement('div');
        wrap.id = 'pref-wrap';
        wrap.innerHTML = html;
        document.body.appendChild(wrap);

        // Tab switcher
        wrap.querySelectorAll('.pref-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                wrap.querySelectorAll('.pref-tab').forEach(t => t.classList.remove('active'));
                wrap.querySelectorAll('.pref-pane').forEach(p => p.classList.remove('active'));
                tab.classList.add('active');
                wrap.querySelector(`.pref-pane[data-pane="${tab.dataset.tab}"]`).classList.add('active');
            });
        });

        // Populate session tab
        refreshSession();
        // 2FA status
        check2FAStatus();
    }

    function closePreferences() {
        const w = document.getElementById('pref-wrap');
        if (w) w.remove();
    }

    async function saveProfile() {
        const fullName = document.getElementById('pref-fullname').value;
        const email = document.getElementById('pref-email').value;
        const phone = document.getElementById('pref-phone').value;
        try {
            const r = await fetch('/api/users/me', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ fullName, email, phone })
            });
            if (r.ok) {
                if (typeof showToast === 'function') showToast('Profile updated.', 'success');
                if (window.state && window.state.user) {
                    Object.assign(window.state.user, { fullName, email, phone });
                }
            } else {
                const j = await r.json().catch(() => ({}));
                if (typeof showToast === 'function') showToast('Error: ' + (j.error || r.status), 'error');
            }
        } catch (e) {
            if (typeof showToast === 'function') showToast('Network error.', 'error');
        }
    }

    async function saveCrmPassword() {
        const cur = document.getElementById('pref-cur-pwd').value;
        const nw = document.getElementById('pref-new-pwd').value;
        const cf = document.getElementById('pref-conf-pwd').value;
        if (!cur || !nw) return showToast && showToast('Fields required.', 'error');
        if (nw.length < 8) return showToast && showToast('Password too short (min 8).', 'error');
        if (nw !== cf) return showToast && showToast('New passwords do not match.', 'error');
        try {
            const r = await fetch('/api/users/change-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current: cur, next: nw })
            });
            const j = await r.json().catch(() => ({}));
            if (r.ok && j.status === 'success') {
                showToast && showToast('Password changed.', 'success');
                document.getElementById('pref-cur-pwd').value = '';
                document.getElementById('pref-new-pwd').value = '';
                document.getElementById('pref-conf-pwd').value = '';
            } else {
                showToast && showToast('Error: ' + (j.error || 'Unknown'), 'error');
            }
        } catch (e) { showToast && showToast('Network error.', 'error'); }
    }

    function saveAppearance() {
        const theme = document.querySelector('input[name="theme"]:checked')?.value;
        const density = document.querySelector('input[name="density"]:checked')?.value;
        const language = document.getElementById('pref-language')?.value;
        setPrefs({ theme, density, language });
        showToast && showToast('Appearance saved.', 'success');
    }

    function saveNotifications() {
        setPrefs({
            notifPortal: document.getElementById('pref-notif-portal').checked,
            notifDeals: document.getElementById('pref-notif-deals').checked,
            notifEmail: document.getElementById('pref-notif-email').checked,
            notifSound: document.getElementById('pref-notif-sound').checked,
        });
        showToast && showToast('Notification preferences saved.', 'success');
    }

    async function check2FAStatus() {
        const badge = document.getElementById('pref-2fa-status');
        if (!badge) return;
        try {
            const r = await fetch('/api/2fa/status');
            const j = await r.json();
            badge.textContent = j.enabled ? '✓ Enabled' : 'Not set up';
            badge.className = 'pref-badge ' + (j.enabled ? 'pref-badge-green' : 'pref-badge-amber');
        } catch(_) { badge.textContent = 'Unknown'; }
    }

    function open2FA() {
        if (typeof window.show2FASetup === 'function') { closePreferences(); window.show2FASetup(); return; }
        window.location.href = '/2fa/setup';
    }

    async function refreshSession() {
        try {
            const r = await fetch('/api/session/info');
            if (!r.ok) return;
            const j = await r.json();
            const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
            set('sess-login', j.login_time || '—');
            set('sess-activity', j.last_active || '—');
            set('sess-ttl', j.ttl_seconds ? (Math.round(j.ttl_seconds/60) + ' min inactivity') : '—');
            set('sess-ip', j.ip || '—');
        } catch(_) {}
    }

    function logoutNow() {
        if (confirm('Sign out?')) window.location.href = '/logout';
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
    }

    // Expose
    window.openPreferences = openPreferences;
    window.closePreferences = closePreferences;
    window.saveProfile = saveProfile;
    window.saveCrmPassword = saveCrmPassword;
    window.saveAppearance = saveAppearance;
    window.saveNotifications = saveNotifications;
    window.refreshSession = refreshSession;
    window.logoutNow = logoutNow;
    window.open2FA = open2FA;

    // Keyboard shortcut Cmd/Ctrl + , to open
    document.addEventListener('keydown', e => {
        if ((e.metaKey || e.ctrlKey) && e.key === ',') {
            e.preventDefault();
            openPreferences();
        }
    });

    // Auto-inject styles once
    if (!document.getElementById('pref-modal-styles')) {
        const s = document.createElement('style');
        s.id = 'pref-modal-styles';
        s.textContent = `
        .pref-modal-overlay { position:fixed; inset:0; background:rgba(15,23,42,.55); backdrop-filter:blur(6px); z-index:1000; display:flex; align-items:center; justify-content:center; padding:16px; animation:pref-fade .15s ease; }
        .pref-modal { background:white; border-radius:18px; width:100%; max-width:720px; max-height:90vh; overflow:hidden; box-shadow:0 30px 80px rgba(15,23,42,.35); display:flex; flex-direction:column; animation:pref-slide .2s cubic-bezier(.4,0,.2,1); }
        @keyframes pref-fade { from{opacity:0} to{opacity:1} }
        @keyframes pref-slide { from{transform:translateY(20px);opacity:0} to{transform:translateY(0);opacity:1} }
        .pref-head { padding:22px 26px 16px; border-bottom:1px solid #e5e7eb; display:flex; align-items:center; justify-content:space-between; }
        .pref-title { font-size:18px; font-weight:700; color:#0f172a; letter-spacing:-.01em; }
        .pref-subtitle { font-size:12px; color:#64748b; margin-top:2px; }
        .pref-close { width:32px; height:32px; border-radius:8px; background:#f1f5f9; border:none; font-size:20px; cursor:pointer; color:#475569; }
        .pref-close:hover { background:#e2e8f0; }
        .pref-tabs { display:flex; gap:2px; padding:8px 20px 0; border-bottom:1px solid #e5e7eb; overflow-x:auto; }
        .pref-tab { background:transparent; border:none; padding:10px 14px; border-radius:8px 8px 0 0; font-size:13px; font-weight:500; color:#64748b; cursor:pointer; border-bottom:2px solid transparent; transition:all .15s; white-space:nowrap; }
        .pref-tab:hover { color:#4f46e5; background:#f8fafc; }
        .pref-tab.active { color:#4f46e5; border-bottom-color:#4f46e5; font-weight:600; }
        .pref-body { padding:22px 26px; overflow-y:auto; flex:1; }
        .pref-pane { display:none; }
        .pref-pane.active { display:block; }
        .pref-field { margin-bottom:16px; }
        .pref-field label { display:block; font-size:12px; font-weight:600; color:#334155; margin-bottom:6px; }
        .pref-field input:not([type="radio"]):not([type="checkbox"]), .pref-field select { width:100%; padding:10px 14px; border:1px solid #e2e8f0; border-radius:10px; font-size:13px; transition:border-color .15s; }
        .pref-field input:focus, .pref-field select:focus { outline:none; border-color:#4f46e5; box-shadow:0 0 0 4px rgba(79,70,229,.15); }
        .pref-field input:disabled { background:#f8fafc; color:#94a3b8; }
        .pref-hint { color:#94a3b8; font-size:11px; margin-top:4px; }
        .pref-radio-group { display:flex; flex-direction:column; gap:10px; }
        .pref-radio { display:flex; align-items:center; gap:10px; padding:10px 14px; border:1px solid #e2e8f0; border-radius:10px; cursor:pointer; font-size:13px; transition:all .15s; }
        .pref-radio:has(input:checked) { border-color:#4f46e5; background:#eef2ff; }
        .pref-radio input { margin:0; }
        .pref-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:20px; padding-top:16px; border-top:1px solid #f1f5f9; }
        .pref-btn { padding:9px 18px; border-radius:10px; font-weight:600; font-size:13px; border:none; cursor:pointer; transition:all .15s; }
        .pref-btn-primary { background:linear-gradient(135deg,#4f46e5,#7c3aed); color:white; box-shadow:0 4px 12px -2px rgba(79,70,229,.35); }
        .pref-btn-primary:hover { transform:translateY(-1px); }
        .pref-btn-secondary { background:white; color:#334155; border:1px solid #e2e8f0; }
        .pref-btn-secondary:hover { background:#f8fafc; }
        .pref-btn-danger { background:#ef4444; color:white; }
        .pref-btn-danger:hover { background:#dc2626; }
        .pref-badge { padding:2px 10px; border-radius:999px; font-size:10px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
        .pref-badge-green { background:#dcfce7; color:#166534; }
        .pref-badge-amber { background:#fef3c7; color:#92400e; }
        .pref-info-block { background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:14px 16px; }
        .pref-info-row { display:flex; justify-content:space-between; padding:6px 0; font-size:13px; color:#334155; border-bottom:1px dashed #e5e7eb; }
        .pref-info-row:last-child { border-bottom:none; }
        `;
        document.head.appendChild(s);
    }
})();
