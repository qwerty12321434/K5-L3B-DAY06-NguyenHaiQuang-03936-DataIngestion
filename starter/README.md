# Starter · PostgreSQL + pgvector

Bộ khởi động **tùy chọn** cho lab cá nhân. Docker Compose chạy PostgreSQL + pgvector local. `src/ingestion.py` chuẩn bị corpus Nghị định 13/2023. `src.run` chia chunk, tạo vector bằng Ollama và kiểm tra truy xuất A/B/C. Bộ câu hỏi đã chốt ở [benchmark](../benchmark/README.md).

## Cấu trúc

```text
starter/
├── Dockerfile             # Python 3.13 và thư viện chạy pipeline
├── docker-compose.yml     # PostgreSQL 16 + pgvector, chỉ mở cổng local
├── init.sql               # bật extension khi tạo database mới
├── requirements.txt       # psycopg, pypdf và python-dotenv
├── .env.example           # cấu hình mẫu cho chạy Python trực tiếp
├── scripts/check_setup.py # kiểm tra kết nối và extension (đã chạy được)
├── src/
│   ├── models.py          # cấu trúc dữ liệu mẫu
│   ├── ingestion.py       # đọc / chuẩn hóa corpus Nghị định 13/2023
│   ├── chunking.py        # cửa sổ cố định và chia theo Điều/đoạn
│   ├── embeddings.py      # Qwen3-Embedding-0.6B qua Ollama
│   ├── store.py           # lưu vector, metadata và lọc trong pgvector
│   ├── evaluation.py      # chấm evidence, Hit@1 và Evidence Hit@3
│   ├── benchmark.py       # chạy dev/test và lưu trace
│   └── run.py             # ingest và kiểm tra truy xuất kỹ thuật
├── data/                  # bạn tự chuẩn bị corpus được phép sử dụng
└── results/               # lưu kết quả của bạn
```

## 1. Cài và kiểm tra database

Lần chạy báo cáo dùng Docker Desktop và Ollama trên Windows. Image Python chứa Python 3.13 và các thư viện của bài. Cần mạng ở lần tải image, thư viện và model. Chạy từ **gốc repo** trong PowerShell:

```powershell
docker compose -f starter/docker-compose.yml -p day6_student_starter up -d --wait
docker build -t day6-ingestion:local ./starter
$dbUrl = 'postgresql://day6:day6_local_only@postgres:5432/day6_lab'
docker run --rm --network day6_student_starter_default `
  --mount "type=bind,source=$($PWD.Path),target=/lab,readonly" `
  -e "DATABASE_URL=$dbUrl" `
  day6-ingestion:local python scripts/check_setup.py
```

Khi hiện `Setup OK`, Python trong container đã kết nối được PostgreSQL và xác nhận extension pgvector. Compose công bố cổng local `127.0.0.1:55433`. Container cùng mạng truy cập qua `postgres:5432`. Nếu cổng local đã bận, đổi cổng phía Windows trong Compose. `init.sql` bật extension khi volume được tạo lần đầu.

```powershell
# Dừng container và giữ volume dữ liệu.
docker compose -f starter/docker-compose.yml -p day6_student_starter stop
```

## 2. Chọn embedding model

Pipeline dùng [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) qua [Ollama](https://ollama.com/library/qwen3-embedding:0.6b) trên Windows. Mở Ollama rồi chạy `ollama pull qwen3-embedding:0.6b` một lần. Bản Ollama dùng Q8_0, khoảng 639 MB, vector 1.024 chiều. Tài liệu được encode nguyên văn. Câu hỏi có thêm chỉ dẫn truy xuất tiếng Anh ở dạng `Instruct: ...\nQuery: ...`. Chi phí API là 0. Model digest và phiên bản Ollama được ghi theo lần chạy.

Trong số các model cùng cỡ, BGE-M3 có kết quả tốt hơn Qwen 0.6B trên một số tập luật tiếng Việt ở [nghiên cứu EACL 2026](https://aclanthology.org/2026.findings-eacl.110/), còn bảng [MTEB multilingual của Qwen](https://github.com/QwenLM/Qwen3-Embedding/blob/main/README.md#mteb-multilingual) cho thấy Qwen dẫn trên điểm retrieval tổng hợp. [SEA-LION-E5-Embedding-600M](https://huggingface.co/aisingapore/SEA-LION-E5-Embedding-600M) là lựa chọn cùng cỡ được tối ưu cho tiếng Việt và các ngôn ngữ Đông Nam Á. A/B/C dùng cùng Qwen để so sánh chiến lược dữ liệu.

## 3. Hoàn thiện pipeline

### Chuẩn bị corpus Nghị định 13/2023

Đặt bản PDF từ [Công báo](https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-13-2023-nd-cp-39228.htm) vào `starter/data/2023_685 + 686_13-2023-NĐ-CP.pdf`. Từ **gốc repo** trong PowerShell, đối chiếu SHA-256 với manifest rồi tạo lại corpus:

```powershell
$manifest = Get-Content -Raw -Encoding utf8 .\data\manifest.json | ConvertFrom-Json
$sourceHash = (Get-FileHash -LiteralPath $manifest.local_path -Algorithm SHA256).Hash
if ($sourceHash -ne $manifest.sha256) { throw 'PDF does not match the manifest' }
docker run --rm --mount "type=bind,source=$($PWD.Path),target=/lab" `
  day6-ingestion:local python -m src.ingestion --accessed-at 2026-11-14
```

Corpus đã clean có sẵn để chạy lại từ bước ingest. Ngày trên là ngày đối chiếu nguồn của bản corpus nộp. Lệnh tạo [manifest](../data/manifest.json) của cả 37 trang và [JSONL đã làm sạch](results/corpus_pages.jsonl) của 36 trang dùng cho retrieval. Trang PDF 30 là danh mục mẫu, được ghi trong manifest và loại khỏi JSONL.

`page` là số trang PDF từ 1, còn `metadata.printed_page` là số trang Công báo. `metadata.section` phân biệt thân Nghị định và phụ lục. `metadata.spans` chỉ vị trí trong `text` bằng chỉ số ký tự `start` (bao gồm) và `end` (không bao gồm), kèm Chương/Mục/Điều/Khoản/Điểm hoặc số Mẫu tùy đoạn. Ngữ cảnh được giữ qua ngắt trang. Ví dụ, trang PDF 33 và 34 cùng thuộc Mẫu 03. Bố cục nhiều cột được tuyến tính hóa, vì vậy kiểm tra lại bản PDF khi dùng nội dung biểu mẫu làm bằng chứng.

### Chạy ingest A/B/C bằng Docker

Từ **thư mục gốc repo** trong PowerShell, với Ollama đã mở:

```powershell
$dbUrl = 'postgresql://day6:day6_local_only@postgres:5432/day6_lab'
docker run --rm --network day6_student_starter_default `
  --mount "type=bind,source=$($PWD.Path),target=/lab" `
  -e "DATABASE_URL=$dbUrl" `
  -e "OLLAMA_BASE_URL=http://host.docker.internal:11434" `
  day6-ingestion:local python -m src.run --verify-reingest
```

Image Python giữ dependencies giữa các lần chạy. PostgreSQL giữ vector trong volume Compose. Khi dùng container, host của database là `postgres:5432`, Ollama trên máy Windows đi qua `host.docker.internal:11434`.

| Chiến lược | Chia chunk | Truy xuất |
|---|---|---|
| A | Cửa sổ 500 ký tự, bước trượt 450, cắt trong từng trang | Top-3 theo cosine |
| B | Trong từng trang, giữ ranh giới Điều, ưu tiên ranh giới đoạn hoặc dòng biểu mẫu, tối đa 500 ký tự. Đoạn quá dài tách ở khoảng trắng khi có thể | Top-3 theo cosine |
| C | Dùng cùng chunk và vector của B | Thêm filter `section` và/hoặc `form_number` khi được cung cấp |

Tất cả chunk giữ trang PDF, trang Công báo và khoảng ký tự trong bản clean. pgvector dùng tìm kiếm cosine chính xác, xếp hạng trên A hoặc B và lọc metadata cho C. Mỗi lần ingest, upsert giữ ID ổn định và dọn chunk cũ của tài liệu đã nạp lại trong cùng giao dịch. Tùy chọn `--verify-reingest` ghi lại số hàng trước và sau lần upsert thứ hai.

Các tệp đầu ra ở `starter/results/` gồm `chunks.jsonl` để kiểm tra từng chunk, `ingestion_report.json` gồm cấu hình, số liệu, chi phí, kiểm tra nạp lại và top-3 kỹ thuật, cùng `ingestion.log` ghi tiến trình và lỗi. Thời gian ingest gồm chia chunk, embedding và upsert, với thời gian làm nóng model ghi riêng. Truy vấn kiểm tra khác bộ dev/test.

Trong `chunks.jsonl`, dòng có `strategy: "C"` dùng cùng `chunk_id`, văn bản và metadata với dòng B tương ứng. Trường `storage_strategy: "B"` cho biết C đọc vector từ B trong pgvector. Mọi chunk đều có `metadata.section`; `metadata.form_number` có ở các trang biểu mẫu, ví dụ trang PDF 32 có `section: "phu_luc"` và `form_number: "02"`. C áp dụng các điều kiện này khi truy vấn, nên file xuất liệt kê toàn bộ chunk mà C có thể tìm.

### Chạy benchmark

Từ gốc repo trong PowerShell, dùng lại PostgreSQL, Ollama và image Python đã tạo ở bước ingest. Các lệnh tái lập ghi lại artefact trong `starter/results/`. Có thể chạy trên bản sao repo để đối chiếu với kết quả nộp.

```powershell
$dbUrl = 'postgresql://day6:day6_local_only@postgres:5432/day6_lab'
$labArgs = @(
  'run', '--rm', '--network', 'day6_student_starter_default',
  '--mount', "type=bind,source=$($PWD.Path),target=/lab",
  '-e', "DATABASE_URL=$dbUrl",
  '-e', 'OLLAMA_BASE_URL=http://host.docker.internal:11434',
  'day6-ingestion:local', 'python', '-m', 'src.benchmark'
)
docker @labArgs dev
# Đọc retrieval_dev.jsonl và ghi evidence_review_dev.jsonl trước khi chấm.
docker @labArgs dev --score
docker @labArgs test
# Đọc retrieval_test.jsonl và ghi evidence_review_test.jsonl trước khi chấm.
docker @labArgs test --score
```

File `evidence_review_<split>.jsonl` được để trống nếu kiểm tra thấy kết quả so khớp gold đã đủ. Mỗi đoạn tương đương được duyệt thêm cần một dòng gồm `question_id`, `chunk_id` và `reason`. Lệnh test lưu lựa chọn từ dev trước khi truy xuất và kiểm tra cùng phiên bản corpus, benchmark, chunk và model. Xem [bảng kết quả](results/benchmark_summary.md) để mở trace và nhãn chi tiết của lần chạy.

Lưu code, cấu hình, kết quả và lệnh chạy để người chấm tái lập; không commit key, dữ liệu mật hoặc dữ liệu không có quyền chia sẻ.
