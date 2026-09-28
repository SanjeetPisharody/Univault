"""Integration checks for public access and account authorization."""
import io
import secrets
import unittest

from app import app
import database
from werkzeug.security import check_password_hash, generate_password_hash


class UniVaultSecurityTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = app.test_client()
        self.username = "test_" + secrets.token_hex(5)
        self.password = "StudentCheck123"
        self.material_id = None
        self.extra_material_ids = []
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        token = self.csrf()
        response = self.client.post("/register", data={
            "csrf_token": token, "name": "Test Student", "username": self.username,
            "password": self.password, "confirm_password": self.password,
        })
        self.assertEqual(response.status_code, 302)
        token = self.csrf()
        response = self.client.post("/login", data={
            "csrf_token": token, "username": self.username, "password": self.password,
        })
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        self.user_id = conn.execute("SELECT id FROM users WHERE username=?", (self.username,)).fetchone()["id"]
        self.assertEqual(conn.execute("SELECT role FROM users WHERE id=?", (self.user_id,)).fetchone()["role"], "student")
        contributor=conn.execute("SELECT * FROM contributors WHERE name=?",("Test Student",)).fetchone()
        self.contributor_before=dict(contributor) if contributor else None
        cursor = conn.execute("""INSERT INTO materials(title,subject_name,branch,semester,university,material_type,
            file_url,uploader_name,status) VALUES(?,?,?,?,?,?,?,?,?)""",
            ("Test Security Material", "Test Subject", "Test Branch", "Semester 1", "Test University",
             "Lecture Notes", "/static/uploads/test-not-real.pdf", "Test Student", "approved"))
        self.material_id = cursor.lastrowid
        conn.commit()
        conn.close()

    def csrf(self):
        with self.client.session_transaction() as sess:
            return sess.setdefault("csrf_token", secrets.token_urlsafe(32))

    def test_public_browsing_and_guest_restrictions(self):
        self.client.post("/logout", data={"csrf_token": self.csrf()})
        self.assertEqual(self.client.get("/api/materials?search=Test").status_code, 200)
        self.assertEqual(self.client.get(f"/api/materials/{self.material_id}").status_code, 200)
        self.assertEqual(self.client.post("/api/materials", data={"title":"No","subject_name":"No"}, headers={"X-CSRF-Token":self.csrf()}).status_code, 401)
        self.assertEqual(self.client.post(f"/api/materials/{self.material_id}/reviews", json={"rating":5,"comment":"No"}, headers={"X-CSRF-Token":self.csrf()}).status_code, 401)
        self.assertEqual(self.client.post(f"/api/materials/{self.material_id}/upvote", headers={"X-CSRF-Token":self.csrf()}).status_code, 401)
        self.assertEqual(self.client.post(f"/api/bookmarks/{self.material_id}", headers={"X-CSRF-Token":self.csrf()}).status_code, 401)
        self.assertEqual(self.client.get("/profile").status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_student_reviews_upvotes_bookmarks_and_ownership(self):
        self.assertEqual(self.client.get("/profile").status_code,200)
        self.assertEqual(self.client.get("/my-uploads").status_code,200)
        headers={"X-CSRF-Token":self.csrf()}
        low_rating=self.client.post(f"/api/materials/{self.material_id}/reviews",json={"rating":1,"comment":"One star rating check"},headers=headers)
        high_rating=self.client.post(f"/api/materials/{self.material_id}/reviews",json={"rating":6,"comment":"Invalid rating check"},headers=headers)
        self.assertEqual(low_rating.status_code,201)
        self.assertEqual(high_rating.status_code,400)
        download=self.client.post(f"/api/materials/{self.material_id}/download",headers=headers)
        self.assertEqual(download.status_code,200)
        self.assertEqual(download.get_json()["downloads_count"],1)
        first=self.client.post(f"/api/materials/{self.material_id}/upvote",headers=headers)
        second=self.client.post(f"/api/materials/{self.material_id}/upvote",headers=headers)
        self.assertEqual(first.status_code,200)
        self.assertTrue(second.get_json()["already_upvoted"])
        saved=self.client.post(f"/api/bookmarks/{self.material_id}",headers=headers)
        self.assertEqual(saved.status_code,200)
        self.assertEqual(len(self.client.get("/api/bookmarks").get_json()["bookmarks"]),1)
        reviewed=self.client.post(f"/api/materials/{self.material_id}/reviews",json={
            "author_name":"Impersonated Admin","rating":5,"comment":"Useful notes"
        },headers=headers)
        self.assertEqual(reviewed.status_code,201)
        item=self.client.get(f"/api/materials/{self.material_id}").get_json()
        self.assertEqual(item["reviews"][0]["author_name"],"Test Student")
        self.assertNotIn("uploader_user_id",item)
        self.assertEqual(self.client.get("/api/user-state").get_json()["upvoted_ids"],[self.material_id])

    def test_username_and_password_hash_persist_after_logout(self):
        conn=database.get_connection()
        row=conn.execute("SELECT username,password_hash FROM users WHERE id=?",(self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row["username"],self.username)
        self.assertNotEqual(row["password_hash"],self.password)
        self.assertTrue(check_password_hash(row["password_hash"],self.password))
        self.client.post("/logout",data={"csrf_token":self.csrf()})
        self.assertEqual(self.client.get("/profile").status_code,302)
        self.client.get("/login")
        login=self.client.post("/login",data={"csrf_token":self.csrf(),"username":self.username,"password":self.password})
        self.assertEqual(login.status_code,302)
        self.assertEqual(self.client.get("/profile").status_code,200)

    def test_student_cannot_access_admin_and_delete_requires_password(self):
        self.assertEqual(self.client.get("/admin").status_code,403)
        self.assertEqual(self.client.post(f"/api/admin/users/{self.user_id}",data={"action":"promote"},headers={"X-CSRF-Token":self.csrf()}).status_code,403)
        self.assertEqual(self.client.post("/api/admin/users",data={"name":"Not an admin","username":"student_admin_attempt","password":"StudentPassword123","confirm_password":"StudentPassword123"},headers={"X-CSRF-Token":self.csrf()}).status_code,403)
        bad=self.client.post("/account/delete",data={"csrf_token":self.csrf(),"password":"wrong","confirm_delete":"DELETE"})
        self.assertEqual(bad.status_code,200)
        conn=database.get_connection()
        self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE id=?",(self.user_id,)).fetchone())
        conn.close()
        deleted=self.client.post("/account/delete",data={
            "csrf_token":self.csrf(),"password":self.password,"confirm_delete":"DELETE"
        })
        self.assertEqual(deleted.status_code,302)
        conn=database.get_connection()
        self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE id=?",(self.user_id,)).fetchone())
        self.assertIsNotNone(conn.execute("SELECT 1 FROM materials WHERE id=?",(self.material_id,)).fetchone())
        self.assertIsNone(conn.execute("SELECT uploader_user_id FROM materials WHERE id=?",(self.material_id,)).fetchone()[0])
        conn.close()

    def test_student_upload_uses_session_identity_and_persists_file(self):
        token=self.csrf()
        response=self.client.post("/api/materials",data={
            "title":"Student Upload","subject_name":"Test Subject","uploader_name":"Sanjeet",
            "file":(io.BytesIO(b"local upload"),"notes.txt"),"csrf_token":token,
        },headers={"X-CSRF-Token":token},content_type="multipart/form-data")
        self.assertEqual(response.status_code,201)
        mid=response.get_json()["material_id"]
        self.extra_material_ids.append(mid)
        conn=database.get_connection()
        row=conn.execute("SELECT uploader_name,uploader_user_id,file_url FROM materials WHERE id=?",(mid,)).fetchone()
        conn.close()
        self.assertEqual(row["uploader_name"],"Test Student")
        self.assertEqual(row["uploader_user_id"],self.user_id)
        self.assertTrue(row["file_url"].startswith("/static/uploads/"))
        downloaded=self.client.get(f"/api/materials/{mid}/file")
        self.assertEqual(downloaded.status_code,200)
        self.assertEqual(downloaded.data,b"local upload")
        downloaded.close()
        self.upload_id=mid

    def test_wrong_login_message_is_generic(self):
        self.client.post("/logout",data={"csrf_token":self.csrf()})
        response=self.client.post("/login",data={"csrf_token":self.csrf(),"username":self.username,"password":"incorrect"})
        self.assertEqual(response.status_code,200)
        self.assertIn(b"Invalid username or password.",response.data)

    def test_admin_change_password_dashboard_and_user_controls(self):
        conn=database.get_connection()
        admin=conn.execute("SELECT id,password_hash,must_change_password,is_owner FROM users WHERE username='Sanjeet' COLLATE NOCASE").fetchone()
        self.assertEqual(admin["is_owner"],1)
        original=(admin["password_hash"],admin["must_change_password"])
        test_admin_password="TemporaryAdminCheck987"
        conn.execute("UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?",
                     (generate_password_hash(test_admin_password),admin["id"]))
        cursor=conn.execute("INSERT INTO users(name,username,password_hash,role) VALUES(?,?,?,'student')",
                            ("Second student","test_other_"+secrets.token_hex(4),generate_password_hash("AnotherStudent123")))
        other_id=cursor.lastrowid
        conn.commit(); conn.close()
        created_admin_id=None
        created_admin_username="delegated_admin_"+secrets.token_hex(4)
        delegated_password="DelegatedAdmin123"
        admin_client=app.test_client()
        try:
            admin_client.get("/login")
            with admin_client.session_transaction() as sess: token=sess["csrf_token"]
            login=admin_client.post("/login",data={"csrf_token":token,"username":"Sanjeet","password":test_admin_password})
            self.assertEqual(login.location,"/change-password")
            token=admin_client.get("/change-password")
            with admin_client.session_transaction() as sess: token=sess["csrf_token"]
            changed=admin_client.post("/change-password",data={"csrf_token":token,"current_password":test_admin_password,
                "new_password":"ChangedAdminCheck987","confirm_password":"ChangedAdminCheck987"})
            self.assertEqual(changed.status_code,302)
            dashboard=admin_client.get("/admin")
            self.assertEqual(dashboard.status_code,200)
            self.assertIn(b"Add an administrator",dashboard.data)
            self.assertIn(b"Original admin",dashboard.data)
            with admin_client.session_transaction() as sess: csrf=sess["csrf_token"]
            response=admin_client.post("/api/admin/users",data={"name":"Delegated Administrator","username":created_admin_username,
                "password":delegated_password,"confirm_password":delegated_password},headers={"X-CSRF-Token":csrf})
            self.assertEqual(response.status_code,201)
            created=response.get_json()
            self.assertEqual(created["user"]["role"],"admin")
            self.assertNotIn("password",created["user"])
            self.assertNotIn("password_hash",created["user"])
            created_admin_id=created["user"]["id"]
            conn=database.get_connection()
            created_row=conn.execute("SELECT role,password_hash,must_change_password FROM users WHERE id=?",(created_admin_id,)).fetchone()
            conn.close()
            self.assertEqual(created_row["role"],"admin")
            self.assertTrue(check_password_hash(created_row["password_hash"],delegated_password))
            self.assertEqual(created_row["must_change_password"],1)
            duplicate=admin_client.post("/api/admin/users",data={"name":"Duplicate","username":created_admin_username,
                "password":delegated_password,"confirm_password":delegated_password},headers={"X-CSRF-Token":csrf})
            self.assertEqual(duplicate.status_code,409)
            delegated_client=app.test_client()
            delegated_client.get("/login")
            with delegated_client.session_transaction() as sess: delegated_csrf=sess["csrf_token"]
            delegated_login=delegated_client.post("/login",data={"csrf_token":delegated_csrf,"username":created_admin_username,"password":delegated_password})
            self.assertEqual(delegated_login.location,"/change-password")
            delegated_client.get("/change-password")
            with delegated_client.session_transaction() as sess: delegated_csrf=sess["csrf_token"]
            delegated_changed=delegated_client.post("/change-password",data={"csrf_token":delegated_csrf,
                "current_password":delegated_password,"new_password":"DelegatedAdminChanged123",
                "confirm_password":"DelegatedAdminChanged123"})
            self.assertEqual(delegated_changed.status_code,302)
            other_admin_dashboard=delegated_client.get("/admin")
            self.assertEqual(other_admin_dashboard.status_code,200)
            self.assertIn(b"Original admin",other_admin_dashboard.data)
            self.assertNotIn(f'data-user="{admin["id"]}" data-action="demote"'.encode(),other_admin_dashboard.data)
            owner_demotion=delegated_client.post(f"/api/admin/users/{admin['id']}",data={"action":"demote"},
                headers={"X-CSRF-Token":delegated_csrf})
            self.assertEqual(owner_demotion.status_code,403)
            owner_deletion=delegated_client.post(f"/api/admin/users/{admin['id']}",data={"action":"delete"},
                headers={"X-CSRF-Token":delegated_csrf})
            self.assertEqual(owner_deletion.status_code,403)
            conn=database.get_connection()
            self.assertEqual(conn.execute("SELECT role FROM users WHERE id=?",(admin["id"],)).fetchone()["role"],"admin")
            conn.close()
            response=admin_client.post(f"/api/admin/users/{other_id}",data={"action":"promote"},headers={"X-CSRF-Token":csrf})
            self.assertEqual(response.status_code,200)
            response=admin_client.post(f"/api/admin/users/{other_id}",data={"action":"demote"},headers={"X-CSRF-Token":csrf})
            self.assertEqual(response.status_code,200)
            response=admin_client.post(f"/api/admin/users/{other_id}",data={"action":"suspend"},headers={"X-CSRF-Token":csrf})
            self.assertEqual(response.status_code,200)
            response=admin_client.post(f"/api/admin/users/{other_id}",data={"action":"activate"},headers={"X-CSRF-Token":csrf})
            self.assertEqual(response.status_code,200)
            response=admin_client.post(f"/api/admin/users/{created_admin_id}",data={"action":"demote"},headers={"X-CSRF-Token":csrf})
            self.assertEqual(response.status_code,200)
            response=admin_client.post(f"/api/admin/users/{admin['id']}",data={"action":"demote"},headers={"X-CSRF-Token":csrf})
            self.assertEqual(response.status_code,403)
        finally:
            conn=database.get_connection()
            conn.execute("UPDATE users SET role='admin',password_hash=?,must_change_password=? WHERE id=?",
                         (original[0],original[1],admin["id"]))
            conn.execute("DELETE FROM users WHERE id=?",(other_id,))
            if created_admin_id is not None:
                conn.execute("DELETE FROM users WHERE id=?",(created_admin_id,))
            conn.commit(); conn.close()

    def test_admin_seed_is_hashed_and_not_reset(self):
        conn=database.get_connection()
        row=conn.execute("SELECT password_hash,role,must_change_password,is_owner FROM users WHERE username='Sanjeet' COLLATE NOCASE").fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row["password_hash"].startswith("scrypt:"))
        self.assertEqual(row["role"],"admin")
        self.assertEqual(row["is_owner"],1)
        # The owner may already have completed the required first-login password change.
        self.assertIn(row["must_change_password"],(0,1))
        old_hash=row["password_hash"]
        old_change_status=row["must_change_password"]
        conn.close()
        database.init_db()
        conn=database.get_connection()
        current=conn.execute("SELECT password_hash,must_change_password FROM users WHERE username='Sanjeet' COLLATE NOCASE").fetchone()
        self.assertEqual(current["password_hash"],old_hash)
        self.assertEqual(current["must_change_password"],old_change_status)
        conn.close()

    def tearDown(self):
        conn=database.get_connection()
        for mid in ([self.material_id] if self.material_id else []) + self.extra_material_ids:
            row=conn.execute("SELECT file_url FROM materials WHERE id=?",(mid,)).fetchone()
            conn.execute("DELETE FROM materials WHERE id=?",(mid,))
            if row and row["file_url"].startswith("/static/uploads/"):
                import os
                import os
                path=os.path.join(os.path.dirname(__file__),"static","uploads",os.path.basename(row["file_url"]))
                if os.path.isfile(path): os.remove(path)
        conn.execute("DELETE FROM users WHERE id=?",(self.user_id,))
        if self.contributor_before:
            conn.execute("UPDATE contributors SET uploads_count=?,upvotes_count=?,reputation=?,badge=? WHERE id=?",
                (self.contributor_before["uploads_count"],self.contributor_before["upvotes_count"],
                 self.contributor_before["reputation"],self.contributor_before["badge"],self.contributor_before["id"]))
        else:
            conn.execute("DELETE FROM contributors WHERE name=?",("Test Student",))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
