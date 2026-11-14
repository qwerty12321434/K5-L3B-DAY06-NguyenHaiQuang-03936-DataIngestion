# Kết quả benchmark truy xuất A/B/C

Chạy ngày **14/11/2026**, trên 36 trang của bản Công báo 2023. Dùng Qwen3-Embedding-0.6B Q8_0 qua Ollama, 1.024 chiều, và tìm kiếm cosine chính xác trong pgvector. Cấu hình, thời gian ingest và phiên bản nằm trong [ingestion_report.json](ingestion_report.json). Tiêu chí chấm và quy tắc chọn cấu hình đã ghi trong [benchmark](../../benchmark/README.md).

## Retrieval

| Tập | Cấu hình | Hit@1 | Evidence Hit@3 | Top-3 đúng phạm vi trên câu có filter | Trung vị truy xuất |
|---|---|---:|---:|---:|---:|
| Dev | A | 4/15 | 6/15 | 2/3 | 1,37 ms |
| Dev | B | 8/15 | 11/15 | 1/3 | 1,42 ms |
| Dev | C | 9/15 | 11/15 | 3/3 | 1,32 ms |
| Test | A | 2/5 | 2/5 | 0/2 | 1,27 ms |
| Test | B | 2/5 | 2/5 | 0/2 | 1,34 ms |
| Test | C | 3/5 | 3/5 | 2/2 | 1,30 ms |

**C được chọn trên dev** lúc 10:53:24 (UTC+7), trước lần truy xuất test. B và C hòa Evidence Hit@3. C thắng ở Hit@1, nhờ đưa bằng chứng của `q14` từ hạng 2 lên hạng 1. [Bản ghi chọn cấu hình](dev_selection.json)

Mỗi câu được tạo một vector truy vấn dùng cho cả ba cấu hình. Truy xuất mỗi cấu hình ba lần để đo thời gian, luân phiên thứ tự A/B/C và lấy trung vị mỗi câu. Thời gian gồm gọi pgvector qua driver và nhận kết quả, với thời gian embedding câu hỏi nằm ngoài phép đo. Chênh lệch dưới 0,2 ms trong lần chạy này có thể do nhiễu đo.

Đã đọc các đoạn trả về không khớp gold để tìm chứng cứ tương đương đủ nghĩa và cùng phạm vi. Hai file `evidence_review_*.jsonl` trống vì lần chạy này không có kết quả nào cần nâng thành hit sau đối chiếu. Các trace lưu từng hạng, văn bản, điểm cosine, metadata, gold, filter áp dụng và nhãn hit. [Trace dev](trace_dev.jsonl) · [Trace test](trace_test.jsonl)

## Chấm mẫu

| Cấu hình | Coherence /2, 10 vị trí | Metadata đủ và đúng, 5 câu test × top-3 | Grounding test, số câu ở mức 0 / 1 / 2 | Grounding trung bình /2 |
|---|---:|---:|---:|---:|
| A | 1,00 | 15/15 | 1 / 2 / 2 | 1,20 |
| B | 1,70 | 15/15 | 1 / 2 / 2 | 1,20 |
| C | 1,70 | 15/15 | 0 / 2 / 3 | 1,60 |

- **Coherence.** Mình đọc chunk tại 10 vị trí đã chốt trên trang PDF 1–10. B thường giữ trọn câu và đoạn hơn A. B/C vẫn thiếu tiêu đề của Điều ở một số đoạn bắt đầu từ trang tiếp theo, như trang 6 và 7. [Nhãn coherence từng vị trí](audit_coherence.csv)
- **Metadata.** Đối chiếu 45 kết quả với nguồn, vị trí trong corpus, số trang in và phạm vi trong PDF. Metadata đúng giúp truy ngược đoạn văn và cho C giới hạn đúng mẫu, kể cả trang PDF 34 tiếp nối Mẫu 03. Một chunk có metadata đúng vẫn có thể nằm ngoài phạm vi câu hỏi hoặc thiếu bằng chứng như `q19`. [Nhãn metadata từng kết quả](audit_metadata.csv)
- **Grounding.** Codex đọc top-3 và chấm độ đủ của bằng chứng theo thang 0–2. Ở `q18`, các đoạn có thông tin liên quan nhưng thiếu ngữ cảnh thông báo sau khi chuyển thành công. Ở `q19`, cả ba tìm thấy Mẫu 02 nhưng thiếu trường cần trả lời. [Nhãn grounding từng câu](audit_grounding.csv)

## Ảnh hưởng của chiến lược

| Cấu hình | Số chunk tìm kiếm | Tổng ký tự | Ingest sau làm nóng | Chi phí API |
|---|---:|---:|---:|---:|
| A | 163 | 73.002 | 2,67 giây | 0 |
| B | 190 | 66.652 | 2,94 giây | 0 |
| C | 190, dùng chung B | 66.652, dùng chung B | 0 giây bổ sung | 0 |

**A → B.** Giữ ranh giới Điều và đoạn tăng Hit@3 dev từ 6 lên 11 câu, với 27 chunk nhiều hơn và thời gian ingest tăng khoảng 0,27 giây trong lần đo. Test hòa 2/5. Ở `q13` (Điều 39, trang PDF 28), A truy xuất hai chunk chứa hai phần của cùng câu nhưng không có chunk nào tự chứa đủ bằng chứng. B giữ trọn câu trong index nhưng xếp chunk đó ngoài top-3.

**B → C.** Filter cải thiện Hit@1 dev một câu và Hit@3 test một câu, với cùng văn bản, vector và chi phí index. Ở `q20` (trang PDF 34, Mẫu 03), B trả về các đoạn điều khoản trang 18 và 5. C lọc đúng Mẫu 03 và đưa trường cần tìm lên hạng 1. Ở `q19` (trang PDF 32, Mẫu 02), C tuân thủ filter trên cả ba kết quả nhưng chunk chứa địa chỉ trụ sở vẫn ngoài top-3. Hiệu quả của C phụ thuộc vào phạm vi được cung cấp chính xác.

**Lỗi còn lại.** Với `q18` (Điều 25, Khoản 4, trang PDF 22), A đưa phần đầu câu vào hạng 3 nhưng cắt mất nội dung, hình thức và thời điểm phải thông báo. B/C lưu đủ Khoản 4 nhưng ưu tiên các đoạn về hồ sơ ở trang 21–22. Đây là hai điểm khác nhau có thể kiểm tra trực tiếp trong trace test.

Các nhãn định tính do Codex đọc và ghi, cần học viên kiểm tra khi dùng trong bài nộp. Test chỉ có 5 câu từ một văn bản. Chênh lệch một câu giữa B và C chưa đủ để suy rộng ra corpus khác.
