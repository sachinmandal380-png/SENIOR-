import os, tempfile, unittest, importlib.util, sqlite3
from pathlib import Path

spec = importlib.util.spec_from_file_location("attendance_app", Path(__file__).parents[1] / "app.py")
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)

class CoreLogicTests(unittest.TestCase):
    def test_same_point_distance_zero(self):
        self.assertAlmostEqual(app.haversine_m(28.4674447,77.0266035,28.4674447,77.0266035),0,places=5)
    def test_outside_point_distance(self):
        d=app.haversine_m(28.4674447,77.0266035,28.4675,77.0266035)
        self.assertGreater(d,2)
    def test_password_hash_and_verify(self):
        stored=app.hash_password("StrongPassphrase123!")
        self.assertTrue(app.verify_password("StrongPassphrase123!",stored))
        self.assertFalse(app.verify_password("wrong",stored))
    def test_kolkata_date_format(self):
        day, stamp=app.kolkata_parts()
        self.assertRegex(day,r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("+05:30",stamp)

    def test_database_schema_and_daily_duplicate_constraint(self):
        previous_db = app.DB
        with tempfile.TemporaryDirectory() as temp_dir:
            app.DB = Path(temp_dir) / "test-attendance.sqlite3"
            try:
                app.init_db()
                with app.connect() as c:
                    site = c.execute("SELECT id FROM sites WHERE id=?", (app.SITE_ID,)).fetchone()
                    self.assertIsNotNone(site)
                    cur = c.execute(
                        "INSERT INTO people(name,normalized_name,active,created_at,updated_at) VALUES(?,?,1,?,?)",
                        ("Test Person", "test person", "2026-10-15T00:00:00+00:00", "2026-10-15T00:00:00+00:00"),
                    )
                    person_id = cur.lastrowid
                    insert = "INSERT INTO attendance(person_id,site_id,business_date,marked_at_utc,marked_at_ist,status) VALUES(?,?,?,?,?,'IN')"
                    values = (person_id, app.SITE_ID, "2026-10-15", "2026-10-15T00:00:00+00:00", "2026-10-15T05:30:00+05:30")
                    c.execute(insert, values)
                    c.commit()
                    with self.assertRaises(sqlite3.IntegrityError):
                        c.execute(insert, values)
            finally:
                app.DB = previous_db

if __name__ == "__main__": unittest.main()
