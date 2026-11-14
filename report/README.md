# Hồ sơ báo cáo Day 6

Bộ hồ sơ gồm đúng 6 file:

1. `coherence_audit_scored.csv` — 90 bản ghi coherence đã có nhãn 0/1/2 và lý do.
2. `metadata_grounding_audit.csv` — 43 bản ghi metadata và grounding đã có nhãn.
3. `metrics_dev_test_and_audit.csv` — số tử/mẫu Hit@1, Evidence Hit@3, chunk count, audit summary, filter compliance.
4. `benchmark_gold_and_full_traces.json` — 20 câu benchmark/gold và traces Top-3 A/B/C (Dev + Test).
5. `2A202603545_VuQuangThai.md` — báo cáo theo mẫu cá nhân.
6. `README.md` — hướng dẫn đọc bộ hồ sơ và phương pháp.

## Quy tắc chấm đã áp dụng
- Coherence: 0 = chunk khó hiểu/bị cắt rõ; 1 = hiểu một phần hoặc thiếu ngữ cảnh; 2 = tương đối tự hiểu và giữ trọn ý.
- Metadata: 1 khi source/page khớp và article metadata có giá trị, không mâu thuẫn rõ; 0 khi thiếu/sai trường hoặc article không khớp.
- Grounding: 0 = không có evidence đủ liên quan; 1 = liên quan nhưng chưa đủ điều kiện/ngữ cảnh; 2 = chứa evidence quote gold hoặc bằng chứng trực tiếp đủ truy nguồn.
- Nhãn được gán bằng cách đối chiếu chunk text, metadata, câu hỏi và gold evidence.

## Cách đọc kết quả
- Retrieval metrics tự động: xem `metrics_dev_test_and_audit.csv`.
- Nhãn thủ công sơ bộ: xem hai CSV audit.
- Trace đầy đủ: `benchmark_gold_and_full_traces.json`, gồm câu hỏi, gold và top3 chunk ID/text/score/metadata/evidence check.
- Báo cáo kết luận chỉ áp dụng cho corpus Nghị định 13/2023/NĐ-CP và benchmark 20 câu; Test có 5 câu nên chỉ mang tính minh họa.

## Phạm vi hồ sơ
Bộ này tập trung vào đúng 6 file báo cáo. Mã nguồn, PDF corpus và môi trường cài đặt nằm trong project gốc đã chạy, không được nhân bản trong ZIP này. Nếu cần tái chạy, dùng README setup/run của project nguồn và giữ nguyên model, database, corpus, benchmark.
