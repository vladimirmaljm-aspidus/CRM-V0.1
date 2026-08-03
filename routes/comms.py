import smtplib
import socket
import base64
from flask import Blueprint, request, jsonify, session
from utils import log_audit, login_required, decrypt_data
from utils_email import send_branded_admin_message
import supabase_store as store

comms_bp = Blueprint('comms', __name__)

def get_smtp_settings():
    """Bezbedno dohvata i dešifruje SMTP lozinke iz settings tabele (Supabase)."""
    try:
        raw = store.get_setting('comms_settings')
    except Exception as e:
        print("Upozorenje: Greška pri čitanju comms_settings iz Supabase:", e)
        return None
    if not raw:
        return None
    try:
        settings = decrypt_data(raw)
        if not isinstance(settings, dict):
            return None
        return settings
    except Exception as e:
        print("Upozorenje: Greška pri dešifrovanju SMTP podataka:", e)
        return None

@comms_bp.route('/api/comms/send_email', methods=['POST'])
@login_required
def send_email():
    """Šalje mejl klijentu iz admin 'Send Email' modala. Koristi ISTI brendovan
    HTML šablon i confidentiality footer kao portal notifikacije — ranije je
    admin mejl bio običan tekst bez branding-a što je izgledalo neprofesionalno
    naspram automatskih portal poruka."""
    data = request.json or {}
    recipient = (data.get('to') or '').strip()
    subject = data.get('subject') or '(no subject)'
    body = data.get('body') or ''
    attachment_b64 = data.get('attachment_b64')
    filename = data.get('filename', 'document.pdf')

    if not recipient:
        return jsonify({"error": "api.recipientRequired"}), 400

    attachments = None
    if attachment_b64:
        try:
            raw = attachment_b64.split(',', 1)[1] if ',' in attachment_b64 else attachment_b64
            attachments = [{'filename': filename, 'data': base64.b64decode(raw)}]
        except Exception as e:
            return jsonify({"error": f"Invalid attachment: {e}"}), 400

    ok, err = send_branded_admin_message(recipient, subject, body, attachments=attachments)
    if ok:
        log_audit('COMMUNICATION', 'email', f'Sent email to {recipient} with subject: {subject}')
        return jsonify({"status": "success"})

    # Map internal error codes back to translation keys the frontend already knows.
    err_map = {
        'SMTP_NOT_CONFIGURED': ('api.smtpNotConfigured', 400),
        'SMTP_INCOMPLETE_OR_NO_RECIPIENT': ('api.smtpIncomplete', 400),
    }
    if err in err_map:
        code, http = err_map[err]
        return jsonify({"error": code}), http
    # Detect common SMTP failure classes from the raw message string
    low = (err or '').lower()
    if 'authentication' in low or 'auth' in low and 'fail' in low:
        return jsonify({"error": "api.smtpAuthError"}), 400
    if 'timeout' in low or 'timed out' in low:
        return jsonify({"error": "api.smtpTimeoutError"}), 400
    log_audit('ERROR', 'email', f'Failed to send email to {recipient}: {err}', is_suspicious=True)
    return jsonify({"error": f"SMTP Error: {err}"}), 500

@comms_bp.route('/api/comms/test_smtp', methods=['POST'])
@login_required
def test_smtp():
    """Ruta isključivo za testiranje konekcije. Prepoznaje tačan razlog pada."""
    if session.get('role') != 'admin':
        return jsonify({"error": "api.unauthorized"}), 403
        
    data = request.json
    smtp_server = data.get('smtpServer')
    smtp_port = int(data.get('smtpPort', 587))
    smtp_user = data.get('smtpUser')
    smtp_pass = data.get('smtpPass')
    smtp_security = data.get('smtpSecurity', 'tls')
    
    if not all([smtp_server, smtp_port, smtp_user, smtp_pass]):
        return jsonify({"error": "api.smtpIncomplete"}), 400
        
    try:
        # Povezivanje zavisno od bezbednosnog protokola
        if smtp_security == 'ssl' or smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            if smtp_security != 'none':
                server.starttls()
                
        server.login(smtp_user, smtp_pass)
        server.quit()
        
        log_audit('SECURITY', 'system', 'Admin je uspešno testirao SMTP konekciju.')
        return jsonify({"status": "success", "message": "api.smtpSuccess"})
        
    except smtplib.SMTPAuthenticationError:
        return jsonify({"error": "api.smtpAuthError"}), 400
    except (socket.gaierror, socket.timeout, TimeoutError):
        return jsonify({"error": "api.smtpTimeoutError"}), 400
    except smtplib.SMTPConnectError:
        return jsonify({"error": "api.smtpConnectError"}), 400
    except Exception as e:
        log_audit('ERROR', 'system', f'SMTP Test nije uspeo (Ostalo): {str(e)}')
        return jsonify({"error": f"SMTP Error: {str(e)}"}), 400

@comms_bp.route('/api/comms/email_queue', methods=['GET'])
@login_required
def email_queue_view():
    """Admin pregled pending/failed mejlova sa mogucnoscu manuelnog retry-ja."""
    if session.get('role') != 'admin':
        return jsonify({"error": "UNAUTHORIZED"}), 403
    # V24.0 SUPABASE-ONLY: čita email_queue preko data_layer.select.
    # Tabela već postoji na Supabase (definisana u schemas/supabase_v23_1.sql).
    try:
        from data_layer import select as _dl_select
        rows = _dl_select('email_queue', order='-queued_at', limit=200) or []
        return jsonify({
            'items': [{
                'id': r.get('id'),
                'recipient': r.get('recipient'),
                'subject': r.get('subject'),
                'attempts': r.get('attempts'),
                'lastError': r.get('last_error'),
                'queuedAt': r.get('queued_at'),
                'nextRetryAt': r.get('next_retry_at'),
                'status': r.get('status'),
            } for r in rows]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@comms_bp.route('/api/comms/email_queue/retry_now', methods=['POST'])
@login_required
def email_queue_retry_now():
    """Manuelni trigger — pokreni retry čitave queue odmah."""
    if session.get('role') != 'admin':
        return jsonify({"error": "UNAUTHORIZED"}), 403
    from utils_email import process_email_queue
    stats = process_email_queue(max_batch=100)
    return jsonify(stats)
