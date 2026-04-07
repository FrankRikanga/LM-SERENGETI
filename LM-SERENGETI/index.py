from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response
import sqlite3, difflib, time, os, json
from datetime import datetime
from chatterbot import ChatBot
from werkzeug.utils import secure_filename

# --- Flask Setup ---
app = Flask(__name__)
app.secret_key = "supersecretkey"

UPLOAD_FOLDER = "static/profile_images"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# --- ChatBot Setup ---
bot = ChatBot("math", logic_adapters=["chatterbot.logic.MathematicalEvaluation"])

# --- DB Path ---
DB_PATH = os.path.join(os.path.dirname(__file__), "chatbot.db")

# --- Get DB Connection ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    return conn


# --- Load Knowledge Files ---
def load_knowledge():
    global knowledge_data, translations
    knowledge_data, translations = {}, {}
    for file_name in ["general.py", "science.py", "politics.py"]:
        if os.path.exists(file_name):
            namespace = {}
            with open(file_name, "r", encoding="utf-8") as f:
                exec(f.read(), namespace)
            knowledge_data.update(namespace.get("knowledge_data", {}))
            translations.update(namespace.get("translations", {}))

load_knowledge()

# --- Initialize DB ---
def init_db():
    conn = get_db()
    c = conn.cursor()

    # --- USERS TABLE ---
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT,
                    name TEXT,
                    age INTEGER,
                    email TEXT,
                    notes TEXT,
                    profile_pic TEXT DEFAULT 'default_avatar.png',
                    role TEXT DEFAULT 'user')''')

    # --- Add missing 'role' column (if DB existed before) ---
    try:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    except sqlite3.OperationalError:
        pass  # column already exists

    # --- POSTS TABLE ---
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    # --- MESSAGES TABLE ---
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER,
                    receiver_id INTEGER,
                    message TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
    # --- JOINS TABLE ---
    c.execute('''CREATE TABLE IF NOT EXISTS joins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    joiner_id INTEGER,
                    joined_id INTEGER,
                    UNIQUE(joiner_id, joined_id)
)
''')
    # --- LIKES TABLE ---
    c.execute('''CREATE TABLE IF NOT EXISTS likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_id INTEGER,
        UNIQUE(user_id, post_id))''')

# --- COMMENTS TABLE ---
    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER,
        user_id INTEGER,
        comment TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
    
    # --- Insert default users ---
    c.execute("INSERT OR IGNORE INTO users (id, username, password, name, role) VALUES (1, 'admin', '1234', 'Admin', 'admin')")
    c.execute("INSERT OR IGNORE INTO users (id, username, password, name, role) VALUES (2, 'user', 'abcd', 'Sample User', 'user')")

    conn.commit()
    conn.close()

init_db()


# --- Helper Functions ---
def get_user_id(username):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username=?", (username,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

def get_username(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

def get_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {
        "id": r[0], "username": r[1], "password": r[2],
        "name": r[3], "age": r[4], "email": r[5],
        "notes": r[6], "profile_pic": r[7], "role": r[8]
    }

def get_closest_match(text):
    matches = difflib.get_close_matches(text.lower().strip(), knowledge_data.keys(), n=1, cutoff=0.6)
    return matches[0] if matches else None

def typing_effect(text):
    for char in text:
        yield char
        time.sleep(0.03)

def translate_to_english(text):
    # Try to match full Swahili answer in translations
    for sw, en in translations.items():
        if sw.lower() in text.lower():
            return en
    
    return "Translation not available for this message."

#user post search
def search_related_posts(query, limit=2):
    """
    Find user posts related to the user's question
    """
    conn = get_db()
    c = conn.cursor()

    keywords = query.lower().split()
    conditions = " OR ".join(["posts.content LIKE ?"] * len(keywords))
    params = [f"%{k}%" for k in keywords]

    sql = f"""
        SELECT users.username, posts.content
        FROM posts
        JOIN users ON posts.user_id = users.id
        WHERE {conditions}
        LIMIT ?
    """

    c.execute(sql, params + [limit])
    results = c.fetchall()
    conn.close()

    return results


# --- ROUTES ---

@app.route("/")
def home():
    username = session.get("username")
    return render_template("index.html", username=username)

# --- Chatbot ---
@app.route("/get")
def get_bot_response():
    global last_bot_message
    user_text = request.args.get("msg", "").lower().strip()

    if user_text in ["yes", "ndio", "english please", "nataka kwa kiingereza"]:
        if last_bot_message:
            english = translate_to_english(last_bot_message)
            return Response(typing_effect(english), mimetype="text/plain")
        else:
            return Response(typing_effect("Hakuna ujumbe wa kutafsiri."), mimetype="text/plain")

    best_match = get_closest_match(user_text)

    if best_match is not None:
        response = knowledge_data[best_match]
    else:
        response = "Samahani, sijui hilo bado. (Sorry, I don’t know that yet.)\n\n"

    related_posts = search_related_posts(user_text)

    if related_posts:
        response += "\n\n💬 Kutoka kwa community:\n\n"
        for username, content in related_posts:
            short = content[:120] + ("..." if len(content) > 120 else "")
            response += f"- {username} alisema: \"{short}\"\n"

    response += "\n(ungependa tafsiri ya kimombo (english)?)"
    last_bot_message = response

    return Response(typing_effect(response), mimetype="text/plain")


# --- Register ---
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=?", (username,))
        if c.fetchone():
            return render_template("register.html", error="Username already exists.")
        c.execute("INSERT INTO users (username, password, name, role) VALUES (?, ?, ?, 'user')",
                  (username, password, username))
        conn.commit()
        conn.close()
        return render_template("register.html", success="Registration successful.")
    return render_template("register.html")

# --- Login ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, role FROM users WHERE username=? AND password=?", (username, password))
        u = c.fetchone()
        conn.close()
        if u:
            session["username"], session["user_id"], session["role"] = username, u[0], u[1]
            return redirect(url_for("admin_panel" if u[1] == "admin" else "home"))
        return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")

# --- Logout ---
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# --- Admin ---
@app.route("/admin")
def admin_panel():
    if session.get("role") == "admin":
        return render_template("admin.html")
    return redirect(url_for("login"))

@app.route("/admin/add", methods=["POST"])
def add_qa():
    if session.get("role") != "admin":
        return "Unauthorized", 403

    data = request.json
    cat, q, a, t = data.get("category"), data.get("question").strip(), data.get("answer").strip(), data.get("translation").strip()

    

    file_map = {"general": "general.py", "science": "science.py", "politics": "politics.py"}
    fpath = file_map.get(cat)
    if not fpath:
        return "Invalid category."

    namespace = {}
    if os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
            exec(f.read(), namespace)
        k_data, t_data = namespace.get("knowledge_data", {}), namespace.get("translations", {})
    else:
        k_data, t_data = {}, {}

    k_data[q], t_data[a.split("\n")[0]] = a, t
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("knowledge_data = " + json.dumps(k_data, indent=4, ensure_ascii=False) + "\n\n")
        f.write("translations = " + json.dumps(t_data, indent=4, ensure_ascii=False) + "\n")

    load_knowledge()
    return f"Added successfully to {fpath}!"

@app.route("/user/<username>")
def user_chat(username):
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("user_chat.html", target=username)

# --- User Profile ---
@app.route("/user_profile")
def user_profile():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user = get_user(session["user_id"])
    return render_template("user_profile.html", user=user)

@app.route("/edit_profile", methods=["GET", "POST"])
def edit_profile():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    conn = get_db()
    c = conn.cursor()

    # Load current data
    c.execute("SELECT name, age, email, notes, profile_pic FROM users WHERE id=?", (user_id,))
    data = c.fetchone()
    profile = {
        "name": data[0],
        "age": data[1],
        "email": data[2],
        "notes": data[3],
        "profile_pic": data[4]
    }

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        age = request.form.get("age")
        notes = request.form.get("notes")

        profile_pic = profile["profile_pic"]  # keep old image if no upload

        # Handle new profile picture upload
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file.filename != '':
                if not os.path.exists(app.config['UPLOAD_FOLDER']):
                    os.makedirs(app.config['UPLOAD_FOLDER'])

                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

                # Save ONLY the filename in the DB
                profile_pic = filename

        # Update database
        c.execute("""
            UPDATE users
            SET name=?, email=?, age=?, notes=?, profile_pic=?
            WHERE id=?
        """, (name, email, age, notes, profile_pic, user_id))

        conn.commit()
        conn.close()
        return redirect(url_for("user_profile"))

    conn.close()
    return render_template("edit_profile.html", profile=profile)


# --- Community ---
@app.route("/community")
def community():
    # --- Check if user is logged in ---
    if "user_id" not in session:
        return redirect(url_for("login"))

    # --- Fetch posts from DB ---
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT 
            posts.id,
            posts.content,
            posts.timestamp,
            users.name,
            users.profile_pic,
            (SELECT COUNT(*) FROM likes WHERE post_id = posts.id) AS likes,
            posts.user_id  -- <-- add this so owner can delete
        FROM posts
        JOIN users ON posts.user_id = users.id
        ORDER BY posts.id DESC
    """)
    posts = c.fetchall()
    conn.close()

    return render_template("community.html", posts=posts)

@app.route("/post", methods=["POST"])
def create_post():
    if "user_id" not in session:
        return redirect(url_for("login"))
    text = request.form.get("content", "").strip()
    if text:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO posts (user_id, content) VALUES (?, ?)", (session["user_id"], text))
        conn.commit()
        conn.close()
    return redirect(url_for("community"))

# --- Messaging ---
@app.route("/send_message", methods=["POST"])
def send_message():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"})
    recv = request.form["receiver"]
    msg = request.form["message"]
    sid = session["user_id"]
    rid = get_user_id(recv)
    if not rid:
        return jsonify({"error": "Invalid receiver"})
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO messages (sender_id, receiver_id, message) VALUES (?, ?, ?)", (sid, rid, msg))
    conn.commit()
    conn.close()
    return jsonify({"status": "Message sent"})

@app.route("/get_messages/<receiver>")
def get_messages(receiver):
    if "user_id" not in session:
        return jsonify([])
    sid = session["user_id"]
    rid = get_user_id(receiver)
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT sender_id, message, timestamp FROM messages 
                 WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
                 ORDER BY timestamp ASC""", (sid, rid, rid, sid))
    rows = c.fetchall()
    conn.close()
    msgs = [{"sender": get_username(r[0]), "message": r[1], "time": r[2]} for r in rows]
    return jsonify(msgs)

# --- Collaborate ---
@app.route("/collaborate")
def collaborate():
    if "user_id" not in session:
        return redirect(url_for("login"))

    uname = session["username"]
    user_id = session["user_id"]

    conn = get_db()
    c = conn.cursor()

    # Fetch users excluding admin and current user
    c.execute("""
        SELECT id, username, profile_pic
        FROM users 
        WHERE username != ? 
        AND role != 'admin'
    """, (uname,))
    rows = c.fetchall()

    users = []
    for r in rows:
        uid, username, profile_pic = r

        # Count joins for this user
        c.execute("SELECT COUNT(*) FROM joins WHERE joined_id=?", (uid,))
        join_count = c.fetchone()[0]

        # Check if current user already joined this user
        c.execute("SELECT 1 FROM joins WHERE joiner_id=? AND joined_id=?", (user_id, uid))
        joined = bool(c.fetchone())

        users.append({
            "username": username,
            "profile_pic": profile_pic if profile_pic else "default_avatar.png",
            "joins": join_count,
            "joined": joined
        })

    conn.close()
    return render_template("collaborate.html", users=users, current_user=uname)


@app.route("/toggle_join/<username>", methods=["POST"])
def toggle_join(username):
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    joiner_id = session["user_id"]
    joined_id = get_user_id(username)

    if not joined_id or joiner_id == joined_id:
        return jsonify({"error": "Invalid user"}), 400

    conn = get_db()
    c = conn.cursor()

    # Check if already joined
    c.execute("""
        SELECT 1 FROM joins 
        WHERE joiner_id=? AND joined_id=?
    """, (joiner_id, joined_id))

    exists = c.fetchone()

    if exists:
        # UNJOIN
        c.execute("""
            DELETE FROM joins 
            WHERE joiner_id=? AND joined_id=?
        """, (joiner_id, joined_id))
        action = "unjoined"
    else:
        # JOIN
        c.execute("""
            INSERT INTO joins (joiner_id, joined_id)
            VALUES (?, ?)
        """, (joiner_id, joined_id))
        action = "joined"

    # Count joins
    c.execute("""
        SELECT COUNT(*) FROM joins WHERE joined_id=?
    """, (joined_id,))
    count = c.fetchone()[0]

    conn.commit()
    conn.close()

    return jsonify({
        "action": action,
        "joins": count
    })

@app.route("/user_posts/<username>")
def user_posts(username):
    if "user_id" not in session:
        return redirect(url_for("login"))

    viewer_id = session["user_id"]

    conn = get_db()
    c = conn.cursor()

    # Get user info
    c.execute("SELECT id, profile_pic FROM users WHERE username=?", (username,))
    u = c.fetchone()
    if not u:
        conn.close()
        return "User not found", 404

    user_id, profile_pic = u

    # Get posts
    c.execute("""
    SELECT
        posts.id,
        posts.content,
        posts.timestamp,
        (SELECT COUNT(*) FROM likes WHERE likes.post_id = posts.id) AS likes,
        (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id) AS replies,
        users.profile_pic,
        users.username
    FROM posts
    JOIN users ON posts.user_id = users.id
    WHERE users.id=?
    ORDER BY posts.timestamp DESC""", (user_id,))


    posts = c.fetchall()

    # Count joins
    c.execute("SELECT COUNT(*) FROM joins WHERE joined_id=?", (user_id,))
    joins = c.fetchone()[0]

    # Check if viewer joined this user
    c.execute("SELECT 1 FROM joins WHERE joiner_id=? AND joined_id=?", (viewer_id, user_id))
    joined = bool(c.fetchone())

    conn.close()

    return render_template(
        "user_posts.html",
        posts=posts,
        username=username,
        profile_pic=profile_pic or "default_avatar.png",
        joins=joins,
        joined=joined
    )

@app.route("/toggle_like/<int:post_id>", methods=["POST"])
def toggle_like(post_id):
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    user_id = session["user_id"]
    conn = get_db()
    c = conn.cursor()

    c.execute(
        "SELECT 1 FROM likes WHERE user_id=? AND post_id=?",
        (user_id, post_id)
    )
    liked = c.fetchone()

    if liked:
        c.execute(
            "DELETE FROM likes WHERE user_id=? AND post_id=?",
            (user_id, post_id)
        )
        action = "unliked"
    else:
        c.execute(
            "INSERT INTO likes (user_id, post_id) VALUES (?, ?)",
            (user_id, post_id)
        )
        action = "liked"

    c.execute("SELECT COUNT(*) FROM likes WHERE post_id=?", (post_id,))
    count = c.fetchone()[0]

    conn.commit()
    conn.close()

    return jsonify({"action": action, "likes": count})

@app.route("/post/<int:post_id>", methods=["GET", "POST"])
def post_details(post_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    c = conn.cursor()

    # Post
    c.execute("""
        SELECT posts.content, posts.timestamp, users.name, users.profile_pic
        FROM posts JOIN users ON posts.user_id = users.id
        WHERE posts.id=?
    """, (post_id,))
    post = c.fetchone()

    # Comments
    c.execute("""
        SELECT comments.comment, comments.timestamp, users.name, users.profile_pic
        FROM comments JOIN users ON comments.user_id = users.id
        WHERE comments.post_id=?
        ORDER BY comments.timestamp ASC
    """, (post_id,))
    comments = c.fetchall()

    # Current user profile
    c.execute("SELECT profile_pic FROM users WHERE id=?", (session['user_id'],))
    current_user_profile = c.fetchone()[0] or 'default_avatar.png'

    conn.close()
    return render_template("post_details.html", post=post, comments=comments, post_id=post_id, current_user_profile=current_user_profile)


@app.route("/comment/<int:post_id>", methods=["POST"])
def comment(post_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    text = request.form.get("comment").strip()
    if text:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO comments (post_id, user_id, comment) VALUES (?, ?, ?)",
            (post_id, session["user_id"], text)
        )
        conn.commit()
        conn.close()

    return redirect(url_for("post_details", post_id=post_id))

@app.route("/delete_post/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    user_id = session["user_id"]
    role = session.get("role", "user")

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT user_id FROM posts WHERE id=?", (post_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Post not found"}), 404

    post_owner_id = row[0]

    if role != "admin" and post_owner_id != user_id:
        conn.close()
        return jsonify({"error": "Unauthorized"}), 403

    # Delete post, likes, and comments
    c.execute("DELETE FROM posts WHERE id=?", (post_id,))
    c.execute("DELETE FROM likes WHERE post_id=?", (post_id,))
    c.execute("DELETE FROM comments WHERE post_id=?", (post_id,))

    conn.commit()
    conn.close()
    return jsonify({"success": True, "post_id": post_id})
# --- Run ---
if __name__ == "__main__":
    app.run(debug=True)
