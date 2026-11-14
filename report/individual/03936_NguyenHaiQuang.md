# Báo cáo cá nhân · Day 06 Data Ingestion

**Nguyễn Hải Quang · MSSV 03936 · K5-L3B**

**Ngày chốt kết quả 14/11/2026**

## 1. Dữ liệu và pipeline

Bài thực nghiệm truy xuất bằng chứng cho câu hỏi tiếng Việt trên bản Nghị định 13/2023/NĐ-CP trong Công báo số 685 + 686 ngày 30/04/2023. Corpus dùng **36/37 trang PDF**, gồm trang 1–29 của thân Nghị định và trang 31–37 chứa sáu biểu mẫu. Trang 30 là danh mục biểu mẫu. Tệp PDF được lấy từ nguồn Công báo công khai cho bài thực hành. Repo hướng dẫn lấy lại bản gốc từ nguồn và kiểm tra bằng SHA-256. Phạm vi câu hỏi là nội dung bản Công báo này. [Nguồn Công báo](https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-13-2023-nd-cp-39228.htm) · [Manifest và quy tắc tính trang](../../data/manifest.json)

Pipeline đọc từng trang bằng **pypdf 6.21.4** ở chế độ layout, chuẩn hóa Unicode NFC, bỏ đầu trang Công báo lặp lại và nối các dòng bị ngắt khi dàn trang. Cách trích xuất theo bố cục khắc phục lỗi tách chữ tiếng Việt ở các mẫu đã kiểm tra. Một số ký tự thừa ở khối chữ ký và chú thích được sửa sau khi đối chiếu PDF. Cả 37 trang đều trích được chữ, corpus sau chọn trang có **66.652 ký tự**. Biểu mẫu nhiều cột được đưa về văn bản tuyến tính, nên thứ tự trường cần được đối chiếu với PDF khi lấy bằng chứng.

Mỗi chunk giữ `document_id`, URL `source`, `page` theo số trang PDF, `printed_page` và khoảng ký tự trong trang sạch. `section` phân biệt thân Nghị định với phụ lục. `form_number` được lấy từ tiêu đề biểu mẫu và giữ qua trang tiếp nối, ví dụ Mẫu 03 ở trang PDF 33–34. Các trường này cho phép truy nguồn và lọc đúng phạm vi câu hỏi. [Corpus sạch](../../starter/results/corpus_pages.jsonl) · [Các chunk và metadata](../../starter/results/chunks.jsonl)

| Thành phần | Cấu hình của lần chạy |
|---|---|
| Embedding | Qwen3-Embedding-0.6B qua Ollama **0.40.16**, tag `qwen3-embedding:0.6b`, Q8_0, **1.024 chiều**. Digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`. |
| Đầu vào model | Chunk được encode nguyên văn, theo lô 16. Mỗi câu hỏi được thêm chỉ dẫn retrieval tiếng Anh theo định dạng `Instruct/Query` và dùng cùng một vector cho A/B/C. |
| Lưu trữ và truy xuất | PostgreSQL **16.16**, pgvector **0.8.7**, cosine chính xác, lấy top-3. ID ổn định theo tài liệu, trang, khoảng ký tự và nội dung. Upsert dọn chunk cũ của tài liệu được nạp lại trong cùng giao dịch. Lần upsert lặp giữ **163 bản ghi A và 190 bản ghi B**. |
| Môi trường và chi phí | Pipeline Python 3.13 trong Docker, Ollama chạy local trên máy Windows. Chi phí API **0** cho cả ba cấu hình. |

[Lệnh tái lập](../../starter/README.md) · [Số liệu ingest, model và log kiểm tra](../../starter/results/ingestion_report.json)

## 2. Cấu hình và benchmark

| Cấu hình | Cách chia chunk trong từng trang | Truy xuất |
|---|---|---|
| A | Cửa sổ **500 ký tự**, overlap **50 ký tự**. | Xếp hạng cosine toàn bộ tập A. |
| B | Giữ ranh giới Điều, ưu tiên đoạn hoặc dòng biểu mẫu, tối đa **500 ký tự** và không overlap. Đoạn quá dài được tách tại khoảng trắng khi có thể. | Xếp hạng cosine toàn bộ tập B. |
| C | Dùng cùng chunk và vector của B. | Thêm filter `section` và/hoặc `form_number` khi câu hỏi cung cấp phạm vi, rồi xếp hạng cosine. |

Bộ benchmark gồm **20 câu** được chốt trước thí nghiệm, với **15 dev** (`q01`–`q15`) và **5 test** (`q16`–`q20`). Có **5 câu kiểm tra filter** gồm 3 dev và 2 test. Mỗi câu lưu đáp án, ID tài liệu, trang PDF và đoạn trích để đối chiếu. Cả 20 đoạn trích được kiểm tra trên corpus sạch và text trích từ cùng trang PDF. Dev và test lấy bằng chứng từ các Điều và biểu mẫu khác nhau trong cùng văn bản.

Một kết quả được tính hit nếu chunk đúng nguồn, đúng phạm vi và tự chứa đủ bằng chứng để trả lời. Hit@1 xét kết quả đầu, Evidence Hit@3 xét ba kết quả đầu. Mỗi trace lưu thứ hạng, điểm cosine, nội dung, metadata và gold. Trước khi chạy test, chọn cấu hình theo thứ tự Evidence Hit@3 trên dev, rồi Hit@1 và thời gian truy xuất trung vị. **C được chốt lúc 10:53:24 UTC+7**, sau khi hòa B ở Hit@3 và cao hơn B một câu ở Hit@1. [Bộ câu hỏi và cách chấm](../../benchmark/README.md) · [Bản ghi lựa chọn trước test](../../starter/results/dev_selection.json)

## 3. Kết quả đã chốt

| Tập | Cấu hình | Hit@1 | Evidence Hit@3 | Top-3 đúng phạm vi trên câu có filter | Trung vị truy xuất |
|---|---|---:|---:|---:|---:|
| Dev | A | 4/15 | 6/15 | 2/3 | 1,37 ms |
| Dev | B | 8/15 | 11/15 | 1/3 | 1,42 ms |
| Dev | C | 9/15 | 11/15 | 3/3 | 1,32 ms |
| Test | A | 2/5 | 2/5 | 0/2 | 1,27 ms |
| Test | B | 2/5 | 2/5 | 0/2 | 1,34 ms |
| Test | C | 3/5 | 3/5 | 2/2 | 1,30 ms |

| Cấu hình | Số chunk | Ký tự min / trung vị / max | Tổng ký tự | Thời gian ingest | Chi phí API |
|---|---:|---:|---:|---:|---:|
| A | 163 | 66 / 500 / 500 | 73.002 | 2,67 giây | 0 |
| B | 190 | 58 / 378,5 / 500 | 66.652 | 2,94 giây | 0 |
| C | 190 dùng chung B | 58 / 378,5 / 500 | 66.652 dùng chung B | 0 giây bổ sung | 0 |

Thời gian ingest gồm chunking, embedding và upsert sau khi làm nóng model. Đo retrieval từ lúc gửi truy vấn pgvector đến lúc nhận kết quả, sau khi đã có vector câu hỏi. Mỗi câu được đo ba lần cho mỗi cấu hình, luân phiên thứ tự A/B/C. Các chênh lệch truy xuất dưới 0,2 ms có thể nằm trong nhiễu đo.

| Cấu hình | Coherence trung bình /2 (n) | Metadata đủ và đúng | Grounding 0 / 1 / 2 (số câu) | Grounding trung bình /2 |
|---|---:|---:|---:|---:|
| A | 1,00 (10) | 15/15 | 1 / 2 / 2 | 1,20 |
| B | 1,70 (10) | 15/15 | 1 / 2 / 2 | 1,20 |
| C | 1,70 (10) | 15/15 | 0 / 2 / 3 | 1,60 |

Coherence được chấm tại cùng 10 vị trí đã định trên trang PDF 1–10. Metadata được đối chiếu cho 45 kết quả test theo nguồn, trang in, phạm vi và vị trí trong corpus. Grounding đo độ đủ của bằng chứng trong top-3 cho 5 câu test của mỗi cấu hình. Các nhãn coherence và grounding do **Codex (OpenAI)** đọc đoạn và ghi lý do theo thang 0–2. Công cụ cũng hỗ trợ triển khai code và đối chiếu các tổng điểm với trace, log và bảng nhãn. Nhãn định tính trong lần chạy này phản ánh đánh giá của một bên chấm.

[Trace dev](../../starter/results/trace_dev.jsonl) · [Trace test](../../starter/results/trace_test.jsonl) · [Coherence](../../starter/results/audit_coherence.csv) · [Metadata](../../starter/results/audit_metadata.csv) · [Grounding](../../starter/results/audit_grounding.csv) · [Nhật ký thí nghiệm](../../artifacts/experiment_log.csv)

## 4. Phân tích và kết luận

**A → B.** Giữ ranh giới Điều và đoạn tăng Evidence Hit@3 dev từ **6/15 lên 11/15** và coherence từ **1,00 lên 1,70**, đồng thời tạo thêm 27 chunk và mất thêm khoảng 0,27 giây ingest. Hai cấu hình cùng đạt **2/5** trên test. Với `q13` (Điều 39, trang PDF 28), A chia câu trả lời về xóa và trả lại dữ liệu thành hai chunk. B giữ câu đầy đủ trong index nhưng xếp chunk chứa câu đó ngoài top-3.

**B → C.** Filter đưa bằng chứng của `q14` (Mẫu 01, trang PDF 31) từ hạng 2 lên hạng 1 trên dev. Ở `q20` (Mẫu 03, trang PDF 34), B lấy các đoạn Điều khoản ngoài biểu mẫu. C đưa đúng trường **Biện pháp áp dụng** lên hạng 1. Với `q19` (Mẫu 02, trang PDF 32), cả ba kết quả của C đều đúng phạm vi nhưng thiếu trường **Địa chỉ trụ sở**. Metadata giúp giới hạn tập tìm kiếm, còn cosine vẫn có thể xếp đoạn đủ bằng chứng ngoài top-3.

**Lỗi còn lại.** Ở `q18` (Điều 25, Khoản 4, trang PDF 22), A đưa phần đầu câu vào hạng 3 nhưng cắt mất hình thức, nội dung và mốc phải thông báo. B/C lưu đầy đủ Khoản 4 trong index nhưng ưu tiên các đoạn lân cận về lập và cập nhật hồ sơ. Các lỗi này làm giảm Evidence Hit@3 dù kết quả có thể trỏ đúng trang hoặc đúng chủ đề.

**Chọn C cho corpus này** theo quy tắc đã định. Test đạt **3/5 Evidence Hit@3**, vẫn bỏ sót 2 câu. Mỗi câu tương ứng 20 điểm phần trăm trên test và cả corpus chỉ có một văn bản. Lọc phụ thuộc vào việc nhận diện đúng phạm vi, độ mạch lạc phụ thuộc cách đặt ranh giới trang và kết quả thời gian phản ánh một lần chạy trên một máy.

Nếu mở rộng, ưu tiên giữ ngữ cảnh Điều/Mẫu qua biên trang và thử kết hợp BM25 với dense retrieval để tăng khả năng tìm tên trường biểu mẫu. Chọn tham số trên dev và đánh giá lại bằng một tập test mới. [Phân tích và bảng tổng hợp lần chạy](../../starter/results/benchmark_summary.md)
