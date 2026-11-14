# RAG Benchmark – Nghị định 13/2023/NĐ-CP

## Corpus
- PDF: `data/corpus/13_2023_ND-CP.pdf`
- 37 trang, scan dạng ảnh; OCR là bắt buộc.
- Nội dung Nghị định: trang PDF 1–30; biểu mẫu phụ lục: trang PDF 31–37.
- `gold_page` là số trang vật lý trong PDF, không phải số trang in trên Công báo.

## OCR
`src/ingestion.py` dùng pypdf trước, sau đó OCR các trang có text rỗng/quá ngắn bằng PyMuPDF + Tesseract.
Cần Tesseract OCR cài trên máy và traineddata `vie` + `eng`.

## Metadata pháp lý
- `chapter`, `article`, `article_title`, `clause`, `point`
- Metadata được trích từ nội dung nguồn, không lấy từ gold labels.
- Metadata theo từng Điều/Khoản được gắn vào chunks; trạng thái Chương/Điều được truyền qua ranh giới trang.

## Retrieval strategies
- A: fixed-size chunking.
- B: legal-structure-aware chunking.
- C: cùng chunks với B, bổ sung metadata filter theo Điều.
- Cùng embedding model, vector store và Top-3 cho cả ba chiến lược.

## Benchmark
20 câu: 15 Dev + 5 Test. Các gold pages dùng số trang PDF vật lý. Evidence quote lấy từ bản scan hiển thị; cần rà soát thủ công OCR text/quote trước khi chốt kết quả chính thức.
