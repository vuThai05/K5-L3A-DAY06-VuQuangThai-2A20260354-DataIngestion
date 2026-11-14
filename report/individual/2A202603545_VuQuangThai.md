# Báo cáo cá nhân — Day 6

**Tác giả:** Vũ Quang Thái   
**Corpus:** Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân.

## 1. Dữ liệu và pipeline

- **Nguồn:** PDF scan Nghị định 13/2023/NĐ-CP, 37 trang. Corpus dùng trong lần chạy nằm trong `data/corpus/13_2023_ND-CP.pdf` của bộ project trước; bộ hồ sơ 6 file này đóng gói benchmark, gold và traces để giảm dung lượng.
- **Quy tắc trang:** `page`/`gold_page` là số trang vật lý PDF, bắt đầu từ 1.
- **OCR:** PyMuPDF render + Tesseract `vie+eng`; `pypdf` được thử trước, OCR khi trang không có hoặc có quá ít text. OCR còn lỗi nhận dạng dấu/ký tự tiếng Việt.
- **Embedding:** provider OpenAI, model `text-embedding-3-small`, 1536 chiều (theo `experiment.json`).
- **Vector store:** PostgreSQL + pgvector; cosine similarity; Top-3.
- **Lệnh chạy:** `pip install -r requirements.txt`; `pip install -r requirements-ocr.txt`; `python -m src.run` từ thư mục `starter/`.
- **Chi phí:** `$0.004` - 73 requests - 198,647 input token.
- **Phiên bản thư viện:** phiên bản chính xác tại thời điểm chạy không được ghi trong `experiment.json`. Thời điểm chạy gốc và seed cũng không được lưu. Ngày lập hồ sơ: 2026-11-14.
- **Metadata:** source, page, chapter, article, article_title, clause, point. Filter C dùng `article`.

## 2. Cấu hình và benchmark

| Chiến lược | Cấu hình |
|---|---|
| A | Fixed-size, 900 ký tự, overlap 50; 94 chunks |
| B | Legal-structure-aware, ưu tiên ranh giới Điều/Khoản; max 900; 119 chunks |
| C | Cùng chunks và document vectors với B; thêm metadata filter theo Điều; 119 chunks |

Cùng embedding model, vector store, cosine và Top-3. B–C khác nhau ở bước lọc metadata; C tái sử dụng chunks/vectors của B.

Benchmark có 20 câu hỏi: 15 Dev, 5 Test. Gold gồm source ID, trang PDF, evidence quote và filter. Nội dung benchmark/gold và full traces nằm trong `benchmark_gold_and_full_traces.json`.

**Tiêu chí chọn chiến lược:** ưu tiên Evidence Hit@3, sau đó grounding, metadata usefulness, coherence và tài nguyên. Đây là tiêu chí lựa chọn trong corpus này, không dùng để tuyên bố tốt nhất tổng quát.

## 3. Kết quả retrieval

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunks | Ingest + embedding (s) |
|---|---|---:|---:|---:|---:|
| A | Dev | 10/15 (66.67%) | 13/15 (86.67%) | 94 | 6.390 |
| A | Test | 2/5 (40.00%) | 4/5 (80.00%) | 94 | 6.390 |
| B | Dev | 12/15 (80.00%) | 13/15 (86.67%) | 119 | 1.765 |
| B | Test | 5/5 (100%) | 5/5 (100%) | 119 | 1.765 |
| C | Dev | 13/15 (86.67%) | 15/15 (100%) | 119 | 0.105 |
| C | Test | 5/5 (100%) | 5/5 (100%) | 119 | 0.105 |

- Thời gian là ingest + embedding được ghi trong experiment, không phải query latency. C tái sử dụng vectors của B nên thời gian C không so sánh trực tiếp với A/B.

## 4. Đánh giá năm khía cạnh

### 4.1 Retrieval accuracy

- A: Dev Hit@1 10/15, Evidence Hit@3 13/15; Test 2/5 và 4/5.
- B: Dev 12/15 và 13/15; Test 5/5 và 5/5.
- C: Dev 13/15 và 15/15; Test 5/5 và 5/5.
- Đây là kiểm tra evidence theo source/page/quote. Nếu evidence bị tách qua nhiều chunk, strict single-chunk metric có thể không ghi nhận; không thay đổi định nghĩa metric chính.

### 4.2 Chunk coherence

Mẫu audit lấy từ 10 trang nội dung đầu, các chunk bao phủ những vị trí đã chọn; do mỗi chiến lược tạo số chunk khác nhau nên số bản ghi lần lượt A=26, B=32, C=32 (không phải 10 chunk/strategy).

| Strategy | Nhãn 0 | Nhãn 1 | Nhãn 2 | Trung bình /2 |
|---|---:|---:|---:|---:|
| A | 13 | 11 | 2 | 0.58 |
| B | 8 | 17 | 7 | 0.97 |
| C | 8 | 17 | 7 | 0.97 |

Nhãn AI sơ bộ dựa trên dấu hiệu chunk bị cắt, độ tự hiểu và dấu mốc Điều/Chương. C dùng cùng chunks với B nên coherence bằng nhau. Các nhãn và lý do được ghi từng dòng trong `coherence_audit_scored.csv`; tác giả cần rà soát lại.

### 4.3 Metadata usefulness

Audit 5 câu Test × Top-3; thực tế A=15, B=15, C=13 bản ghi được trả về.

| Strategy | Metadata đủ và đúng |
|---|---:|
| A | 10/15 |
| B | 13/15 |
| C | 12/13 |

Filter compliance theo câu hỏi Test có filter (5 câu):
- A: 1/5 câu có toàn bộ kết quả Top-3 tuân thủ filter.
- B: 2/5.
- C: 5/5.

`source` và `page` giúp truy ngược về tài liệu/trang; `article` hỗ trợ lọc theo Điều. Hạn chế: parser có thể thiếu/sai `article`, đặc biệt khi chunk cắt qua ranh giới Điều hoặc OCR làm hỏng heading. Nhãn metadata từng dòng ở `metadata_grounding_audit.csv`.

### 4.4 Grounding quality

Đây là đánh giá **evidence retrieval**, không phải độ chính xác câu trả lời của LLM. Nhãn 0/1/2 được gán sơ bộ cho các chunk trả về trên Test:

| Strategy | 0: không có | 1: chưa đủ | 2: đủ | Trung bình /2 |
|---|---:|---:|---:|---:|
| A | 5 | 7 | 3 | 0.87 |
| B | 1 | 10 | 4 | 1.20 |
| C | 0 | 9 | 4 | 1.31 |

C chỉ có 13 kết quả trong audit do q18 trả về 1 chunk; mẫu grounding vì vậy không cân bằng hoàn toàn giữa chiến lược. Điểm là nhãn AI hỗ trợ, tác giả cần xác nhận.

### 4.5 Data strategy impact

- **A–B:** B tăng Hit@1 Dev 10/15 → 12/15; Test 2/5 → 5/5. Legal-aware chunking có vẻ giữ ngữ cảnh Điều/Khoản tốt hơn trong corpus này, đổi lại tạo nhiều chunks hơn (119 so với 94).
- **B–C:** C tăng Dev Hit@1 12/15 → 13/15 và Evidence Hit@3 13/15 → 15/15; Test không đổi (5/5 cho cả hai). Metadata filter giúp thu hẹp phạm vi, nhưng phụ thuộc chất lượng metadata và có thể loại evidence nếu parser sai.
- **Tài nguyên:** A/B/C có lần lượt 94/119/119 chunks. Thời gian C thấp vì tái sử dụng B vectors; không thể xem đó là phép đo công bằng của chi phí ingest độc lập.
- **Chọn trong phạm vi bài:** B là baseline đơn giản; C hữu ích khi biết Điều cần truy vấn và metadata đã được kiểm chứng. Không đủ cơ sở để tuyên bố C tốt hơn B trên Test.

## 5. Trường hợp lỗi, hạn chế và thiên lệch

- q17 Test: A không hit trong Top-3, B/C hit; đây là ví dụ minh họa lợi ích cấu trúc pháp lý.
- q18 Test: A có kết quả cùng trang nhưng quote cần review; B/C tìm được evidence. OCR và chunk boundary có thể ảnh hưởng strict quote matching.
- Có chunk B với `article=null` dù nội dung có heading Điều; metadata parser còn hạn chế.
- OCR nhầm dấu/ký tự tiếng Việt; có thể làm giảm matching và embedding quality.
- Test chỉ có 5 câu; một câu tương đương 20 điểm phần trăm. Không suy rộng ra tài liệu khác.
- Tác giả chịu trách nhiệm rà soát là Vũ Quang Thái. Nhãn trong audit được AI hỗ trợ đề xuất vì deadline; có nguy cơ thiên lệch và không thay thế chấm độc lập của con người. Cần đọc lại từng dòng sau khi nộp.
- Timestamp chạy gốc, exact package versions, seed và billing export không có trong artifacts. Không bịa các thông tin này.

## 6. Kết luận

Trong thử nghiệm trên Nghị định 13/2023/NĐ-CP, legal-aware chunking (B) cải thiện rõ Hit@1 so với fixed-size (A). Metadata filtering (C) cải thiện một phần kết quả Dev nhưng không tăng điểm Test so với B,nên dùng B làm baseline, bổ sung C khi metadata Điều đáng tin cậy. Kết quả chỉ minh họa cho corpus và benchmark hiện tại; cần tăng số câu Test, cải thiện OCR/parser.