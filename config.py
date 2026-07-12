import os
import secrets
from cryptography.fernet import Fernet

# Apsolutna putanja do glavnog direktorijuma projekta
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# ==========================================================
# SECRET KEY
# ==========================================================

INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

SECRET_KEY_FILE = os.path.join(INSTANCE_DIR, "secret.key")

# Prioritet:
# 1. Environment variable
# 2. Sačuvani ključ
# 3. Automatski generisani ključ

if os.getenv("SECRET_KEY"):
    SECRET_KEY = os.getenv("SECRET_KEY")

elif os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, "r", encoding="utf-8") as f:
        SECRET_KEY = f.read().strip()

else:
    SECRET_KEY = secrets.token_urlsafe(64)

    with open(SECRET_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(SECRET_KEY)

    try:
        os.chmod(SECRET_KEY_FILE, 0o600)
    except Exception:
        pass

# ==========================================================
# ENCRYPTION KEY (FERNET)
# ==========================================================

KEY_FILE = os.path.join(BASE_DIR, "vault.key")

if not os.path.exists(KEY_FILE):
    key = Fernet.generate_key()

    with open(KEY_FILE, "wb") as f:
        f.write(key)

    try:
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        pass

with open(KEY_FILE, "rb") as f:
    ENCRYPTION_KEY = f.read()

# ==========================================================
# DATABASES
# ==========================================================

DB_FILE = os.path.join(BASE_DIR, "aspidus_crm.db")

PORTAL_DB_FILE = os.path.join(BASE_DIR, "aspidus_portal.db")

AUDIT_DB_FILE = os.path.join(BASE_DIR, "aspidus_audit.db")

# ==========================================================
# STORAGE
# ==========================================================

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

PORTAL_UPLOAD_FOLDER = os.path.join(BASE_DIR, "portal_uploads")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PORTAL_UPLOAD_FOLDER, exist_ok=True)

# ==========================================================
# UPLOAD LIMITS
# ==========================================================

MAX_CONTENT_LENGTH = 100 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "csv",
    "json",
    "txt",
    "doc",
    "docx",
    "xls",
    "xlsx",
}