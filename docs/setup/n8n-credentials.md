# n8n Credentials Setup — Google OAuth + Zoom

**One-time human setup.** Felix cannot complete OAuth flows on your behalf.  
Run `scripts/n8n_check_credentials.py` after each step to verify the credential is active.

---

## Prerequisites

- n8n is running at http://localhost:5678 (see [SETUP.md](../../SETUP.md))
- You have a Google account with access to Google Cloud Console
- You have a Zoom account with access to Zoom Marketplace

---

## Part 1 — Google OAuth credential

Google OAuth covers all Workspace services: Gmail, Calendar, Drive, Docs, Sheets,
Slides, Contacts, Maps, and Tasks. You only configure it once.

### Step 1 — Create a Google Cloud project

1. Go to https://console.cloud.google.com
2. Create a new project (or reuse an existing one): **OpenMind** is a good name
3. Note the **Project ID**

### Step 2 — Enable APIs

In your project, go to **APIs & Services → Library** and enable:

- Google Calendar API
- Gmail API
- Google Drive API
- Google Docs API
- Google Sheets API
- Google Slides API
- People API (for Contacts)
- Maps JavaScript API (or Geocoding API for server-side)
- Tasks API

### Step 3 — Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** (works for personal accounts; switch to Internal if using Google Workspace)
3. Fill in:
   - App name: `OpenMind`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue** through Scopes (add scopes later if required)
5. Add your own Google account as a **Test user**
6. Click **Back to Dashboard**

### Step 4 — Create OAuth 2.0 credentials

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Application type: **Web application**
3. Name: `OpenMind n8n`
4. Authorised redirect URIs — add:
   ```
   http://localhost:5678/rest/oauth2-credential/callback
   ```
5. Click **Create**
6. Copy the **Client ID** and **Client secret** — you'll paste them into n8n

### Step 5 — Add the credential in n8n

1. Open http://localhost:5678
2. Go to **Credentials → Add Credential**
3. Search for **Google OAuth2 API** and select it
4. Paste your **Client ID** and **Client Secret**
5. Click **Sign in with Google** and complete the OAuth flow in the popup
6. Name the credential: `My Google Account`
7. Click **Save**

> n8n stores the credential in its local encrypted vault (`~/.n8n`). It is never written to the Felix codebase or any environment file.

---

## Part 2 — Zoom OAuth credential

### Step 1 — Create a Zoom OAuth app

1. Go to https://marketplace.zoom.us/develop/create
2. Choose **OAuth** as the app type
3. Choose **User-managed** (not Account-level) for personal use
4. Name the app: `OpenMind`
5. Toggle **Publish**: leave OFF (this stays private)

### Step 2 — Configure the Zoom app

1. In the app's **App Credentials** tab, note the **Client ID** and **Client Secret**
2. Set the **Redirect URL for OAuth**:
   ```
   http://localhost:5678/rest/oauth2-credential/callback
   ```
3. In **Scopes**, add:
   - `meeting:read`
   - `meeting:write`
   - `user:read`
4. Click **Save**

### Step 3 — Add the credential in n8n

1. Open http://localhost:5678
2. Go to **Credentials → Add Credential**
3. Search for **Zoom OAuth2 API** and select it
4. Paste your **Client ID** and **Client Secret**
5. Click **Connect** and complete the Zoom OAuth flow
6. Name the credential: `My Zoom Account`
7. Click **Save**

---

## Verify all credentials

Run the health-check script from the repo root:

```bash
python scripts/n8n_check_credentials.py
```

Expected output when both are configured:

```
n8n credentials check — 2 credential(s) found
  [✓] My Google Account  (googleOAuth2Api)
  [✓] My Zoom Account  (zoomOAuth2Api)

All required credentials are configured.
```

If a credential is missing, the script lists it and exits with code 1.

Felix can also call the check programmatically:

```python
from scripts.n8n_check_credentials import check_credentials

result = await check_credentials()
# {"ok": True, "credentials": [...], "missing": []}
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Could not reach n8n` | Start n8n: `n8n start` or `docker start n8n` |
| Google sign-in popup blocked | Allow popups for localhost:5678 in your browser |
| `redirect_uri_mismatch` error | Ensure the redirect URI in Google Cloud Console matches exactly: `http://localhost:5678/rest/oauth2-credential/callback` |
| Zoom `invalid_client` error | Double-check Client ID and Secret; regenerate if needed |
| Credential shows but test workflow fails | Re-authorise: open the credential in n8n → click **Reconnect** |

---

## Security notes

- Credentials are stored in n8n's local encrypted vault at `~/.n8n` (Windows: `%USERPROFILE%\.n8n`)
- They are never written to the OpenMind codebase, `.env` files, or any version-controlled file
- The Google OAuth app remains in **Testing** mode; only accounts you listed as test users can authorise it
- For production use, submit your Google app for verification (optional for personal/home use)
