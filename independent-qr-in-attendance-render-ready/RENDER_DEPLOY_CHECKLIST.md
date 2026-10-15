# Render deployment checklist (phone-friendly)

## A. Update GitHub files first
Upload the updated files from this project ZIP to the same repository. In particular, make sure the new `requirements.txt`, updated `app.py`, updated `static/admin.html`, `.env.example`, and README are committed. Do not upload a real `.env` file or secret values.

## B. Create PostgreSQL
Use a PostgreSQL provider whose retention and backup policy you accept. A free Render Postgres database expires after 30 days; it should not be treated as permanent storage. Neon is an alternative provider, but review its current free limits and retention/backup policy too.

Copy the connection string. Do not paste it into chat or commit it to GitHub.

## C. Configure Render Web Service
- Runtime: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `python app.py`
- Environment variable `DATABASE_URL`: your private PostgreSQL connection string
- Environment variable `ADMIN_SETUP_KEY`: random secret of at least 24 characters
- Health check path: `/health` (if the form offers it)

Then deploy. After it is live, open `/health` and confirm it returns `{"status":"ok"}`.

## D. Create first admin
Open `/admin` over HTTPS, enter username, a unique password of at least 12 characters, and the exact `ADMIN_SETUP_KEY` value. Do not share either secret. Once the first admin exists, setup is closed.

## E. Before attendance use
Verify SITE-001's physical coordinates before activating it. Check the QR scan and geofence with a real phone. Export/backup data regularly. The 2 m radius and <=2 m GPS accuracy policy is unusually strict and may reject valid users.
