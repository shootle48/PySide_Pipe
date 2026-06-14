# RS485 output writer — ไฟล์สำรอง 2 เวอร์ชัน

ไฟล์ที่แอปใช้งานจริงคือ **`core/rs485_worker.py`** (ตอนนี้ = เวอร์ชัน QUEUE)
โฟลเดอร์นี้เก็บ "ตัวสำรอง" ไว้ก็อปไปวางทับเวลาต้องสลับ — ทั้งสองไฟล์เป็น
`rs485_worker.py` ที่สมบูรณ์ (drop-in) ใช้ชื่อ class `RS485OutputWriter` เหมือนกัน
**ไม่ต้องแก้ import หรือไฟล์อื่นเลย**

| ไฟล์ | วิธีส่ง pulse | ใช้เมื่อ |
|------|--------------|---------|
| `rs485_worker_QUEUE.py` ✅ | writer thread เดียว + queue (ทีละลูก ไม่ซ้อน) | **default — ใช้ตัวนี้** |
| `rs485_worker_THREADED.py` ⚠️ | spawn เธรดต่อ verdict (อาจซ้อนถ้าชิ้นถี่) | fallback ถ้า queue มีปัญหา |

ทั้งสองเวอร์ชันมี `bus_lock` กัน Modbus frame ชน (race) เหมือนกัน
ต่างแค่ "การกัน pulse ซ้อน" ที่ QUEUE ทำได้ดีกว่า

## วิธีสลับ (Windows)
1. ก็อปเนื้อหาไฟล์เวอร์ชันที่ต้องการ (เช่น `rs485_worker_THREADED.py`)
2. วางทับ `core/rs485_worker.py` ทั้งไฟล์
3. ปิด-เปิดโปรแกรมใหม่
4. ดู log ตอน start จะบอกว่าใช้ตัวไหน:
   - QUEUE → `RS485OutputWriter: ... (queued single-writer)`
   - THREADED → `RS485OutputWriter [THREADED]: ... (spawn-per-verdict)`

> หมายเหตุ: `core/rs485_worker.py` ปัจจุบัน = สำเนาเดียวกับ `rs485_worker_QUEUE.py`
> ถ้าแก้ logic อื่น (เช่น poll/debounce) ในตัวจริง อย่าลืมอัปเดตไฟล์สำรองให้ตรงด้วย
