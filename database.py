"""SQLite storage and backward-compatible migrations for UniVault."""
import os
import sqlite3
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DATABASE_PATH = os.path.join(DATA_DIR, "univault.db")


def get_db_path():
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATABASE_PATH


def get_connection():
    conn = sqlite3.connect(get_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS materials (
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
      subject_name TEXT NOT NULL, subject_code TEXT, branch TEXT NOT NULL, semester TEXT NOT NULL,
      university TEXT NOT NULL, material_type TEXT NOT NULL, academic_year TEXT DEFAULT '2024',
      file_url TEXT NOT NULL, file_type TEXT DEFAULT 'PDF', file_size_kb INTEGER DEFAULT 2048,
      page_count INTEGER DEFAULT 20, uploader_name TEXT NOT NULL, uploader_avatar TEXT,
      downloads_count INTEGER DEFAULT 0, views_count INTEGER DEFAULT 0, upvotes_count INTEGER DEFAULT 0,
      tags TEXT DEFAULT '', is_featured INTEGER DEFAULT 0, preview_content TEXT DEFAULT '',
      status TEXT DEFAULT 'approved', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, file_data BLOB DEFAULT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS reviews (
      id INTEGER PRIMARY KEY AUTOINCREMENT, material_id INTEGER NOT NULL, author_name TEXT NOT NULL,
      rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5), comment TEXT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE CASCADE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS contributors (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, avatar TEXT, university TEXT,
      uploads_count INTEGER DEFAULT 0, upvotes_count INTEGER DEFAULT 0,
      badge TEXT DEFAULT 'Contributor', reputation INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      username TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','admin')),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_active INTEGER NOT NULL DEFAULT 1,
      must_change_password INTEGER NOT NULL DEFAULT 0,
      is_owner INTEGER NOT NULL DEFAULT 0)""")

    def add_column(table, column, declaration):
        columns = {r["name"] for r in c.execute("PRAGMA table_info(" + table + ")")}
        if column not in columns:
            c.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + declaration)

    # Migrate old schemas in place, retaining every existing row and BLOB.
    add_column("materials", "file_data", "BLOB DEFAULT NULL")
    add_column("materials", "uploader_user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL")
    add_column("reviews", "user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL")
    add_column("users", "is_owner", "INTEGER NOT NULL DEFAULT 0")
    c.execute("""CREATE TABLE IF NOT EXISTS bookmarks (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, material_id INTEGER NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,material_id),
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE CASCADE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS material_upvotes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, material_id INTEGER NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,material_id),
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE CASCADE)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_upvotes_material ON material_upvotes(material_id)")

    # Mark the existing seeded account as the permanent owner once, using the
    # reserved seed username only to migrate existing databases. All runtime
    # authorization checks use this persisted flag rather than a username.
    c.execute("""UPDATE users SET is_owner=1 WHERE id=(
        SELECT id FROM users WHERE username='Sanjeet' COLLATE NOCASE ORDER BY id LIMIT 1
      ) AND NOT EXISTS (SELECT 1 FROM users WHERE is_owner=1)""")

    # A fixed Werkzeug scrypt verifier seeds the specified initial account; no plaintext is stored.
    if c.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone() is None:
        c.execute("""INSERT INTO users(name,username,password_hash,role,must_change_password,is_owner)
                     VALUES(?,?,?,'admin',1,1)""", (
            "Sanjeet", "Sanjeet",
            "scrypt:32768:8:1$xqcSj1Kd3xbReAfM$aa8334592483873c7ed195493e81b876138c465d005a5801ffee599d1a72e6735d9d243f77f75bd31f0a5d8eb4ea7784274d24932c76348ffe251e1889a4f16f"))
    conn.commit()
    conn.close()


def get_materials(search="", branch="all", semester="all", university="all", material_type="all", sort_by="popular", page=1, per_page=12):
    conn = get_connection()
    query = """SELECT m.*, COALESCE(AVG(r.rating),5.0) avg_rating, COUNT(r.id) review_count
               FROM materials m LEFT JOIN reviews r ON m.id=r.material_id WHERE m.status='approved'"""
    params = []
    if search and search.strip():
        query += " AND (m.title LIKE ? OR m.subject_name LIKE ? OR m.subject_code LIKE ? OR m.description LIKE ? OR m.university LIKE ? OR m.uploader_name LIKE ? OR m.tags LIKE ?)"
        params.extend([f"%{search.strip()}%"] * 7)
    for val, col in ((branch,"branch"),(semester,"semester"),(university,"university"),(material_type,"material_type")):
        if val and val.lower() != "all":
            query += " AND LOWER(m." + col + ")=LOWER(?)"
            params.append(val)
    query += " GROUP BY m.id"
    query += {"downloads":" ORDER BY m.downloads_count DESC, m.id DESC", "rating":" ORDER BY avg_rating DESC, m.upvotes_count DESC", "newest":" ORDER BY m.id DESC"}.get(sort_by," ORDER BY m.is_featured DESC, m.upvotes_count DESC, m.downloads_count DESC")
    rows = conn.execute(query, params).fetchall()
    total = len(rows)
    results = []
    for row in rows[(page-1)*per_page:(page-1)*per_page+per_page]:
        d = dict(row); d.pop("file_data", None); d.pop("uploader_user_id", None)
        d["avg_rating"] = round(d["avg_rating"] or 5.0, 1)
        if not d.get("file_url") or "mathiasbynens/small/master/pdf.pdf" in d.get("file_url",""):
            d["file_url"] = f"/api/materials/{d['id']}/file"
        results.append(d)
    conn.close()
    return {"materials":results,"total":total,"page":page,"per_page":per_page,"total_pages":(total+per_page-1)//per_page if total else 1}


def get_material_by_id(material_id):
    conn = get_connection()
    row = conn.execute("""SELECT m.*, COALESCE(AVG(r.rating),5.0) avg_rating, COUNT(r.id) review_count
                          FROM materials m LEFT JOIN reviews r ON m.id=r.material_id WHERE m.id=? AND m.status='approved' GROUP BY m.id""",(material_id,)).fetchone()
    if not row:
        conn.close(); return None
    item = dict(row); item.pop("file_data",None); item.pop("uploader_user_id",None); item["avg_rating"] = round(item["avg_rating"] or 5.0,1)
    if not item.get("file_url") or "mathiasbynens/small/master/pdf.pdf" in item.get("file_url",""):
        item["file_url"] = f"/api/materials/{item['id']}/file"
    item["reviews"] = [dict(r) for r in conn.execute("SELECT id,author_name,rating,comment,created_at FROM reviews WHERE material_id=? ORDER BY id DESC",(material_id,))]
    conn.close(); return item


def increment_views(material_id):
    conn=get_connection(); conn.execute("UPDATE materials SET views_count=views_count+1 WHERE id=?",(material_id,)); conn.commit(); conn.close()


def increment_upvote(material_id,user_id):
    conn=get_connection(); c=conn.cursor()
    try: c.execute("INSERT INTO material_upvotes(user_id,material_id) VALUES(?,?)",(user_id,material_id))
    except sqlite3.IntegrityError:
        row=c.execute("SELECT upvotes_count FROM materials WHERE id=?",(material_id,)).fetchone(); conn.close(); return (row["upvotes_count"] if row else 0,True)
    c.execute("UPDATE materials SET upvotes_count=upvotes_count+1 WHERE id=?",(material_id,))
    row=c.execute("SELECT upvotes_count FROM materials WHERE id=?",(material_id,)).fetchone(); conn.commit(); conn.close()
    return (row["upvotes_count"] if row else 0,False)


def increment_download(material_id):
    conn=get_connection(); c=conn.cursor(); c.execute("UPDATE materials SET downloads_count=downloads_count+1 WHERE id=?",(material_id,))
    row=c.execute("SELECT downloads_count,file_url FROM materials WHERE id=?",(material_id,)).fetchone(); conn.commit(); conn.close()
    return dict(row) if row else {"downloads_count":0,"file_url":""}


def create_material(data,file_data=None,uploader_user_id=None):
    conn=get_connection(); c=conn.cursor()
    c.execute("""INSERT INTO materials(title,description,subject_name,subject_code,branch,semester,university,material_type,academic_year,file_url,file_type,file_size_kb,page_count,uploader_name,uploader_avatar,downloads_count,views_count,upvotes_count,tags,is_featured,preview_content,status,file_data,uploader_user_id)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,?,0,?,'approved',?,?)""",(
        data.get("title","").strip(),data.get("description","").strip(),data.get("subject_name","").strip(),data.get("subject_code","").strip().upper(),
        data.get("branch","Computer Science & Engineering"),data.get("semester","Semester 1"),data.get("university","General University"),
        data.get("material_type","Lecture Notes"),data.get("academic_year",str(datetime.now().year)),data.get("file_url",""),data.get("file_type","PDF"),
        data.get("file_size_kb",2500),data.get("page_count",20),data.get("uploader_name","Student Contributor"),data.get("uploader_avatar",""),
        data.get("tags",""),data.get("preview_content",data.get("description","")),file_data,uploader_user_id))
    mid=c.lastrowid
    if not data.get("file_url","").strip(): c.execute("UPDATE materials SET file_url=? WHERE id=?",(f"/api/materials/{mid}/file",mid))
    name=data.get("uploader_name","Student Contributor").strip()
    contributor=c.execute("SELECT id FROM contributors WHERE name=?",(name,)).fetchone()
    if contributor: c.execute("UPDATE contributors SET uploads_count=uploads_count+1,reputation=reputation+150 WHERE id=?",(contributor["id"],))
    else: c.execute("INSERT INTO contributors(name,avatar,university,uploads_count,upvotes_count,badge,reputation) VALUES(?,?,?,1,0,'Rising Contributor',150)",(name,data.get("uploader_avatar",""),data.get("university","General University")))
    conn.commit(); conn.close(); return mid


def get_material_file_data(material_id):
    conn=get_connection(); row=conn.execute("SELECT file_data,file_type FROM materials WHERE id=?",(material_id,)).fetchone(); conn.close()
    return (bytes(row["file_data"]),row["file_type"]) if row and row["file_data"] else (None,None)


def create_review(material_id,author_name,rating,comment,user_id=None):
    conn=get_connection(); c=conn.cursor()
    c.execute("INSERT INTO reviews(material_id,author_name,rating,comment,user_id) VALUES(?,?,?,?,?)",(material_id,author_name.strip(),rating,comment.strip(),user_id))
    rid=c.lastrowid; conn.commit(); conn.close(); return rid


def get_stats():
    conn=get_connection(); c=conn.cursor()
    stats=dict(c.execute("""SELECT COUNT(id) total_materials,COALESCE(SUM(downloads_count),0) total_downloads,
      COALESCE(SUM(views_count),0) total_views,COALESCE(SUM(upvotes_count),0) total_upvotes,
      COUNT(DISTINCT university) total_universities,COUNT(DISTINCT branch) total_branches FROM materials WHERE status='approved'""").fetchone())
    stats["total_reviews"]=c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]; conn.close(); return stats


def get_leaderboard(limit=6):
    conn=get_connection(); rows=conn.execute("SELECT name,avatar,university,uploads_count,upvotes_count,badge,reputation FROM contributors ORDER BY reputation DESC,upvotes_count DESC LIMIT ?",(limit,)).fetchall(); conn.close(); return [dict(r) for r in rows]


def get_filter_options():
    conn=get_connection()
    out={key:[r[0] for r in conn.execute("SELECT DISTINCT "+col+" FROM materials WHERE status='approved' ORDER BY "+col)] for key,col in (("universities","university"),("branches","branch"),("semesters","semester"),("material_types","material_type"))}
    conn.close(); return out
