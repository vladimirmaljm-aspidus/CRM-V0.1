import os
from cryptography.fernet import Fernet

# Dobijanje apsolutne putanje do glavnog direktorijuma (sprečava gubljenje baze pri restartu)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY', 'aspidus-pro-secure-vault-x92f8j3pL-2026-vQpZm1!k9')

# --- KREIRANJE VOJNOG TREZORA (FERNET AES-128-CBC KLJUČ) ---
# Ako ključ ne postoji, sistem generiše novi. OVAJ FAJL (vault.key) ČUVAJ NA SIGURNOM!
KEY_FILE = os.path.join(BASE_DIR, 'vault.key')

if not os.path.exists(KEY_FILE):
    # Generisanje i čuvanje novog ključa
    key = Fernet.generate_key()
    with open(KEY_FILE, 'wb') as f:
        f.write(key)
    
    # Sigurnosno zaključavanje fajla na nivou operativnog sistema (samo vlasnik ima pristup)
    try:
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        # Na Windows sistemima chmod možda neće raditi na isti način, ignorišemo grešku
        pass

with open(KEY_FILE, 'rb') as f:
    ENCRYPTION_KEY = f.read()
# -----------------------------------------------

# 1. Glavna baza i folder (Nedostupno klijentima)
DB_FILE = os.path.join(BASE_DIR, 'aspidus_crm.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

# 2. [AIR-GAPPED PORTAL] - Potpuno izolovana baza i folder za B2B Portal i KYC
PORTAL_DB_FILE = os.path.join(BASE_DIR, 'aspidus_portal.db') 
PORTAL_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'portal_uploads')

# 3. [VOJNA AUDIT BAZA] - Izolovana baza samo za nadzor
AUDIT_DB_FILE = os.path.join(BASE_DIR, 'aspidus_audit.db')

# Sigurnosni limiti za fajlove
MAX_CONTENT_LENGTH = 100 * 1024 * 1024 
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'csv', 'json', 'txt', 'doc', 'docx', 'xls', 'xlsx'}

# Kreiranje radnih direktorijuma ako ne postoje
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PORTAL_UPLOAD_FOLDER, exist_ok=True)