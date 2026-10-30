# Cẩm nang vận hành và xử lý sự cố (Runbook) — TraceLens

Tài liệu này hướng dẫn chẩn đoán và xử lý các tình huống lỗi vận hành trong môi trường sandbox của TraceLens.

## 1. Nguồn dữ liệu bị thiếu, trễ hoặc lỗi (`SOURCE_INCOMPLETE` / `SOURCE_UNAVAILABLE` / `SOURCE_TIMEOUT`)

- **Triệu chứng:** Báo cáo hiển thị trạng thái `missing_evidence` hoặc `source_error`, đề xuất tự động chuyển về `INSUFFICIENT_EVIDENCE` (ví dụ ca `TL-1048`, `TL-1049`).
- **Nguyên nhân:** Bản ghi trong bảng `source_quality` của hệ thống nguồn (`trips`, `devices`, `ledger`, `accounts`) có `completeness != "complete"` hoặc `as_of < case.cutoff`.
- **Xử lý:**
  1. Mở tab **Dữ liệu & nguồn** để xem cảnh báo nguồn nào đang bị `partial` / `unavailable`.
  2. Tuyệt đối không coi việc thiếu nguồn là bằng chứng không có gian lận hoặc xác nhận gian lận.
  3. Người duyệt chọn `REQUEST_MORE_INFORMATION` hoặc `ESCALATE_FOR_MANUAL_REVIEW` kèm ghi chú nguồn cần đồng bộ lại.

## 2. Lượt chạy bị kẹt (Job Stuck) hoặc Worker tắt đột ngột

- **Triệu chứng:** Hồ sơ ở trạng thái `investigating`, `run.status == "running"` quá `LEASE_SECONDS` (60 giây).
- **Cơ chế tự hồi phục:**
  - Bảng `jobs` dùng cơ chế thuê bao có thời hạn (`lease_until`, `lease_token`) và tối đa `MAX_ATTEMPTS = 3`.
  - Khi worker khởi động lại hoặc chạy `uv run python -m apps.worker.main --once`, hàm `claim_job` tự động thu hồi các job có `lease_until < now`:
    - Nếu `attempts < 3`: cấp `lease_token` mới và chạy lại an toàn (fencing ngăn worker cũ ghi đè).
    - Nếu `attempts >= 3`: đánh dấu `Job.status = "failed"`, `run.error_code = "RETRY_EXHAUSTED"` và chuyển hồ sơ sang `needs_manual_review`.

## 3. Lỗi cấu hình hoặc lỗi từ Live LLM Provider (`LIVE_PROVIDER_NOT_CONFIGURED` / `PROVIDER_*`)

- **Triệu chứng:** API trả `503` khi bắt đầu chạy hoặc lượt chạy kết thúc với `error_code` thuộc nhóm `PROVIDER_TIMEOUT`, `PROVIDER_RATE_LIMIT`, `PROVIDER_INVALID_JSON`, `LLM_BUDGET_EXCEEDED`.
- **Xử lý:**
  1. Kiểm tra `/api/ready` và file `.env` xem `LLM_MODE`, `LLM_API_KEY`, `LLM_MODEL` (hoặc `EMBEDDING_API_KEY`, `EMBEDDING_MODEL` khi `RETRIEVAL_MODE=hybrid`) đã được khai báo đúng chưa.
  2. Hệ thống mặc định **không tự ý đổi sang mock** khi `LLM_MODE=live` bị lỗi. Nếu muốn chạy thử offline không cần key, đặt tường minh `LLM_MODE=mock` và `RETRIEVAL_MODE=lexical`.

## 4. Từ chối phê duyệt bản cũ (Stale Approval / `409 Conflict`)

- **Triệu chứng:** Người duyệt nhấn **Ghi quyết định sandbox** nhưng nhận lỗi `409 Phiên bản cũ hoặc hồ sơ không còn chờ duyệt` hoặc `Báo cáo hoặc snapshot không khớp`.
- **Nguyên nhân:** Điều tra viên đã tạo phiên bản báo cáo mới (`report_version` tăng lên) hoặc hồ sơ đã được duyệt bởi một request trước đó.
- **Xử lý:** Tải lại hồ sơ trên giao diện để lấy `report.version` và `report.content_hash` mới nhất, kiểm tra lại nội dung phiên bản mới trước khi gửi quyết định.

## 5. Khởi động lại API / Worker khi đang chờ người duyệt (`awaiting_review`)

- **Cơ chế:** Trạng thái hồ sơ, báo cáo, bằng chứng nằm trong DB chính (`tracelens.db` / PostgreSQL), còn trạng thái `interrupt()` của LangGraph nằm trong file checkpointer (`tracelens.db.checkpoints.sqlite`).
- **Xử lý:** Chỉ cần khởi động lại tiến trình `uvicorn` và `worker`; không cần chạy lại lượt điều tra. Khi người duyệt gửi quyết định qua `/api/cases/{case_id}/review`, hệ thống tự động nạp lại checkpoint theo `thread_id = run.id` và hoàn tất node `finalize_disposition`.

## 6. Trích dẫn hoặc số liệu không hợp lệ (`VALIDATION_FAILED`)

- **Triệu chứng:** Lượt chạy dừng với `error_code = "VALIDATION_FAILED"` và hồ sơ chuyển sang `needs_manual_review`.
- **Nguyên nhân:** Sau 1 bước sửa lỗi tự động (`repair_report`), báo cáo vẫn chứa `evidence_id` hoặc `source_ref` ngoài phạm vi `(tenant_id, case_id, snapshot_id)` hoặc số liệu `facts` lệch với số liệu tính bằng code.
- **Xử lý:** Kiểm tra sự kiện trong tab **Audit & lượt chạy** để xem mã lỗi kiểm chứng cụ thể.

## 7. Quyết định đã lưu nhưng checkpoint chưa tiếp tục

`review_pending_resume` / `CHECKPOINT_RESUME_PENDING` nghĩa là quyết định người duyệt đã lưu bền vững,
nhưng LangGraph chưa hoàn tất. Kiểm tra quyền truy cập và đường dẫn checkpoint dùng chung cho API/worker,
sau đó chạy lại worker. Worker tự thử lại quyết định đã lưu; không tạo quyết định mới hoặc xóa checkpoint.
Audit `review.resume_pending` ghi nhận lỗi; lượt chạy chỉ chuyển `completed` khi resume thành công.

