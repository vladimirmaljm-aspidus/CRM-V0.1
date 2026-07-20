"""Outbound chat webhooks — Slack + Microsoft Teams.

Both platforms accept a simple JSON POST to a per-channel incoming
webhook URL configured in their admin. This module posts consistent
messages on interesting events (offer accepted, KYC submitted,
document signed, sanctions flag, deal created).

Never blocks the main flow — every send is best-effort, network
failures are logged but the caller does not see them.

Config source is settings.chatWebhooks:
    {
        "slack": "https://hooks.slack.com/services/T00/B00/xxxx",
        "teams": "https://outlook.office.com/webhook/...",
        "events": ["offer_accepted","kyc_submitted","document_signed",
                    "sanctions_flag","deal_created","offer_declined"]
    }
Only events listed in `events` array are delivered — admin can pick a
subset (e.g. only sanctions_flag) via the Settings UI.
"""
import json
import logging
import sqlite3
import threading
import urllib.request

from config import DB_FILE
from utils import decrypt_data

logger = logging.getLogger(__name__)

# Per-process cache of settings — reloaded on first miss or after 60s.
_CFG_CACHE = {'ts': 0, 'data': None}
_CFG_TTL_S = 60


def _load_config():
    """Čita chatWebhooks blob iz settings tabele. Dekriptuje ako je enkriptovan."""
    import time
    now = time.time()
    if _CFG_CACHE['data'] is not None and (now - _CFG_CACHE['ts']) < _CFG_TTL_S:
        return _CFG_CACHE['data']
    cfg = {}
    try:
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='chatWebhooks'")
            row = c.fetchone()
            if row and row[0]:
                try:
                    cfg = decrypt_data(row[0]) or {}
                except Exception:
                    try: cfg = json.loads(row[0])
                    except Exception: cfg = {}
                if not isinstance(cfg, dict): cfg = {}
    except Exception:
        pass
    _CFG_CACHE['data'] = cfg
    _CFG_CACHE['ts'] = now
    return cfg


def clear_cache():
    """Poziva se posle Settings save — invalidira cache pa sledeći send učita svežu konfig."""
    _CFG_CACHE['data'] = None
    _CFG_CACHE['ts'] = 0


def _post(url, payload, timeout=5):
    """POST JSON. Vraća (ok, http_status). Tihi na failure."""
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={
            'Content-Type': 'application/json',
            'User-Agent': 'AspidusCRM/1.0 webhooks',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (200 <= r.status < 300, r.status)
    except Exception as e:
        logger.warning('webhook POST failed for %s: %s', url[:60], e)
        return (False, 0)


def _format_slack(event, ctx):
    """Slack Block Kit format — profesionalan izgled, ne obični tekst."""
    title, color, emoji = _event_style(event)
    fields = []
    for k, v in (ctx or {}).items():
        if v in (None, ''): continue
        fields.append({'title': str(k), 'value': str(v)[:300], 'short': len(str(v)) < 40})
    return {
        'attachments': [{
            'color': color,
            'title': f'{emoji} {title}',
            'fields': fields,
            'footer': 'Aspidus CRM',
            'ts': int(__import__('time').time()),
        }]
    }


def _format_teams(event, ctx):
    """MS Teams MessageCard (legacy connector) format — support-uje sve tenante
    bez novog Adaptive Card upgrade path-a."""
    title, color_hex, emoji = _event_style(event)
    facts = [{'name': str(k), 'value': str(v)[:300]}
             for k, v in (ctx or {}).items() if v not in (None, '')]
    return {
        '@type': 'MessageCard',
        '@context': 'https://schema.org/extensions',
        'themeColor': color_hex.replace('#', ''),
        'title': f'{emoji} {title}',
        'sections': [{'facts': facts, 'markdown': True}],
    }


def _event_style(event):
    """Boja + naslov + emoji po tipu događaja."""
    styles = {
        'offer_accepted':  ('Offer accepted by client',  '#059669', '✅'),
        'offer_declined':  ('Offer declined by client',  '#dc2626', '❌'),
        'kyc_submitted':   ('KYC submission received',   '#0284c7', '📋'),
        'document_signed': ('Document e-signed by client', '#7c3aed', '🖋'),
        'sanctions_flag':  ('SANCTIONS MATCH — admin review required', '#dc2626', '🚨'),
        'deal_created':    ('New deal created',          '#0284c7', '🤝'),
        'partner_created': ('New partner added',         '#0284c7', '👥'),
    }
    return styles.get(event, (event.replace('_', ' ').title(), '#6b7280', '📌'))


def notify(event, context=None):
    """Public API — pošalji obaveštenje o događaju na sve konfigurisane webhook-e.
    Nikad ne diže — greške samo idu u log. Slanje se dešava u background thread-u
    da bi glavni HTTP odgovor bio brz."""
    def _worker():
        cfg = _load_config()
        allowed = cfg.get('events') or []
        # Prazna lista == sve default-uključeno; podskup ako je admin izabrao
        if allowed and event not in allowed:
            return
        slack_url = (cfg.get('slack') or '').strip()
        teams_url = (cfg.get('teams') or '').strip()
        if slack_url.startswith('https://hooks.slack.com/'):
            _post(slack_url, _format_slack(event, context))
        if teams_url.startswith('http'):
            _post(teams_url, _format_teams(event, context))

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception as e:
        logger.warning('webhook thread start failed: %s', e)
