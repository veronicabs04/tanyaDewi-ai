Backend chat menggunakan FastAPI untuk menerima request dari client, melakukan auth ke Laravel via bearer token, lalu menjalankan Google Agent Dev Kit (ADK) dengan model Gemini untuk menghasilkan jawaban. History chat disimpan ke SQLite (data/chat.db). Retrieval tool (RAG) tersedia melalui my_agent.retrieval_tool.search_report yang membaca data/knowledge.db.

# PRASYARAT
1. Python 3.11+
2. Laravel API berjalan di http://127.0.0.1:8000
3. .env berisi API key Gemini

Contoh .env :
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=YOUR_GOOGLE_API_KEY

# INSTALL & SETUP
## dari folder project
python -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt

## Jika belum ada python-dotenv, install:
pip install python-dotenv

# MENJALANKAN SERVER (PORT 8001)
Jalankan FastAPi dengan uvicorn:

uvicorn my_agent.app.server:app --reload --host 127.0.0.1 --port 8001

# ENDPOINT API
## 1. Start Chat
POST /chat/start
Endpoint ini membuat percakapan baru dan menghasilkan `conversation_id`.
Client wajib menyimpan `conversation_id` ini untuk request selanjutnya.

Headers:
Authorization: Bearer <token>
Accept: application/json

Response:
{
  "conversation_id": "uuid"
}


## 2. Send Message
POST /chat/send
Mengirim pesan user untuk diproses oleh ADK.
Client mengirim pesan ke agent.
`conversation_id` HARUS berasal dari response `/chat/start`,
bukan dibuat manual oleh client.

Headers:
- Authorization: Bearer <token>
- Accept: application/json
- Content-Type: application/json

Body:
{
  "conversation_id": "uuid",
  "message": "Halo, aku mau tanya..."
}

Response:
{
  "conversation_id": "uuid",
  "reply": "Jawaban dari agent..."
}

Notes:
- Jika conversation_id bukan milik user → 403 Forbidden
- Jika ADK error → 500 (traceback ditampilkan karena debug handler)

## 3. Get Chat History
GET /chat/{conversation_id}/history
Mengambil seluruh pesan dalam conversation (dari data/chat.db).
Mengambil seluruh riwayat pesan untuk satu percakapan.

Catatan:
- `conversation_id` adalah ID yang dihasilkan oleh server saat memanggil `/chat/start`
- Client tidak perlu dan tidak boleh membuat `conversation_id` sendiri

Headers:
- Authorization: Bearer <token>

Response:
{
  "conversation_id": "uuid",
  "messages": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ]
}

# CONTOH TESTING VIA POWERSHELL
## Start Chat
$token="PASTE_TOKEN"
irm -Method Post `
  -Uri "http://127.0.0.1:8001/chat/start" `
  -Headers @{ Authorization="Bearer $token"; Accept="application/json"}
Simpan conversation_id, lalu send message:

## Send Message
$token="PASTE_TOKEN"
$cid="PASTE_CONVERSATION_ID"

$body = @{ conversation_id=$cid; message="Halo Dewi" } | ConvertTo-Json

irm -Method Post `
  -Uri "http://127.0.0.1:8001/chat/send" `
  -Headers @{ Authorization="Bearer $token"; Accept="application/json" } `
  -ContentType "application/json" `
  -Body $body

# Data & Database
1. Chat DB: data/chat.db
2. Knowledge DB (FTS untuk RAG): data/knowledge.db
## Tool RAG (my_agent/retrieval_tool.py) akan query:
- table: report_fts
- columns: chunk, source, page

# TROUBLESHOOTING
1) Eror : Missing Key inputs argument (api_key)
Penyebab: env var belum kebaca / .env tidak diload sebelum import agent.
Solusi:
- Pastikan load_dotenv() dipanggil di paling atas server.py sebelum import root_agent
- Pastikan .env berisi GOOGLE_API_KEY=...
- Restart uvicorn

2) Reply selalu sama / tidak relevan dokumen
- instruction terlalu ketat (menolak jika tidak ada di dokumen)
- atau retrieval search_report selalu mengembalikan kosong (DB belum terisi / tabel tidak ada / query tidak match)

