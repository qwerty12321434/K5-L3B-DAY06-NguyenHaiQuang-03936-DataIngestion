# Benchmark truy xuất Nghị định 13/2023/NĐ-CP

Chốt ngày **2026-11-14**, trước khi chạy A/B/C. Câu hỏi chỉ xét nội dung bản Công báo 2023 trong [corpus đã làm sạch](../starter/results/corpus_pages.jsonl). Nguồn, phiên bản PDF và cách tính trang nằm trong [manifest](../data/manifest.json).

## Bộ câu hỏi

| Tập | ID | Số câu | Câu có filter |
|---|---|---:|---|
| Dev | `q01`–`q15` | 15 | `q08`, `q14`, `q15` |
| Test | `q16`–`q20` | 5 | `q19`, `q20` |

[questions.jsonl](questions.jsonl) lưu mỗi câu trên một dòng, gồm câu hỏi, đáp án đối chiếu, ID nguồn, số trang PDF bắt đầu từ 1, đoạn bằng chứng và filter nếu có. Tập test dùng các điều và biểu mẫu khác với các câu dev. Cả hai tập cùng lấy từ một văn bản nên có chủ đề, thuật ngữ và mẫu biểu gần nhau. Kết quả test chỉ phản ánh corpus này.

Các giá trị của `filter.section` và `filter.form_number` đến từ phạm vi được nêu trong câu hỏi. Với `q14` và `q19`, đối tượng của phiếu là cá nhân hoặc tổ chức, doanh nghiệp. Đối chiếu tiêu đề mẫu trong corpus cho ra số mẫu tương ứng `"01"` và `"02"`. `q20` nêu trực tiếp số mẫu. Khi có cả hai trường, áp dụng đồng thời. A và B truy xuất trên toàn corpus. C áp dụng filter khi câu hỏi có khai báo.

## Cách chấm đã chốt

1. Truy xuất **top-3** với cùng câu hỏi, corpus, model và cosine cho A/B/C. Đầu vào truy xuất gồm `question` và với C là `filter` nếu có. `expected_answer`, `gold_source_id`, `gold_page` và `evidence_quote` dùng để chấm kết quả.
2. Một chunk được tính hit khi trỏ đúng nguồn, bao phủ trang gold và tự chứa đủ bằng chứng để trả lời câu hỏi. Có thể chuẩn hóa Unicode NFC và gộp khoảng trắng khi so khớp câu trích. Nếu đoạn trích đủ ý nhưng khác cách trình bày, xác minh thủ công và ghi đoạn, trang cùng lý do. Với một câu có nhiều đoạn tương đương trong đúng phạm vi, ghi lại tất cả nguồn hợp lệ đã xác minh và dùng chung nhãn cho ba cấu hình. Nếu bằng chứng chỉ đầy đủ khi ghép nhiều chunk, ghi nhận ở grounding.
3. Báo cáo **Hit@1** và **Evidence Hit@3** theo số câu đúng trên tổng số câu ở từng tập. Với câu có filter, ghi riêng số câu mà các chunk trả về đều tuân thủ filter. Một truy vấn trả về rỗng tính miss và không đạt kiểm tra tuân thủ filter.
4. Chọn cấu hình trên dev theo thứ tự **Evidence Hit@3 cao hơn → Hit@1 cao hơn → thời gian truy xuất trung vị trên dev thấp hơn**. Đo thời gian của bước truy xuất từ pgvector sau khi đã có vector câu hỏi, theo cùng quy trình cho cả ba cấu hình. Nếu vẫn hòa, ưu tiên cấu hình đơn giản hơn theo thứ tự A, B, C. Cố định tham số trước khi chạy test cho cả ba cấu hình và ghi rõ cấu hình đã chọn từ dev.
5. Chấm grounding và metadata bằng cùng 5 câu test. Chấm coherence tại cùng 10 vị trí nguồn là ký tự đầu của đoạn nội dung đầu tiên được gắn cả Điều và Khoản trên mỗi trang PDF 1–10. Nếu nhiều chunk phủ cùng vị trí, chấm chunk xuất hiện sớm nhất theo vị trí trong văn bản. Dùng thang điểm và cách báo cáo trong [GUIDE.md](../GUIDE.md).

## Đối chiếu evidence

Đã kiểm tra cả 20 đoạn trích có mặt nguyên văn trong trang gold của corpus và khớp với text từ cùng trang PDF sau khi chuẩn hóa khoảng trắng. Đã kiểm tra cả 5 filter khớp với metadata của trang gold.

- `q14` hỏi phiếu dành cho cá nhân. Cụm về địa chỉ nhận qua bưu điện lặp ở Mẫu 02, nhưng phiếu dành cho tổ chức, doanh nghiệp nằm ngoài phạm vi câu hỏi. Khi chấm A/B, đoạn chỉ ở Mẫu 02 vẫn là miss.
- `q20` lấy bằng chứng ở trang PDF 34, phần tiếp theo của Mẫu 03. Dựa vào số Mẫu trong metadata để truy vết phần tiếp nối.
- `q10` cần cả quy tắc không áp dụng việc xóa và điều kiện dữ liệu đã công khai theo quy định. Đoạn chỉ nhắc đến dữ liệu đã công khai chưa đủ để tính hit.

### Đọc để review

Chạy từ thư mục gốc repo trong PowerShell. Thay `q20` bằng ID câu muốn đọc.

```powershell
Get-Content .\benchmark\questions.jsonl -Encoding utf8 |
  ForEach-Object { ConvertFrom-Json -InputObject $_ } |
  Where-Object { $_.id -eq 'q20' } |
  ConvertTo-Json -Depth 5
```
