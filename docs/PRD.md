# Product Requirements Document (PRD) — TraceLens

## 1. Bối cảnh và người dùng mục tiêu

Trong nền tảng gọi xe, các chương trình khuyến mãi (voucher) thường bị lạm dụng bởi các nhóm phối hợp tạo nhiều tài khoản mới, dùng chung thiết bị và thực hiện các chuyến đi ngắn tập trung với cùng tài xế. Tuy nhiên, các tín hiệu cảnh báo thô (như dùng chung thiết bị, trùng dải mạng IP hoặc nhiều lượt áp mã) cũng xuất hiện ở **người dùng hợp lệ**: hộ gia đình dùng chung máy tính bảng, thiết bị chuyển nhượng/bán lại kế tiếp nhau, nhân viên cùng văn phòng chung Wi-Fi, hoặc các chuyến đã hủy/hoàn trả (reversal).

**Người dùng mục tiêu:**
1. **Điều tra viên (Investigator):** Mở cảnh báo, thu thập dữ liệu chuyến đi, thiết bị, sổ khuyến mãi và SOP, đối chiếu bằng chứng ủng hộ và phản bác để soạn hồ sơ đề xuất.
2. **Người duyệt (Reviewer):** Kiểm chứng nguồn trích dẫn trong hồ sơ, xem xét bằng chứng phản bác và dữ liệu thiếu, đưa ra quyết định xử lý trong sandbox (`CLOSE_NO_ACTION`, `REQUEST_MORE_INFORMATION`, `ESCALATE_FOR_MANUAL_REVIEW`) kèm lý do.
3. **Kiểm toán viên (Auditor):** Tra cứu lịch sử chạy, sự kiện công cụ (tool calls), phiên bản báo cáo và nhật ký phê duyệt.

## 2. Nỗi đau (Pain Points) hiện tại

- Phải mở rời rạc nhiều bảng (chuyến đi, thiết bị, sổ cái voucher, SOP) khiến mất thời gian tổng hợp.
- Dễ mắc thiên kiến xác nhận (confirmation bias): chỉ nhìn thấy cảnh báo "dùng chung thiết bị" hoặc tổng lượt áp mã ban đầu mà bỏ sót việc các khoảng thời gian không hề chồng lấn hoặc các bút toán đã có reversal.
- Các báo cáo tự viết thiếu trích dẫn nguồn chuẩn hóa theo snapshot, gây khó khăn khi người duyệt kiểm tra lại.

## 3. Giả thuyết giá trị (Value Hypothesis)

> **Giả thuyết:** Việc sử dụng workflow điều tra hướng bằng chứng có giới hạn (Mode C) giúp tăng tỷ lệ bao phủ bằng chứng phản bác (counterevidence recall) và độ nhất quán với rubric chính sách so với dashboard cơ sở (Mode A) và checklist cố định (Mode B), trong khi vẫn đảm bảo 100% trích dẫn hợp lệ trong phạm vi snapshot và giữ quyết định cuối cùng ở con người.

*Lưu ý bắt buộc:* Đây là giả thuyết cần kiểm chứng qua thực nghiệm; hệ thống không tự động khóa tài khoản, không chấm điểm chặn giao dịch thời gian thực và không tuyên bố tiết kiệm chi phí hay độ chính xác ngoài thực tế từ dữ liệu mô phỏng.

## 4. Phạm vi MVP và giới hạn ngoài phạm vi

### Trong phạm vi MVP
- **1 loại cảnh báo:** Nghi phối hợp trục lợi voucher trong nền tảng gọi xe.
- **8 nhóm kịch bản dữ liệu tổng hợp (12 ca demo biên soạn sẵn + tập dev 50 ca + tập held-out 150 ca):** Phối hợp trục lợi, hộ gia đình hợp lệ, chuyển giao thiết bị kế tiếp, trùng mạng văn phòng, đi làm định kỳ, bút toán đảo (reversal), thiếu dữ liệu nguồn và văn bản chứa chỉ thị giả mạo (prompt injection).
- **7 công cụ chỉ đọc (read-only tools)** thực thi chung domain service cho cả Direct adapter và Local stdio MCP server.
- **RAG trên SOP có phiên bản và hiệu lực thời gian**, lọc tenant/role/cutoff trước khi xếp hạng.
- **LangGraph state machine** có persistent SQLite checkpointer, giới hạn ngân sách (budget) và tạm dừng chờ người duyệt (`interrupt` / `Command(resume=...)`).
- **Kiểm chứng deterministic & sửa lỗi có giới hạn (bounded repair 1 bước).**
- **Đánh giá A / B / C** độc lập với kho nhãn `evaluation/private_gold/` (không mount vào runtime).

### Ngoài phạm vi MVP (Deferred)
- Huấn luyện mô hình đồ thị lớn (XGBoost/GNN), Neo4j cluster, Leiden clustering, hệ thống đa tác tử (multi-agent), fine-tuning/DPO, tích hợp cổng thanh toán thật hoặc khóa tài khoản thật.

## 5. Tiêu chí nghiệm thu (Acceptance Criteria) và Thước đo

| Nhóm tiêu chí | Yêu cầu nghiệm thu | Thước đo kiểm chứng |
| --- | --- | --- |
| Tính đúng đắn số liệu | Tiền lưu bằng integer VND; ưu đãi ròng tính đúng `redemption + reversal` trước `cutoff` | `FACTS_MISMATCH == 0` trên toàn bộ test |
| Chất lượng bằng chứng | Mọi nhận định đều gắn `evidence_ids` mở được bản ghi nguồn trong cùng `case_id` và `snapshot_id` | `Citation Validity = 100%` |
| Đối chiếu phản bác | Nêu rõ bằng chứng phản bác khi không chồng lấn thiết bị, tài khoản lâu năm hoặc có reversal | `Counterevidence Recall` trên tập có phản bác |
| An toàn khi thiếu nguồn | Nguồn `partial` / `unavailable` / `timeout` phải chuyển về `INSUFFICIENT_EVIDENCE` | Không kết luận gian lận khi thiếu nguồn |
| Kiểm soát quyền & HITL | Investigator không thể tự duyệt; Reviewer duyệt khớp `version` + `content_hash` + `snapshot_id` | Chặn 403/409 trên cross-tenant & stale review |

