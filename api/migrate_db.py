import sqlite3
import os

DB_PATH = "./data/nanobot.db"

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}, skipping migration.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 获取当前列名
    cursor.execute("PRAGMA table_info(chat_logs)")
    columns = [col[1] for col in cursor.fetchall()]

    new_columns = [
        ("session_id", "TEXT"),
        ("sender_name", "TEXT"),
        ("session_name", "TEXT")
    ]

    for col_name, col_type in new_columns:
        if col_name not in columns:
            print(f"Adding column {col_name} to chat_logs...")
            try:
                cursor.execute(f"ALTER TABLE chat_logs ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                print(f"Error adding {col_name}: {e}")
        else:
            print(f"Column {col_name} already exists.")

    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
