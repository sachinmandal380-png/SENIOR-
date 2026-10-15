# Threat model and limitations

## Protected assets
Approved-person records, attendance records, admin account, site coordinates/configuration, audit events.

## Threats addressed
- Arbitrary/unapproved names: rejected against active approved list.
- Duplicate attendance: unique database constraint on person + Asia/Kolkata business date.
- Stale/poor GPS readings: server checks age and reported accuracy thresholds.
- QR/site mismatch: site ID is validated against server-side site configuration.
- Unauthorized admin APIs: session authentication required.
- CSV formula injection: spreadsheet-leading formula characters are escaped.
- Password storage: PBKDF2-HMAC-SHA256 with per-password salt.

## Residual risks
- Browser geolocation can be spoofed on compromised devices.
- A fixed QR can be copied. GPS does not establish human identity.
- Client-provided GPS accuracy and age metadata can be manipulated; this app does not have hardware-backed attestation.
- Two-metre GPS geofence and accuracy thresholds may reject genuine users, particularly indoors.
- Standard-library server/session implementation is for local prototype use, not hardened public production.
- Audit events are application-protected but not immutable against a database owner.
- HTTPS, CSRF defense, production session hardening, monitoring, backups, and PostgreSQL are required before public deployment.
