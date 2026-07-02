import sqlite3
import logging
from config import DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE

# Postavljanje logera za bazu
logger = logging.getLogger(__name__)

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
            
            # Kreiranje tabela za sve entitete
            tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
            for table in tables:
                c.execute(f'''CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT)''')
                
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