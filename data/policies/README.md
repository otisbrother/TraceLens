# Kho chính sách / SOP giả định (Synthetic Policies)

Thư mục này chứa danh mục các đoạn chính sách và SOP điều tra giả định (`policies_catalog.json`) phục vụ kiểm thử RAG có kiểm soát quyền và thời gian hiệu lực.

## Metadata bắt buộc

Mỗi mục chính sách lưu trong bảng `policies` có:
- `policy_id`: mã định danh chính sách gốc (`device`, `device-transfer`, `ledger`, `eligibility`, `network-commute`, `review`, `escalation`, `privacy-access`, `adversarial`, `governance`).
- `version`: số phiên bản nguyên (`0`, `1`, ...).
- `effective_from`, `effective_to`: khoảng thời gian hiệu lực chuẩn UTC ISO-8601 (`effective_to = null` nếu đang còn hiệu lực).
- `tenant_id`: phạm vi tenant sở hữu chính sách.
- `allowed_roles`: danh sách vai trò được phép đọc (`investigator`, `reviewer`, `auditor`).
- `section_id`: định danh mục nội dung phục vụ trích dẫn chính xác.
- `content_hash`: SHA-256 của nội dung đoạn chính sách.

## Xử lý thời điểm hiệu lực (Transaction-time Policy vs Investigation SOP)

1. **Chính sách nghiệp vụ tại thời điểm giao dịch (`transaction_time`):**
   Khi đánh giá tính hợp lệ của việc dùng chung thiết bị, điều kiện hưởng khuyến mãi hoặc hạch toán bút toán đảo (`device`, `device-transfer`, `ledger`, `eligibility`, `network-commute`), hệ thống lọc chính sách theo mốc `effective_at = case.cutoff` (thời điểm chốt bản chụp giao dịch của hồ sơ). Chính sách cũ đã hết hạn trước `cutoff` (ví dụ `device` v0 năm 2024) sẽ bị loại **trước khi xếp hạng**.
2. **SOP quy trình điều tra và phê duyệt (`investigation_sop`):**
   Hiện tại các quy định này cũng dùng `case.cutoff`. Việc tách chính sách tại từng giao dịch khỏi SOP hiện hành tại thời điểm điều tra chưa được triển khai.
3. **Thứ tự thực thi:**
   Luôn lọc `tenant_id`, `actor.role in allowed_roles` và `effective_from <= effective_at < effective_to` **trước khi** tính điểm BM25/lexical hoặc kết hợp vector hybrid và trước khi đưa vào context của mô hình.

