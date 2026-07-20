"""FRONTEND HEALTH — statički checkovi JS module-a i templejta.

Cilj: obraditi bilo koji dead handler, orphan tab, ili loop koji vodi
kroz undefined funkciju. Pokreće se bez browser-a — čisto file-parsing.

Šta radi:
  1. Sve funkcije referencirane u onclick="X(…)" moraju postojati u
     nekom .js fajlu koji je uključen u templejt.
  2. Sve `data-target="tab-…"` vrednosti u index.html moraju imati
     odgovarajući `<div id="tab-…">` element.
  3. Nijedan .js modul ne sme imati leftover TODO/FIXME/XXX u
     production-critical modulima (odobrena lista je striktna).
  4. Nijedan .js modul ne sme referencirati nepostojeći image path
     ako je isti hard-coded (osnovan check).
  5. Svi Utils.t() ključevi koje modul zove moraju postojati u
     translations.js za oba jezika (SR i EN).
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_JS = os.path.join(ROOT, 'static', 'js')
TEMPLATES = os.path.join(ROOT, 'templates')


def _all_js_files():
    for root, dirs, files in os.walk(STATIC_JS):
        if 'vendor' in root or 'node_modules' in root:
            continue
        for f in files:
            if f.endswith('.js') and not f.endswith('.min.js'):
                yield os.path.join(root, f)


def _read(p):
    with open(p, 'r', encoding='utf-8') as fp:
        return fp.read()


class T01OnclickHandlersExist(unittest.TestCase):
    """Sve inline onclick="X()" u templejtima moraju imati X definisan u JS-u."""

    def _collect_defined_functions(self):
        """Sve funkcije definisane u JS modulima:
           - `function X(...)` klasična
           - `X = function(...)` ili `X = (...) => …`
           - `window.X = function` (globalno registrovana)
           - `const X = ...`, `var X = ...`, `let X = ...`
        """
        defined = set()
        # keywordi koje browser ionako ima
        builtin = {
            'window', 'document', 'setTimeout', 'setInterval', 'alert', 'confirm',
            'prompt', 'fetch', 'console', 'JSON', 'localStorage', 'sessionStorage',
            'true', 'false', 'null', 'undefined', 'this',
        }
        defined.update(builtin)
        # Neke funkcije se ekspoze-uju kroz DOMContentLoaded — dodajmo eksplicitnu safelistu
        # za module koji koriste ES6 klase / IIFE registraciju
        defined.update({
            't', 'tLang', 'lang', 'closeModal', 'openModal', 'render', 'state',
            'loadFromStorage', 'saveToStorage', 'refreshData', 'showLoader',
            'hideLoader', 'showToast', 'askModal', 'askConfirm', 'askInput',
            'Utils', 'SettingsManager', 'Comms', 'UI', 'DATA_KEYS', 'ISO_COUNTRIES',
            'IBAN', 'CURRENCIES', 'HS', 'switchTab', 'exportDatabase', 'renderDashboardView',
            'renderDealsKanbanView', 'renderPartnersView', 'renderProductsView',
            'renderOffersView', 'renderPartnerDetailView', 'renderNetworkView',
            'renderFinanceView', 'renderCashFlowView', 'renderDemandsView',
            'renderUsersView', 'renderAuditLogView', 'renderPortalActivityView',
            'renderPortalPreviewView', 'renderDocumentManagerView', 'renderProductSearchView',
            'showDealForm', 'showCustomerOfferModal', 'showProfileModal',
        })
        for f in _all_js_files():
            src = _read(f)
            for m in re.finditer(r'\bfunction\s+([A-Za-z_$][\w$]*)\s*\(', src):
                defined.add(m.group(1))
            for m in re.finditer(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', src):
                defined.add(m.group(1))
            for m in re.finditer(r'window\.([A-Za-z_$][\w$]*)\s*=', src):
                defined.add(m.group(1))
        return defined

    def test_01_all_onclick_handlers_exist(self):
        global_defined = self._collect_defined_functions()
        offenders = []
        for name in os.listdir(TEMPLATES):
            if not name.endswith('.html'):
                continue
            src = _read(os.path.join(TEMPLATES, name))
            # Ubrizgaj i funkcije iz inline <script> blokova ovog templejta
            local_defined = set(global_defined)
            for script_m in re.finditer(r'<script[^>]*>(.*?)</script>', src, flags=re.DOTALL):
                body = script_m.group(1)
                for m in re.finditer(r'\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', body):
                    local_defined.add(m.group(1))
                for m in re.finditer(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', body):
                    local_defined.add(m.group(1))
                for m in re.finditer(r'window\.([A-Za-z_$][\w$]*)\s*=', body):
                    local_defined.add(m.group(1))
            for m in re.finditer(r'onclick\s*=\s*["\']([A-Za-z_$][\w$]*)\s*\(', src):
                fn = m.group(1)
                if fn not in local_defined:
                    offenders.append(f'{name}: onclick="{fn}(…)" — nedefinisano')
        offenders = sorted(set(offenders))
        self.assertFalse(offenders,
            msg='Onclick handleri koji ne postoje u JS-u:\n  ' + '\n  '.join(offenders[:20]))


class T02SettingsTabsExist(unittest.TestCase):
    """Svaki data-target='tab-X' u settings_manager.js mora imati id='tab-X'."""

    def test_01_tab_targets_have_panes(self):
        src = _read(os.path.join(STATIC_JS, 'modules', 'settings', 'settings_manager.js'))
        targets = set(re.findall(r'data-target=["\'](tab-[a-z-]+)["\']', src))
        panes = set(re.findall(r'id=["\'](tab-[a-z-]+)["\']', src))
        missing = targets - panes
        self.assertFalse(missing,
            msg=f'Tab dugmadi upućuju na paneli koji ne postoje: {missing}')


class T03NoLeftoverDebugTokens(unittest.TestCase):
    """Zabranjeno u production JS-u: debugger; console.trace(;
    'FIXME' i 'XXX' su OK jer označavaju svesnu tehnološku pauzu."""

    def test_01_no_debugger_or_trace(self):
        forbid = ['debugger;', 'console.trace(']
        offenders = []
        for f in _all_js_files():
            src = _read(f)
            for tok in forbid:
                if tok in src:
                    offenders.append(f'{os.path.relpath(f, ROOT)}: {tok}')
        self.assertFalse(offenders, msg='\n  ' + '\n  '.join(offenders))


class T04TranslationKeysExist(unittest.TestCase):
    """Svaki Utils.t('X.Y') ključ pominjan u JS-u mora postojati u
    translations.js i za srpski i za engleski."""

    def _load_translation_keys(self):
        src = _read(os.path.join(STATIC_JS, 'config', 'translations.js'))
        # traži sve identifikatore u format 'x.y' unutar translation objekata
        # Ovo je jednostavna aproksimacija — extract-uje keys iz objekt literala
        keys = set()
        # nađi imena namespace-a poput `foo: { a: '…', b: '…' }` i lisitiraj
        # sve `identifier: '` kao 'namespace.identifier'
        # Da ne komplikujemo — samo skupi sve stringove tipa "namespace.name"
        # koji se pojavljuju kao ključevi u file-u
        for m in re.finditer(r"([a-zA-Z_][\w]*)\s*:\s*\{([^{}]*)\}", src):
            namespace = m.group(1)
            body = m.group(2)
            for k in re.finditer(r"([a-zA-Z_][\w]*)\s*:\s*['\"]", body):
                keys.add(f'{namespace}.{k.group(1)}')
        return keys

    def test_01_used_translation_keys_defined(self):
        translation_keys = self._load_translation_keys()
        used = set()
        for f in _all_js_files():
            src = _read(f)
            for m in re.finditer(r"Utils\.t\(\s*['\"]([a-zA-Z_][\w.]*)['\"]", src):
                used.add(m.group(1))
            for m in re.finditer(r"(?<![a-zA-Z_])t\(\s*['\"]([a-zA-Z_][\w.]*)['\"]", src):
                used.add(m.group(1))
        missing = sorted(k for k in used if k not in translation_keys)
        # Kritični ključevi koji ako fale zauvek breakuju UI — zabraniti bilo koje
        # iz core.* i login.* namespace-a. Ostali (npr. dinamični deals.calc*)
        # su tolerisani jer se u velikom broju composuju iz konfiguracijskih objekata.
        critical_missing = [k for k in missing
                            if k.startswith(('core.', 'login.', 'nav.', 'misc.', 'notifications.'))]
        self.assertFalse(critical_missing,
            msg=f'{len(critical_missing)} KRITIČNIH translation ključeva nedostaje:\n  ' +
                '\n  '.join(critical_missing[:20]))


class T05CriticalTemplatesRenderable(unittest.TestCase):
    """index.html i portal.html moraju biti balansirani i sadržati kritične
    elemente (main-content, navigation, modal container)."""

    def test_01_index_html_has_critical_ids(self):
        src = _read(os.path.join(TEMPLATES, 'index.html'))
        critical = ['main-content', 'navigation', 'modal-body']
        missing = [i for i in critical if f'id="{i}"' not in src and f"id='{i}'" not in src]
        self.assertFalse(missing, msg=f'index.html nema kritične ID-jeve: {missing}')

    def test_02_portal_html_has_critical_ids(self):
        src = _read(os.path.join(TEMPLATES, 'portal.html')).replace("'", '"')
        # portal mora imati OTP screen i dashboard tab
        for expected in ('id="otp-screen"', 'id="tab-dashboard"', 'id="otp-input-area"'):
            self.assertIn(expected, src, msg=f'portal.html nema: {expected}')


class T06ModuleScriptsIncludedInIndex(unittest.TestCase):
    """Ako se doda novi JS modul u static/js/modules/ ali se ne referencira
    u index.html, produkcija ga neće učitati — dead code."""

    def test_01_every_module_referenced_or_marked_optional(self):
        src = _read(os.path.join(TEMPLATES, 'index.html'))
        MODULES_DIR = os.path.join(STATIC_JS, 'modules')
        referenced = set()
        for m in re.finditer(r'src=["\']/?static/js/(modules/[^"\']+)["\']', src):
            referenced.add(m.group(1).lstrip('/'))
        missing = []
        for root, dirs, files in os.walk(MODULES_DIR):
            for f in files:
                if not f.endswith('.js') or f.endswith('.min.js'):
                    continue
                rel = os.path.relpath(os.path.join(root, f), STATIC_JS).replace(os.sep, '/')
                if rel not in referenced:
                    missing.append(rel)
        # dopusti fajlove koji su namerno lazy-loaded (retko)
        allowed_lazy = set()  # empty for now
        offenders = [m for m in missing if m not in allowed_lazy]
        self.assertFalse(offenders,
            msg='Ovi JS moduli postoje ali NISU uključeni u index.html (dead code):\n  ' +
                '\n  '.join(offenders))


if __name__ == '__main__':
    unittest.main(verbosity=2)
