/* =========================================================================
 *  ASPIDUS — TOP BAR (V23.1 #2)
 * =========================================================================
 *  Horizontalni bar koji sedi iznad sadrzaja i drzi:
 *    - Global search dugme (otvara Cmd+K palettu)
 *    - Breadcrumbs mount (levo)
 *    - Notifications ikonu (bell)
 *    - Quick actions (New offer, New deal, itd — po permisiji)
 *    - Preferences (settings dropdown → Profile / Security / Preferences / Sign out)
 *
 *  Sam sebe montira u element sa id="topbar-mount". Ako ga nema,
 *  auto-injektuje pre body-first-child-a.
 * ========================================================================= */

(function () {
    'use strict';
    if (window.topbarMounted) return;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g,
            m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
    }

    function build() {
        const user = (window.state && window.state.user) || {};
        const initial = (user.username || '?').charAt(0).toUpperCase();
        const html = `
        <div id="topbar" class="sticky top-0 z-40 bg-white/95 backdrop-blur border-b border-slate-200 px-4 py-2.5 flex items-center gap-3">
            <button id="tb-hamburger" title="Menu" class="lg:hidden p-1.5 hover:bg-slate-100 rounded-lg text-slate-600 -ml-2" aria-label="Toggle sidebar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="w-5 h-5"><path d="M3 6h18M3 12h18M3 18h18"/></svg>
            </button>
            <div id="breadcrumbs-mount" class="flex-shrink min-w-0 flex-1"></div>

            <button id="tb-search" title="Global search (⌘K)" class="hidden md:flex items-center gap-2 px-3 py-1.5 text-xs text-slate-500 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-lg">
                🔍 <span>Search…</span>
                <kbd class="ml-2 text-[10px] px-1.5 py-0.5 bg-white border border-slate-200 rounded">⌘K</kbd>
            </button>

            <a href="/documents/new/offer" title="New offer" class="hidden lg:inline-flex text-xs text-white bg-gradient-to-br from-indigo-500 to-purple-600 hover:opacity-90 px-3 py-1.5 rounded-lg font-semibold">
                + New offer
            </a>

            <button id="tb-notifications" title="Notifications" class="relative p-1.5 hover:bg-slate-100 rounded-lg text-slate-600">
                🔔
                <span id="tb-notif-dot" class="hidden absolute top-0 right-0 w-2 h-2 bg-red-500 rounded-full"></span>
            </button>

            <div class="relative">
                <button id="tb-menu-btn" class="flex items-center gap-2 hover:bg-slate-100 rounded-lg px-2 py-1">
                    <div class="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 text-white flex items-center justify-center text-xs font-bold">${esc(initial)}</div>
                    <span class="text-xs text-slate-600 hidden sm:inline">${esc(user.username || 'Guest')}</span>
                </button>
                <div id="tb-menu" class="hidden absolute right-0 top-full mt-1 w-56 bg-white border border-slate-200 rounded-lg shadow-lg z-50">
                    <a href="/profile/security" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">🔐 Security Center</a>
                    <button onclick="if(window.openPreferences)openPreferences()" class="w-full text-left block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">⚙ Preferences</button>
                    <a href="/documents/register" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">📚 Document Register</a>
                    ${user.role==='admin' ? `
                    <div class="border-t border-slate-100 my-1"></div>
                    <div class="px-3 pt-2 pb-1 text-[10px] uppercase text-slate-400 font-bold">Admin</div>
                    <a href="/admin/permissions" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">🔒 Permissions Matrix</a>
                    <a href="/admin/portal-permissions" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">🌐 Portal Permissions</a>
                    <a href="/admin/reports" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">📊 Custom Reports</a>
                    <a href="/admin/health" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">💚 Health</a>
                    <a href="/admin/mail-queue" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">📧 Mail Queue</a>
                    <a href="/admin/supabase" class="block px-3 py-2 text-sm hover:bg-slate-50 text-slate-700">☁ Operations Center</a>
                    ` : ''}
                    <div class="border-t border-slate-100 my-1"></div>
                    <button onclick="if(window.logout)logout()" class="w-full text-left block px-3 py-2 text-sm hover:bg-red-50 text-red-600">→ Sign out</button>
                </div>
            </div>
        </div>
        `;
        let mount = document.getElementById('topbar-mount');
        if (!mount) {
            mount = document.createElement('div');
            mount.id = 'topbar-mount';
            document.body.insertBefore(mount, document.body.firstChild);
        }
        mount.innerHTML = html;

        // Wire hamburger — off-canvas toggle on mobile
        document.getElementById('tb-hamburger')?.addEventListener('click', () => {
            document.body.classList.toggle('sidebar-mobile-open');
        });
        // Klik na overlay zatvara sidebar
        document.body.addEventListener('click', (e) => {
            if (document.body.classList.contains('sidebar-mobile-open')) {
                const sb = document.getElementById('app-sidebar');
                if (sb && !sb.contains(e.target) && e.target.id !== 'tb-hamburger'
                    && !(e.target.closest && e.target.closest('#tb-hamburger'))) {
                    document.body.classList.remove('sidebar-mobile-open');
                }
            }
        });

        // Wire
        document.getElementById('tb-search')?.addEventListener('click', () => {
            if (window.openCommandPalette) window.openCommandPalette();
            else document.dispatchEvent(new KeyboardEvent('keydown', {key:'k', metaKey:true}));
        });
        document.getElementById('tb-notifications')?.addEventListener('click', () => {
            if (window.showNotificationsPanel) window.showNotificationsPanel();
        });
        const menuBtn = document.getElementById('tb-menu-btn');
        const menu = document.getElementById('tb-menu');
        menuBtn?.addEventListener('click', (e) => {
            e.stopPropagation();
            menu.classList.toggle('hidden');
        });
        document.addEventListener('click', (e) => {
            if (menu && !menu.contains(e.target) && e.target !== menuBtn) menu.classList.add('hidden');
        });

        // Mount breadcrumbs
        if (window.mountBreadcrumbs) window.mountBreadcrumbs('breadcrumbs-mount');

        window.topbarMounted = true;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', build);
    } else {
        build();
    }

    // Ekspoze API za re-render (npr. posle logina, kad se state.user promeni)
    window.rebuildTopbar = () => { window.topbarMounted = false; build(); };
})();
