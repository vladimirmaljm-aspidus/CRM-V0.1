# Supabase Email Templates — za copy/paste

Idi u **Authentication → Email Templates**. Postoji 6 template-a; menjamo 4 koja klijenti vide.

Za sve: **Sender name** postavi na `Aspidus` (ili tvoj naziv firme), **Sender email** ostavi Supabase default (`noreply@mail.app.supabase.io`) — kasnije u Fazi 3 povezaćemo custom SMTP ako želiš (postmark tunel već imaš).

Placeholder-i koje Supabase koristi (ne diraj ih):
- `{{ .ConfirmationURL }}` — link za akciju
- `{{ .Email }}` — email primaoca
- `{{ .Token }}` — 6-cifreni OTP kod (magic link fallback)

---

## 1. Confirm Signup (nakon novog registracije)

**Subject:**
```
Welcome to Aspidus — verify your email
```

**Body (HTML):**
```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body{margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1e293b}
  .container{max-width:560px;margin:0 auto;padding:40px 24px}
  .card{background:#fff;border-radius:16px;padding:40px;box-shadow:0 4px 24px rgba(0,0,0,0.06);border:1px solid #e2e8f0}
  h1{margin:0 0 16px;font-size:24px;font-weight:800;letter-spacing:-0.02em;color:#0f172a}
  p{margin:0 0 16px;font-size:15px;line-height:1.6;color:#475569}
  .btn{display:inline-block;background:#0f172a;color:#fff !important;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:0.02em;margin:24px 0}
  .footer{margin-top:32px;padding-top:24px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}
  .brand{font-weight:900;font-size:20px;letter-spacing:0.05em;color:#0f172a;margin-bottom:24px}
</style>
</head>
<body>
  <div class="container">
    <div class="brand">ASPIDUS</div>
    <div class="card">
      <h1>Verify your email</h1>
      <p>Welcome to the Aspidus B2B Portal. Click the button below to confirm your email address and activate your access.</p>
      <a class="btn" href="{{ .ConfirmationURL }}">Verify email address</a>
      <p style="font-size:13px;color:#64748b">This link expires in 24 hours. If you didn't request this, you can safely ignore this email.</p>
      <div class="footer">
        Aspidus Trading &middot; Confidential — do not forward.<br>
        Need help? Reply to this email.
      </div>
    </div>
  </div>
</body>
</html>
```

---

## 2. Magic Link (klijent bira "email me a login link")

**Subject:**
```
Your Aspidus sign-in link
```

**Body (HTML):**
```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body{margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1e293b}
  .container{max-width:560px;margin:0 auto;padding:40px 24px}
  .card{background:#fff;border-radius:16px;padding:40px;box-shadow:0 4px 24px rgba(0,0,0,0.06);border:1px solid #e2e8f0}
  h1{margin:0 0 16px;font-size:24px;font-weight:800;letter-spacing:-0.02em;color:#0f172a}
  p{margin:0 0 16px;font-size:15px;line-height:1.6;color:#475569}
  .btn{display:inline-block;background:#0f172a;color:#fff !important;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:0.02em;margin:24px 0}
  .code{display:inline-block;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;padding:10px 16px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:20px;font-weight:700;letter-spacing:0.15em;color:#0f172a;margin:16px 0}
  .footer{margin-top:32px;padding-top:24px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}
  .brand{font-weight:900;font-size:20px;letter-spacing:0.05em;color:#0f172a;margin-bottom:24px}
</style>
</head>
<body>
  <div class="container">
    <div class="brand">ASPIDUS</div>
    <div class="card">
      <h1>Sign in with one click</h1>
      <p>You requested a sign-in link. Click the button below to access your portal — no password required for this session.</p>
      <a class="btn" href="{{ .ConfirmationURL }}">Sign in to Aspidus Portal</a>
      <p style="font-size:13px;color:#64748b">Prefer to enter a code? Use this instead:</p>
      <div class="code">{{ .Token }}</div>
      <p style="font-size:13px;color:#64748b">This link and code expire in 10 minutes. If you didn't request this, ignore this email — your account is safe.</p>
      <div class="footer">
        Aspidus Trading &middot; Confidential — do not forward.<br>
        Sent from a secure IP. If suspicious, contact your account manager.
      </div>
    </div>
  </div>
</body>
</html>
```

---

## 3. Reset Password (klijent klikne "Forgot password")

**Subject:**
```
Reset your Aspidus password
```

**Body (HTML):**
```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body{margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1e293b}
  .container{max-width:560px;margin:0 auto;padding:40px 24px}
  .card{background:#fff;border-radius:16px;padding:40px;box-shadow:0 4px 24px rgba(0,0,0,0.06);border:1px solid #e2e8f0}
  h1{margin:0 0 16px;font-size:24px;font-weight:800;letter-spacing:-0.02em;color:#0f172a}
  p{margin:0 0 16px;font-size:15px;line-height:1.6;color:#475569}
  .btn{display:inline-block;background:#0f172a;color:#fff !important;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:0.02em;margin:24px 0}
  .footer{margin-top:32px;padding-top:24px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}
  .brand{font-weight:900;font-size:20px;letter-spacing:0.05em;color:#0f172a;margin-bottom:24px}
</style>
</head>
<body>
  <div class="container">
    <div class="brand">ASPIDUS</div>
    <div class="card">
      <h1>Reset your password</h1>
      <p>We received a request to reset your Aspidus Portal password. Click the button below to choose a new one.</p>
      <a class="btn" href="{{ .ConfirmationURL }}">Set a new password</a>
      <p style="font-size:13px;color:#64748b">This link expires in 1 hour. If you didn't request this, ignore this email — your password remains unchanged.</p>
      <div class="footer">
        Aspidus Trading &middot; Confidential — do not forward.<br>
        For your security, do not share this link with anyone.
      </div>
    </div>
  </div>
</body>
</html>
```

---

## 4. Invite User (koristimo za migraciju POSTOJEĆIH klijenata — Faza 1)

Ovo se šalje kada admin doda novog klijenta ILI kada Fazu 1 migriramo postojeće klijente iz SQLite-a.

**Subject:**
```
You've been invited to the Aspidus B2B Portal
```

**Body (HTML):**
```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body{margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1e293b}
  .container{max-width:560px;margin:0 auto;padding:40px 24px}
  .card{background:#fff;border-radius:16px;padding:40px;box-shadow:0 4px 24px rgba(0,0,0,0.06);border:1px solid #e2e8f0}
  h1{margin:0 0 16px;font-size:24px;font-weight:800;letter-spacing:-0.02em;color:#0f172a}
  p{margin:0 0 16px;font-size:15px;line-height:1.6;color:#475569}
  .btn{display:inline-block;background:#0f172a;color:#fff !important;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:0.02em;margin:24px 0}
  .footer{margin-top:32px;padding-top:24px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8}
  .brand{font-weight:900;font-size:20px;letter-spacing:0.05em;color:#0f172a;margin-bottom:24px}
  .hi{background:#f1f5f9;border-radius:10px;padding:16px;font-size:13px;color:#475569;margin:16px 0}
</style>
</head>
<body>
  <div class="container">
    <div class="brand">ASPIDUS</div>
    <div class="card">
      <h1>Welcome to Aspidus B2B Portal</h1>
      <p>Your account has been created. Click below to set your password and access your dashboard.</p>
      <a class="btn" href="{{ .ConfirmationURL }}">Set your password</a>
      <div class="hi">
        <strong>What's in your portal:</strong><br>
        · View offers and shared documents<br>
        · Track shipments and RFQs<br>
        · Manage your KYC and company details<br>
        · Chat directly with your account manager
      </div>
      <p style="font-size:13px;color:#64748b">This invitation is valid for 7 days. If you have questions, reply to this email — your account manager will get back to you.</p>
      <div class="footer">
        Aspidus Trading &middot; Confidential — do not forward.<br>
        Invited by your Aspidus account manager.
      </div>
    </div>
  </div>
</body>
</html>
```

---

## Šta NE menjamo

Ostavi Supabase default za:
- **Change Email Address** (ove edge-case-ove ćemo prilagoditi u Fazi 3 ako zatreba).

---

## Kako testirati email-ove

Idi u **Authentication → Users → Invite user** → unesi svoj email → dobićeš pravi email da vidiš kako izgleda. Nemoj brinuti, neće ništa pokvariti — user možeš da obrišeš odmah posle.
