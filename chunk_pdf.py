import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from pypdf import PdfReader

FR_RE = re.compile(r"\bFR\s*#?\s*(\d+)\b", re.IGNORECASE)

def clean_text(t: str) -> str:
    # rapihin spasi & newline
    t = t.replace("\u00a0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def guess_section_title(page_text: str) -> Optional[str]:
    """
    Heuristik sederhana:
    - ambil baris pertama/dua yang kelihatan seperti judul (tidak terlalu panjang).
    - kalau nggak ketemu, None.
    """
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]
    if not lines:
        return None

    # kandidat judul: baris pendek, bukan bullet panjang
    for i in range(min(3, len(lines))):
        line = lines[i]
        if 3 <= len(line) <= 80 and not line.endswith("."):
            return line
    return None

def extract_fr_number(text: str) -> Optional[str]:
    m = FR_RE.search(text)
    return f"FR {m.group(1)}" if m else None

def chunk_by_paragraphs(text: str, max_chars: int = 1500, overlap_chars: int = 200) -> List[str]:
    """
    Chunking berbasis paragraf (untuk keyword search/FTS).
    Defaultnya: 800–1500 chars, overlap 100–200 chars.
    """
    text = clean_text(text)
    if not text:
        return []

    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    buf = ""

    def flush_buffer():
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + 2 + len(p) <= max_chars:
            buf += "\n\n" + p
        else:
            # flush dan overlap
            flush_buffer()
            if overlap_chars > 0 and chunks:
                tail = chunks[-1][-overlap_chars:]
                buf = tail + "\n\n" + p
            else:
                buf = p

    flush_buffer()
    return chunks

def build_chunks(pdf_path: str) -> List[Dict[str, Any]]:
    reader = PdfReader(pdf_path)
    all_chunks: List[Dict[str, Any]] = []
    pdf_name = Path(pdf_path).name

    current_section_title: Optional[str] = None

    for page_idx, page in enumerate(reader.pages):
        raw = page.extract_text() or ""
        page_text = clean_text(raw)

        # update section title kalau ketemu kandidat yang lebih bagus di page ini
        title_guess = guess_section_title(page_text)
        if title_guess:
            current_section_title = title_guess

        fr_number = extract_fr_number(page_text)

        # chunking per halaman, tapi dipecah per paragraf agar granular
        chunks = chunk_by_paragraphs(page_text, max_chars=1500, overlap_chars=200)

        for ci, ch in enumerate(chunks):
            # FR number bisa muncul di chunk, coba deteksi ulang supaya lebih akurat
            fr_in_chunk = extract_fr_number(ch) or fr_number

            all_chunks.append({
                "id": f"p{page_idx+1:03d}_c{ci+1:02d}",
                "page": page_idx + 1,  # 1-indexed
                "section_title": current_section_title,
                "fr_number": fr_in_chunk,
                "source": pdf_name,
                "text": ch,
            })

    return all_chunks

def save_jsonl(chunks: List[Dict[str, Any]], out_path: str):
    with open(out_path, "w", encoding="utf-8") as f:
        for row in chunks:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    # Ubah path ini sesuai lokasi file kamu
    pdf_path = "knowledge_tanyaDewi.pdf"
    out_path = "chunks_fr_ai.jsonl"

    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    chunks = build_chunks(pdf_path)
    save_jsonl(chunks, out_path)

    print(f"✅ Done. Total chunks: {len(chunks)}")
    print(f"✅ Saved: {out_path}")
    # contoh 3 chunk pertama
    for c in chunks[:3]:
        print("\n---")
        print({k: c[k] for k in ["id", "page", "section_title", "fr_number"]})
        print(c["text"][:200], "...")
