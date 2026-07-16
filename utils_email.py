"""Profesionalno slanje mejlova sa HTML šablonima i confidentiality footer-om.

Centralizovano zbog konzistentnog brendinga i pravnih napomena. Svaki mejl
poslat iz sistema koristi ISTU strukturu: header sa firmom → poruka → footer
sa confidentiality disclaimerom. Ako SMTP nije podešen ili poziv padne,
funkcija se blago vraća bez izuzetka i loguje razlog u audit."""

import base64
import logging
import smtplib
import sqlite3
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import DB_FILE
from utils import decrypt_data, log_audit

logger = logging.getLogger(__name__)


def _get_smtp_settings():
    """Učitava SMTP kredencijale iz baze (comms_settings) i podatke firme."""
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='comms_settings'")
            smtp_row = c.fetchone()
            c.execute("SELECT value FROM settings WHERE key='company'")
            comp_row = c.fetchone()
    except Exception:
        return None, None
    smtp = decrypt_data(smtp_row[0]) if smtp_row and smtp_row[0] else None
    company = decrypt_data(comp_row[0]) if comp_row and comp_row[0] else None
    return (smtp if isinstance(smtp, dict) else None), (company if isinstance(company, dict) else None)


def _brand_pieces(company):
    """Vraća (naziv, adresa, taxId, sender_name, sender_email_hint) — sve
    kao string podesan za HTML."""
    company = company or {}
    return {
        "name": company.get("name") or "Aspidus",
        "address": (company.get("address") or "").replace("\n", ", "),
        "taxId": company.get("taxId") or "",
        "logoUrl": company.get("logoUrl") or "",
        "brandColor": company.get("brandColor") or "#2563eb",
    }


def _html_wrap(subject_line, body_html, company, cta_url=None, cta_label=None):
    """Vraća HTML sa headerom firme, sadržajem i confidentiality footer-om."""
    b = _brand_pieces(company)
    cta_block = ""
    if cta_url and cta_label:
        cta_block = (
            f"<div style='text-align:center;padding:24px 0;'>"
            f"<a href='{cta_url}' style='display:inline-block;background:{b['brandColor']};color:#ffffff;"
            f"padding:14px 30px;border-radius:8px;text-decoration:none;font-weight:600;"
            f"font-family:Arial,sans-serif;font-size:14px;letter-spacing:0.02em;'>"
            f"{cta_label}"
            f"</a></div>"
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{subject_line}</title></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:'Segoe UI',Arial,sans-serif;color:#101828;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f6f9;padding:32px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#ffffff;border:1px solid #e7eaef;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(16,24,40,0.04);">
        <tr>
          <td style="background:{b['brandColor']};padding:24px 32px;text-align:left;">
            <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.02em;">{b['name']}</div>
            <div style="color:rgba(255,255,255,0.7);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.15em;margin-top:4px;">Corporate Communication</div>
          </td>
        </tr>
        <tr>
          <td style="padding:32px;">
            {body_html}
            {cta_block}
          </td>
        </tr>
        <tr>
          <td style="background:#f4f6f9;padding:20px 32px;border-top:1px solid #e7eaef;font-size:11px;color:#667085;line-height:1.6;">
            <p style="margin:0 0 8px 0;font-weight:600;color:#101828;">CONFIDENTIALITY NOTICE</p>
            <p style="margin:0 0 8px 0;">This message and any documents attached are strictly confidential and are intended solely for the addressee named. If you are not the intended recipient, any disclosure, copying, distribution or use of this message is strictly prohibited. Please notify the sender immediately and delete this message.</p>
            <p style="margin:0;font-size:10px;color:#98a2b3;">{b['name']}{(' &middot; ' + b['address']) if b['address'] else ''}{(' &middot; Tax ID: ' + b['taxId']) if b['taxId'] else ''}</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _send(recipient, subject, html_body, plain_body, attachments=None):
    """Šalje mejl. Vraća (ok, error_msg).

    attachments: opciona lista dict-ova [{'filename': str, 'data': bytes}].
    Podržava PDF-ove i druge binarne fajlove — koristi se iz admin-ovog
    'Send Email' modala za slanje ponuda/faktura klijentu.
    """
    smtp, company = _get_smtp_settings()
    if not smtp:
        return False, "SMTP_NOT_CONFIGURED"

    server_host = smtp.get("smtpServer")
    server_port = int(smtp.get("smtpPort") or 587)
    user = smtp.get("smtpUser")
    pw = smtp.get("smtpPass")
    security = smtp.get("smtpSecurity", "tls")
    sender_name = smtp.get("senderName") or (company or {}).get("name") or "Aspidus"
    sender_email = smtp.get("senderEmail") or user

    if not (server_host and user and pw and recipient):
        return False, "SMTP_INCOMPLETE_OR_NO_RECIPIENT"

    # Ako ima priloga → koristimo "mixed" outer + "alternative" za text/html.
    # Ovo je RFC-preporučen redosled za HTML mejl sa attachmentima.
    if attachments:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = recipient
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain_body or subject, "plain", "utf-8"))
        alt.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alt)
        for att in attachments:
            data = att.get("data"); filename = att.get("filename", "document.pdf")
            if not data:
                continue
            part = MIMEApplication(data, Name=filename)
            part["Content-Disposition"] = f'attachment; filename="{filename}"'
            msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = recipient
        msg.attach(MIMEText(plain_body or subject, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if security == "ssl" or server_port == 465:
            server = smtplib.SMTP_SSL(server_host, server_port, timeout=15)
        else:
            server = smtplib.SMTP(server_host, server_port, timeout=15)
            if security != "none":
                server.starttls()
        server.login(user, pw)
        server.send_message(msg)
        server.quit()
        return True, None
    except Exception as e:
        logger.error(f"SMTP send failed to {recipient}: {e}")
        return False, str(e)


# ==========================================================
#  Public helper — koristi se iz admin comms rute
# ==========================================================
def send_branded_admin_message(recipient, subject, message_text, attachments=None):
    """Šalje admin-inicirani mejl (npr. ponuda kupcu iz 'Send Email' modala)
    ISTIM brendovanim šablonom kao portal notifikacije. Ovo je bilo tačka
    inkonzistentnosti — ranije su portal notifikacije bile profesionalne HTML,
    a admin-ovi mejlovi običan tekst bez branding-a.

    message_text: slobodan tekst koji je admin uneo (u modalu). Automatski se
    escape-uje i pretvara u paragrafe (svaka linija = novi paragraf).
    """
    _smtp, company = _get_smtp_settings()

    def _escape(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    paragraphs = [
        f'<p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">{_escape(line)}</p>'
        for line in (message_text or "").split("\n") if line.strip()
    ] or [
        '<p style="margin:0;font-size:14px;color:#344054;">(No message provided)</p>'
    ]
    body_html = "".join(paragraphs)

    html = _html_wrap(subject, body_html, company)
    return _send(recipient, subject, html, message_text or subject, attachments=attachments)


# ==========================================================
#  Konkretni šabloni
# ==========================================================

def send_portal_otp(recipient, company_name_for_client, otp, portal_url):
    """Šalje profesionalan OTP mejl klijentu."""
    _smtp, company = _get_smtp_settings()
    subject = f"[{_brand_pieces(company)['name']}] Your Secure Portal Access Code"

    body = f"""
      <p style="margin:0 0 12px 0;font-size:16px;color:#101828;">Dear {company_name_for_client or 'Client'},</p>
      <p style="margin:0 0 20px 0;font-size:14px;line-height:1.6;color:#344054;">
        You have requested access to the secure B2B client portal. Please use the one-time verification code below to complete your login.
      </p>
      <div style="background:#f4f6f9;border:1px solid #e7eaef;border-radius:10px;padding:24px;text-align:center;margin:20px 0;">
        <div style="font-size:11px;color:#667085;font-weight:600;text-transform:uppercase;letter-spacing:0.15em;margin-bottom:12px;">One-Time Access Code</div>
        <!-- OTP je u <code> tagu — long-press na telefonu ili double-click na desktop-u odabira ceo kod za copy.
             Odvojen sa razmakom radi lakšeg čitanja, ali sirov u samom tekstu je bez razmaka. -->
        <div style="font-family:'Courier New',Consolas,monospace;font-size:38px;font-weight:700;color:#101828;user-select:all;-webkit-user-select:all;">
          <code style="background:#ffffff;border:2px dashed #2563eb;border-radius:8px;padding:8px 16px;display:inline-block;letter-spacing:0.35em;">{otp}</code>
        </div>
        <div style="font-size:12px;color:#475569;margin-top:14px;">Tap or long-press the code to copy it — then paste it into the login screen.</div>
        <div style="font-size:11px;color:#98a2b3;margin-top:8px;">Valid for 5 minutes.</div>
      </div>
      <p style="margin:0 0 8px 0;font-size:13px;color:#344054;">If you did not request this code, please ignore this message. Your account remains secure.</p>
      <p style="margin:0;font-size:13px;color:#344054;">For any questions, please contact your account manager.</p>
    """

    html = _html_wrap(subject, body, company, cta_url=portal_url, cta_label="Return to Portal")
    plain = f"Your one-time access code is: {otp}\nValid for 5 minutes.\n\nIf you did not request this code, please ignore this message."
    return _send(recipient, subject, html, plain)


def send_kyc_approved(recipient, company_name_for_client, portal_url):
    _smtp, company = _get_smtp_settings()
    subject = f"[{_brand_pieces(company)['name']}] KYC Approved"
    body = f"""
      <p style="margin:0 0 12px 0;font-size:16px;color:#101828;">Dear {company_name_for_client or 'Client'},</p>
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">
        We are pleased to inform you that your KYC / compliance submission has been reviewed and <strong style="color:#059669;">approved</strong>.
      </p>
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">
        You now have full access to offers, contracts and document exchange via your B2B portal.
      </p>
    """
    return _send(recipient, subject, _html_wrap(subject, body, company, cta_url=portal_url, cta_label="Open Portal"), f"Your KYC has been approved. Portal: {portal_url}")


def send_kyc_update_requested(recipient, company_name_for_client, portal_url, note=""):
    _smtp, company = _get_smtp_settings()
    subject = f"[{_brand_pieces(company)['name']}] Additional KYC Information Required"
    note_block = ""
    if note:
        note_block = f"<div style='background:#fef3c7;border-left:4px solid #d97706;padding:14px 18px;margin:16px 0;font-size:13px;color:#78350f;'><strong>Reviewer note:</strong><br>{note}</div>"
    body = f"""
      <p style="margin:0 0 12px 0;font-size:16px;color:#101828;">Dear {company_name_for_client or 'Client'},</p>
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">
        We have reviewed your KYC submission and require additional information or updated documents to proceed. Please log in to the portal and complete the requested items.
      </p>
      {note_block}
    """
    return _send(recipient, subject, _html_wrap(subject, body, company, cta_url=portal_url, cta_label="Complete KYC"), "Additional KYC information required.")


def send_new_offer(recipient, company_name_for_client, offer_no, portal_url):
    _smtp, company = _get_smtp_settings()
    subject = f"[{_brand_pieces(company)['name']}] New Offer #{offer_no}"
    body = f"""
      <p style="margin:0 0 12px 0;font-size:16px;color:#101828;">Dear {company_name_for_client or 'Client'},</p>
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">
        A new commercial offer <strong>#{offer_no}</strong> has been prepared for you and is available in your secure B2B portal.
      </p>
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">
        You may review the terms, download the offer PDF, and confirm acceptance directly through the portal.
      </p>
    """
    return _send(recipient, subject, _html_wrap(subject, body, company, cta_url=portal_url, cta_label="View Offer"), f"New offer #{offer_no} available.")


def send_new_document(recipient, company_name_for_client, doc_type, file_name, portal_url):
    _smtp, company = _get_smtp_settings()
    subject = f"[{_brand_pieces(company)['name']}] New Document: {doc_type}"
    body = f"""
      <p style="margin:0 0 12px 0;font-size:16px;color:#101828;">Dear {company_name_for_client or 'Client'},</p>
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">
        A new document is available in your portal document vault:
      </p>
      <div style="background:#f4f6f9;border:1px solid #e7eaef;border-radius:10px;padding:16px;margin:14px 0;">
        <div style="font-size:11px;color:#667085;text-transform:uppercase;letter-spacing:0.1em;font-weight:600;">{doc_type}</div>
        <div style="font-size:15px;color:#101828;margin-top:4px;font-weight:600;">{file_name}</div>
      </div>
      <p style="margin:0;font-size:13px;color:#667085;">Every document download is logged for compliance and audit purposes.</p>
    """
    return _send(recipient, subject, _html_wrap(subject, body, company, cta_url=portal_url, cta_label="Access Document"), f"New document available: {file_name}")


def send_portal_welcome(recipient, company_name_for_client, portal_url):
    _smtp, company = _get_smtp_settings()
    brand = _brand_pieces(company)
    subject = f"[{brand['name']}] Welcome to Your B2B Client Portal"
    body = f"""
      <p style="margin:0 0 12px 0;font-size:16px;color:#101828;">Dear {company_name_for_client or 'Client'},</p>
      <p style="margin:0 0 16px 0;font-size:14px;line-height:1.6;color:#344054;">
        We are pleased to welcome you to our secure B2B client portal. This portal provides you with direct access to manage your business relationship with {brand['name']}.
      </p>

      <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:20px;margin:16px 0;">
        <div style="font-size:12px;color:#0369a1;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px;">Getting Started</div>
        <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;">
          <tr>
            <td style="padding:8px 12px;font-size:13px;color:#344054;line-height:1.5;border-bottom:1px solid #e0f2fe;">
              <strong style="color:#0369a1;">Step 1.</strong> Click the button below to access your portal. A one-time verification code will be sent to this email.
            </td>
          </tr>
          <tr>
            <td style="padding:8px 12px;font-size:13px;color:#344054;line-height:1.5;border-bottom:1px solid #e0f2fe;">
              <strong style="color:#0369a1;">Step 2.</strong> Complete your <strong>KYC (Know Your Customer)</strong> form under the Compliance tab. This is required before we can proceed with offers and contracts.
            </td>
          </tr>
          <tr>
            <td style="padding:8px 12px;font-size:13px;color:#344054;line-height:1.5;border-bottom:1px solid #e0f2fe;">
              <strong style="color:#0369a1;">Step 3.</strong> Upload the required corporate documents: trade license, passports of directors/UBOs, and certificate of incorporation.
            </td>
          </tr>
          <tr>
            <td style="padding:8px 12px;font-size:13px;color:#344054;line-height:1.5;">
              <strong style="color:#0369a1;">Step 4.</strong> Once your KYC is approved, you will have full access to offers, shipment tracking, and document exchange.
            </td>
          </tr>
        </table>
      </div>

      <div style="background:#f4f6f9;border:1px solid #e7eaef;border-radius:10px;padding:16px;margin:16px 0;">
        <div style="font-size:12px;color:#667085;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;">Portal Features</div>
        <ul style="margin:0;padding:0 0 0 18px;font-size:13px;color:#344054;line-height:1.8;">
          <li>View and accept commercial offers</li>
          <li>Track shipments and contracts in real time</li>
          <li>Access and download official documents (offers, invoices, contracts)</li>
          <li>Submit requests for quotation (RFQ)</li>
          <li>Manage your product catalog</li>
          <li>Update your company profile securely</li>
        </ul>
      </div>

      <p style="margin:0 0 8px 0;font-size:13px;color:#344054;">
        All data submitted through the portal is encrypted and stored securely. Document downloads are logged for compliance and audit purposes.
      </p>
      <p style="margin:0;font-size:13px;color:#667085;">
        If you have any questions, please contact your account manager.
      </p>
    """
    html = _html_wrap(subject, body, company, cta_url=portal_url, cta_label="Access Your Portal")
    plain = (
        f"Welcome to {brand['name']} B2B Portal.\n\n"
        f"Your portal link: {portal_url}\n\n"
        "Steps:\n1. Open the link and enter the verification code sent to your email.\n"
        "2. Complete the KYC form under the Compliance tab.\n"
        "3. Upload corporate documents (trade license, passports, incorporation cert).\n"
        "4. Once approved, you'll have full access to offers, tracking, and documents.\n\n"
        "For questions, contact your account manager."
    )
    return _send(recipient, subject, html, plain)


def send_profile_change_approved(recipient, company_name_for_client, changes_summary, portal_url):
    _smtp, company = _get_smtp_settings()
    subject = f"[{_brand_pieces(company)['name']}] Profile Update Confirmed"
    body = f"""
      <p style="margin:0 0 12px 0;font-size:16px;color:#101828;">Dear {company_name_for_client or 'Client'},</p>
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;color:#344054;">
        Your recent profile change request has been reviewed and applied to your account.
      </p>
      <div style="background:#f4f6f9;border:1px solid #e7eaef;border-radius:10px;padding:14px 18px;margin:14px 0;font-size:13px;color:#344054;">
        {changes_summary}
      </div>
    """
    return _send(recipient, subject, _html_wrap(subject, body, company, cta_url=portal_url, cta_label="Open Portal"), "Profile update confirmed.")
