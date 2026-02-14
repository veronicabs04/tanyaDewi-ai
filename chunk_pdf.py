import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from pypdf import PdfReader

HD_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
CATEGORY_MAP = {
    "Knowledge_Branding_dan_Konten_Promosi_UMKM_Pala": "Branding & Konten Promosi",
    "Knowledge_Digital_Marketing_dan_Penjualan_UMKM_Pala": "Digital Marketing & Penjualan",
    "Knowledge_Kemasan_Warna_dan_Visual_Produk_Pala": "Kemasan, Warna & Visual",
    "Knowledge_Penentuan_Harga_Jual_UMKM_Pala": "Penentuan Harga",
    "Knowledge_Pengolahan_dan_Kualitas_Buah_Pala": "Pengolahan & Kualitas",
    "Knowledge_Resep_Olahan_Buah_Pala_UMKM": "Resep Olahan",
}

def clean_text(t: str) -> str:
    t = (t or "").replace("\u00a0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def chunk_by_paragraphs(text: str, max_chars: int = 1500, overlap_chars: int = 200) -> List[str]:
    text = clean_text(text)
    if not text:
        return []

    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    buf = ""

    def flush():
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
            flush()
            if overlap_chars > 0 and chunks:
                tail = chunks[-1][-overlap_chars:]
                buf = tail + "\n\n" + p
            else:
                buf = p

    flush()
    return chunks

def split_by_headings(text: str):
    text = clean_text(text)
    if not text:
        return []

    matches = list(HD_RE.finditer(text))
    if not matches:
        return [{"h1": None, "h2": None, "h3": None, "body": text}]

    blocks = []
    current = {"h1": None, "h2": None, "h3": None}

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        if level == 1:
            current = {"h1": title, "h2": None, "h3": None}
        elif level == 2:
            current["h2"] = title
            current["h3"] = None
        else:
            current["h3"] = title

        blocks.append({"h1": current["h1"], "h2": current["h2"], "h3": current["h3"], "body": body})

    return blocks

def build_chunks(pdf_path: str, max_chars: int = 1500, overlap_chars: int = 0) -> List[Dict[str, Any]]:
    reader = PdfReader(pdf_path)
    pdf_name = Path(pdf_path).name
    stem = Path(pdf_path).stem

    category = CATEGORY_MAP.get(stem, stem) 
    all_chunks: List[Dict[str, Any]] = []

    for page_idx, page in enumerate(reader.pages):
        page_text = clean_text(page.extract_text() or "")
        if not page_text:
            continue

        blocks = split_by_headings(page_text)
        for bi, b in enumerate(blocks):
            body = b["body"]
            if not body:
                continue

            if len(body) <= max_chars:
                subchunks = [body]
            else:
                subchunks = chunk_by_paragraphs(body, max_chars=max_chars, overlap_chars=0)


            for ci, ch in enumerate(subchunks):

                all_chunks.append({
                    "id": f"{stem}__p{page_idx+1:03d}_b{bi+1:02d}_c{ci+1:02d}",
                    "source": pdf_name,
                    "category": category,
                    "section_title": b["h2"] or b["h1"],
                    "page": page_idx + 1,
                    "h1": b["h1"],
                    "h2": b["h2"],
                    "h3": b["h3"],
                    "text": ch,
                })

    return all_chunks

def save_jsonl(chunks: List[Dict[str, Any]], out_path: str):
    with open(out_path, "w", encoding="utf-8") as f:
        for row in chunks:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    pdf_dir = Path("knowledge_pala")  
    out_path = "chunks_fr_ai.jsonl"

    if not pdf_dir.exists():
        raise FileNotFoundError(f"Folder not found: {pdf_dir}")

    all_chunks = []

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError("No PDF files found in folder")

    for pdf_path in pdf_files:
        print(f"📄 Processing: {pdf_path.name}")
        chunks = build_chunks(str(pdf_path))
        all_chunks.extend(chunks)

    save_jsonl(all_chunks, out_path)

    print(f"\n✅ Done. Total PDFs: {len(pdf_files)}")
    print(f"✅ Total chunks: {len(all_chunks)}")
    print(f"✅ Saved: {out_path}")

    # contoh 3 chunk pertama
    for c in all_chunks[:3]:
        print("\n---")
        print({k: c[k] for k in ["id", "source", "page", "h1", "h2", "h3"]})
        print(c["text"][:200], "...")
