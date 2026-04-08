import sqlite3

conn = sqlite3.connect(r'C:\Users\thepr\pipe-inspector-pyside\data\pipe_inspector.db')

# ดู records ก่อน
rows = conn.execute("SELECT piece_id, timestamp FROM inspections").fetchall()
print("Current records:")
for r in rows:
    print(f"  {r[0]}  |  {r[1]}")

# แก้ timestamp — เปลี่ยน piece_id ตามที่ต้องการ
piece_id = "BATCH-6F69C4-0002"
new_ts   = "2025-12-01T00:00:00+00:00"

conn.execute("UPDATE inspections SET timestamp = ? WHERE piece_id = ?", (new_ts, piece_id))
conn.commit()
print(f"\nUpdated {piece_id} -> {new_ts}")

conn.close()
