 c.execute("""
        SELECT posts.content, posts.timestamp
        FROM posts
        WHERE posts.user_id=?
        ORDER BY posts.timestamp DESC
    """, (user_id,))