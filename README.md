# UniVault

UniVault is a Flask and SQLite portal for browsing, previewing, downloading, and sharing university study materials. It runs on the host computer and can be exposed to the Internet through a Cloudflare Tunnel.

## Features

- Public material browsing, search, filters, previews, downloads, statistics, and contributor leaderboard.
- Student accounts with uploads, reviews, ratings, upvotes, persistent bookmarks, profile, and account settings.
- Administrator dashboard for users and uploaded materials.
- Responsive academic atlas interface with an animated isometric library illustration.
- Resource-type collections, a keyboard shortcut for search (`/`), and a saved grid or list layout preference.
- SQLite database at data/univault.db; uploaded files stay under static/uploads/.

## Run the website

Install the Python packages once:

```powershell
python -m pip install -r requirements.txt
```

Then, whenever you want to run UniVault, open PowerShell in `A:\Univault-main` and run:

```powershell
python app.py
```

This starts only Flask at `http://127.0.0.1:5000`. It does not start or stop Cloudflare Tunnel, so you can use localhost without Cloudflare installed. Flask generates a random secret once and stores it in `data/.univault_secret_key`, excluded from Git along with the database. The `UNIVAULT_SECRET_KEY` environment variable takes precedence if set. Never commit or share the key.

To expose the running local app publicly, open a second PowerShell window and start the tunnel separately:

```powershell
cloudflared tunnel --url http://127.0.0.1:5000
```

Cloudflare prints the public HTTPS address in that second window. Start and stop the tunnel there independently; closing Flask does not close the tunnel process. For an existing named tunnel, use this instead in the second window:

```powershell
cloudflared tunnel run your-tunnel-name
```

When using the site through HTTPS, enable secure session cookies before starting Flask: `$env:UNIVAULT_HTTPS = "1"`. For localhost-only HTTP use, leave that variable unset.

UniVault runs on the Windows laptop where its SQLite database and uploads are stored. Cloudflare Tunnel forwards public HTTPS traffic to Flask on the laptop; the app is not hosted on Cloudflare.

## Accounts and security

Register from /register; registrations always create students. Sign in at /login. The first administrator is seeded once as Sanjeet, with a Werkzeug scrypt password hash and a forced password change at first sign-in. Its password is never stored as plaintext, logged, rendered, or returned by an API. Later startups do not reset an existing administrator password.

Passwords are stored in users.password_hash using Werkzeug's secure password hashing. Server-side role checks protect student and administrator actions. Browser state is not trusted for authorization. State-changing requests use session CSRF tokens.

## Data safety and migration

The app creates data/ if necessary and always opens the absolute project path data/univault.db. Startup migrations create missing tables and add missing ownership/user columns in place. Existing materials, reviews, contributors, counts, and legacy file data are retained. Existing uploaded files remain in static/uploads/.

Back up data/univault.db and static/uploads/ together before manually changing or moving the installation.

## Testing

```powershell
python test_app.py
python -m py_compile app.py database.py test_app.py
node --check static/js/app.js
```

For direct Flask/SQLite checks:

```powershell
python -m unittest -v test_app
```

## Project layout

```text
app.py                 Flask routes, authentication, authorization, CSRF
database.py            SQLite migrations and data access
templates/             Existing portal and account/admin pages
static/js/app.js       Search, filters, preview, study kit, and interactions
static/uploads/        Persistent local uploaded files
data/univault.db       Persistent SQLite database (created on first start)
```
