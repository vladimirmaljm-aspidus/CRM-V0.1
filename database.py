import os
import uuid
import sqlite3
import logging
from config import DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE

# Postavljanje logera za bazu
logger = logging.getLogger(__name__)

def seed_admin_if_empty(cursor):
    """Ako u bazi NEMA nijednog korisnika (npr. sveža/prazna baza na produkciji
    gde .db fajlovi nisu deployovani), kreira početnog administratora kako korisnik
    ne bi ostao zaključan van sistema (uzrok 'auth_error' na praznoj bazi).

    Kredencijali se uzimaju iz env-a ADMIN_USERNAME / ADMIN_PASSWORD; ako nisu
    postavljeni, koristi se podrazumevani nalog uz glasno upozorenje da se odmah
    promeni lozinka. NE dira postojeće korisnike."""
    try:
        from werkzeug.security import generate_password_hash
        count = cursor.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        if count and count > 0:
            return
        username = (os.getenv('ADMIN_USERNAME') or 'admin').strip()
        password = os.getenv('ADMIN_PASSWORD') or 'Admin12345'
        pw_hash = generate_password_hash(password, method='scrypt:32768:8:1')
        cursor.execute(
            'INSERT INTO users (id, username, password, role, permissions) VALUES (?, ?, ?, ?, ?)',
            (str(uuid.uuid4()), username, pw_hash, 'admin', '{}')
        )
        if os.getenv('ADMIN_PASSWORD'):
            logger.warning(f"SEED: kreiran početni administrator '{username}' (lozinka iz ADMIN_PASSWORD env-a).")
        else:
            logger.warning("=" * 70)
            logger.warning(f"SEED: baza je bila prazna — kreiran administrator '{username}' / 'Admin12345'.")
            logger.warning("ODMAH se prijavite i promenite lozinku (Moj Profil), ili postavite env ADMIN_PASSWORD.")
            logger.warning("=" * 70)
    except Exception as e:
        logger.error(f"CRITICAL: seed_admin_if_empty nije uspeo - {e}")

def init_db():
    # 1. GLAVNA CRM BAZA
    try:
        # Korišćenje 'with' osigurava da se konekcija uvek bezbedno zatvori i izbegne "database is locked"
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;') # Ubrzava upise drastično
            conn.execute('PRAGMA foreign_keys=ON;')
            c = conn.cursor()
            
            c.execute('''CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT, permissions TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')

            # MIGRACIJA: lični potpis po korisniku (svako koristi samo svoj potpis na dokumentima).
            cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
            if 'signature' not in cols:
                c.execute('ALTER TABLE users ADD COLUMN signature TEXT')
            # MIGRACIJA: token_version — broj koji se povećava pri izmeni lozinke ili
            # ručnom odjavi svih sesija; svaki request u login_required proverava da
            # sesija (session.token_version) odgovara aktuelnoj vrednosti korisnika.
            # Ovim promena lozinke odmah izbacuje SVE ranije otvorene sesije.
            if 'token_version' not in cols:
                c.execute("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 1")
            # MIGRACIJA: last_password_change_at + last_login_country — telemetrija za
            # anomaly detekciju (npr. iznenadna prijava iz druge zemlje).
            if 'last_password_change_at' not in cols:
                c.execute("ALTER TABLE users ADD COLUMN last_password_change_at TEXT")
            if 'last_login_country' not in cols:
                c.execute("ALTER TABLE users ADD COLUMN last_login_country TEXT")
            
            # Kreiranje tabela za sve entitete
            tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
            for table in tables:
                c.execute(f'''CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT)''')

            # DOCUMENT REGISTER — trajni zapis SVIH izdatih dokumentacionih brojeva
            # sa strogim UNIQUE constraint-om koji sprečava dupliranje. Broj se
            # rezerviše atomično čim admin klikne "Pošalji". Nikad se ne briše.
            c.execute('''CREATE TABLE IF NOT EXISTS document_register (
                docType TEXT NOT NULL,
                year INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                docNumber TEXT NOT NULL,
                entityId TEXT,
                revision INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                issuedAt TEXT NOT NULL,
                issuedBy TEXT,
                PRIMARY KEY (docType, year, seq, revision)
            )''')
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_docreg_number ON document_register(docNumber)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_docreg_entity ON document_register(entityId)')

            # DOCUMENT REVISIONS — svaka izmena izdatog dokumenta se snima kao
            # potpun snapshot podataka + hash + reason. Ovim se za bilo koji broj
            # dokumenta u istoriji može rekonstruisati SVAKA verzija.
            c.execute('''CREATE TABLE IF NOT EXISTS document_revisions (
                id TEXT PRIMARY KEY,
                docNumber TEXT NOT NULL,
                revision INTEGER NOT NULL,
                entityId TEXT,
                snapshot TEXT NOT NULL,
                contentHash TEXT,
                bindingHash TEXT,
                changeReason TEXT,
                changedBy TEXT,
                changedAt TEXT NOT NULL
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_docrev_number ON document_revisions(docNumber)')

            # Ako je baza prazna (nema korisnika), kreiraj početnog admina da se izbegne
            # zaključavanje van sistema (npr. sveža baza na produkciji).
            seed_admin_if_empty(c)

            conn.commit()
    except Exception as e:
        logger.error(f"CRITICAL: Greška pri inicijalizaciji glavne baze - {e}")

    # 2. B2B PORTAL BAZA
    try:
        with sqlite3.connect(PORTAL_DB_FILE, timeout=30.0) as conn2:
            conn2.execute('PRAGMA journal_mode=WAL;')
            conn2.execute('PRAGMA synchronous=NORMAL;')
            c2 = conn2.cursor()
            
            # Kreiranje tabela
            c2.execute('''CREATE TABLE IF NOT EXISTS kyc_submissions (id TEXT PRIMARY KEY, partner_id TEXT, token TEXT, data TEXT, submitted_at TEXT)''')
            c2.execute('''CREATE TABLE IF NOT EXISTS portal_products (id TEXT PRIMARY KEY, partner_id TEXT, data TEXT, status TEXT, created_at TEXT)''')
            # Zahtevi partnera za izmenu sopstvenih podataka (email, telefon, adresa...).
            # Ne primenjuju se direktno — admin ih odobrava, tek onda idu u partner profil.
            c2.execute('''CREATE TABLE IF NOT EXISTS profile_change_requests (id TEXT PRIMARY KEY, partner_id TEXT, data TEXT, status TEXT, submitted_at TEXT, reviewed_at TEXT, reviewed_by TEXT)''')
            
            # KREIRANJE INDEKSA (Ključno za optimizaciju i brzinu kada sistem ima mnogo upita)
            c2.execute('''CREATE INDEX IF NOT EXISTS idx_kyc_token ON kyc_submissions(token)''')
            c2.execute('''CREATE INDEX IF NOT EXISTS idx_kyc_partner ON kyc_submissions(partner_id)''')
            c2.execute('''CREATE INDEX IF NOT EXISTS idx_portal_products_partner ON portal_products(partner_id)''')
            
            conn2.commit()
    except Exception as e:
        logger.error(f"CRITICAL: Greška pri inicijalizaciji portal baze - {e}")

    # 3. VOJNA AUDIT BAZA
    try:
        with sqlite3.connect(AUDIT_DB_FILE, timeout=30.0) as conn3:
            conn3.execute('PRAGMA journal_mode=WAL;')
            conn3.execute('PRAGMA synchronous=NORMAL;')
            c3 = conn3.cursor()
            
            # Kreiranje tabele
            c3.execute('''CREATE TABLE IF NOT EXISTS audit_logs 
                         (id TEXT PRIMARY KEY, user_id TEXT, username TEXT, action TEXT, module TEXT, details TEXT, ip_address TEXT, user_agent TEXT, timestamp TEXT, is_suspicious BOOLEAN, location TEXT)''')
            
            # Indeksi za ekstremno brzo učitavanje i filtriranje audit logova
            c3.execute('''CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp)''')
            c3.execute('''CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(username)''')
            c3.execute('''CREATE INDEX IF NOT EXISTS idx_audit_suspicious ON audit_logs(is_suspicious)''')
            
            conn3.commit()
    except Exception as e:
        logger.error(f"CRITICAL: Greška pri inicijalizaciji audit baze - {e}")