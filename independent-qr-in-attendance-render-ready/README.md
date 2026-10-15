# Independent QR IN Attendance System

A separate, mobile-first QR attendance project. It does not modify or reuse any existing project.

## Stack
- Python 3.11+ HTTP server
- SQLite for local development
- PostgreSQL for hosted deployment (`DATABASE_URL`), with psycopg
- Responsive HTML/CSS/JavaScript

## Local run
1. Install Python 3.11 or newer.
2. Run `python app.py` in this folder.
3. Open `http://127.0.0.1:8080`.
4. First-run admin setup appears if the database has no admin. Use a strong unique password.

## Render + PostgreSQL setup
**Do not use SQLite on a free Render web service for attendance records.** Its local filesystem is ephemeral.

1. Create a PostgreSQL database with a provider that suits your data-retention needs (Neon is one option). Copy its pooled or direct PostgreSQL connection string according to that provider's instructions. Keep it private.
2. Upload/commit the files in this folder to your GitHub repository, including `requirements.txt` and the updated `app.py` and `static/admin.html`.
3. On Render, create a Python Web Service connected to that repository.
4. Set **Build Command** to `pip install -r requirements.txt`.
5. Set **Start Command** to `python app.py`.
6. Add environment variable `DATABASE_URL` with the PostgreSQL connection string.
7. Add environment variable `ADMIN_SETUP_KEY` with a random secret of at least 24 characters. Use it only once during first-admin creation; enter the same value into the setup-key field on `/admin`.
8. Set Render Health Check Path to `/health` if that setting is available, then deploy.
9. Open `/admin` over HTTPS and create the first admin. Keep the admin password and setup key private. After setup, the server refuses additional setup attempts.
10. Before real use, verify the site coordinates on-site. The initial `SITE-001` remains disabled until an admin explicitly confirms its coordinates.

Render free web-service files are ephemeral, and Render's free PostgreSQL instances have a 30-day lifetime; select your database provider/plan with its retention and backup policy in mind. Neon free limits and terms can also change. A free tier is suitable for testing, not guaranteed permanent production storage. Check provider terms and arrange exports/backups before recording important attendance.

## Environment variables
- `HOST`: defaults to `0.0.0.0` for hosting. Locally, set `HOST=127.0.0.1` if you want loopback-only access.
- `PORT`: hosting platform port; defaults to `8080` locally.
- `ATTENDANCE_DB`: SQLite file path, used only if `DATABASE_URL` is empty.
- `DATABASE_URL`: PostgreSQL connection string for persistent hosted data.
- `ADMIN_SETUP_KEY`: hosted first-admin bootstrap secret; minimum 24 characters.

## Attendance and GPS limitations
- The initial site uses latitude `28.4674447`, longitude `77.0266035`, radius 2 m and Asia/Kolkata time. It is pending on-site verification.
- GPS accuracy must be 2 m or better, and the browser location must be no older than 15 seconds. These strict thresholds can reject genuine attempts.
- Browser GPS can be spoofed; a fixed QR plus GPS does not prove identity or guarantee physical presence.
- The database enforces at most one successful IN per person per Asia/Kolkata calendar day across all sites.
- QR rendering/scanning use CDN libraries and therefore need internet access unless self-hosted.

## Tests
Run: `python -m unittest discover -s tests -v`

## Status
This is an improved deployable starter, not a certified production system. Local unit tests do not replace a live PostgreSQL integration test, real-phone GPS validation, review of threat controls, monitoring, or backups. Do not record critical attendance until those checks are complete.
