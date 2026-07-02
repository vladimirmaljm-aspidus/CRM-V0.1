import smtplib
import socket
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import sqlite3
import json
import base64
from flask import Blueprint, request, jsonify, session
from config import DB_FILE
from utils import log_audit, login_required, decrypt_data

comms_bp = Blueprint('comms', __name__)

def get_smtp_settings():
    """Bezbedno dohvata i dešifruje SMTP lozinke iz baze."""
    conn = None
    row = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='comms_settings'")
        row = c.fetchone()
    finally:
        if conn: conn.close()

    if not row or not row[0]:
        return None
        
    try:
        settings = decrypt_data(row[0])
        if not isinstance(settings, dict):
            return None
        return settings
    except Exception as e:
        print("Upozorenje: Greška pri dešifrovanju SMTP podataka:", e)
        return None

@comms_bp.route('/api/comms/send_email', methods=['POST'])
@login_required
def send_email():
    data = request.json
    recipient = data.get('to')
    subject = data.get('subject')
    body = data.get('body')
    attachment_b64 = data.get('attachment_b64')
    filename = data.get('filename', 'document.pdf')

    settings = get_smtp_settings()
    
    if not settings:
        return jsonify({"error": "api.smtpNotConfigured"}), 400

    smtp_server = settings.get('smtpServer')
    smtp_port = int(settings.get('smtpPort', 587))
    smtp_user = settings.get('smtpUser')
    smtp_pass = settings.get('smtpPass')
    smtp_security = settings.get('smtpSecurity', 'tls')
    sender_name = settings.get('senderName', 'Aspidus CRM')
    sender_email = settings.get('senderEmail', smtp_user)

    if not all([smtp_server, smtp_port, smtp_user, smtp_pass]):
        return jsonify({"error": "api.smtpIncomplete"}), 400

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{sender_name} <{sender_email}>"
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        if attachment_b64:
            if ',' in attachment_b64:
                attachment_b64 = attachment_b64.split(',')[1]
            pdf_data = base64.b64decode(attachment_b64)
            part = MIMEApplication(pdf_data, Name=filename)
            part['Content-Disposition'] = f'attachment; filename="{filename}"'
            msg.attach(part)

        # Pametan izbor protokola: Port 465 uvek zahteva SSL
        if smtp_security == 'ssl' or smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
            if smtp_security != 'none':
                server.starttls()
                
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()

        log_audit('COMMUNICATION', 'email', f'Sent email to {recipient} with subject: {subject}')
        return jsonify({"status": "success"})

    except smtplib.SMTPAuthenticationError:
        return jsonify({"error": "api.smtpAuthError"}), 400
    except (socket.gaierror, socket.timeout, TimeoutError):
        return jsonify({"error": "api.smtpTimeoutError"}), 400
    except Exception as e:
        log_audit('ERROR', 'email', f'Failed to send email to {recipient}: {str(e)}', is_suspicious=True)
        return jsonify({"error": f"SMTP Error: {str(e)}"}), 500

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