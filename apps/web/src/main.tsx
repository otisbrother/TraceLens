import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, download, setCSRF } from './api'
import type { Actor, Audit, CaseItem, Claim, Detail, Report, Row, Run } from './types'
import './styles.css'

const money = (n: number) => Number.isFinite(n) ? new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(n) : 'Chưa có nguồn'
const time = (s: unknown) => new Date(String(s)).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })

const labels: Record<string, string> = {
  open: 'Chưa điều tra',
  investigating: 'Đang điều tra',
  awaiting_review: 'Chờ người duyệt',
  reviewed: 'Đã duyệt',
  needs_information: 'Cần bổ sung',
  needs_manual_review: 'Cần kiểm tra thủ công',
  queued: 'Đang xếp hàng',
  running: 'Đang thu thập',
  completed: 'Hoàn tất',
  review_pending_resume: 'Đã ghi quyết định · chờ tiếp tục workflow',
  failed: 'Có lỗi',
  ESCALATE_FOR_REVIEW: 'Đề xuất chuyển kiểm tra thêm',
  NO_ACTION_SUGGESTED: 'Đề xuất không hành động',
  INSUFFICIENT_EVIDENCE: 'Chưa đủ bằng chứng',
  CLOSE_NO_ACTION: 'Đóng — không hành động',
  REQUEST_MORE_INFORMATION: 'Yêu cầu bổ sung thông tin',
  ESCALATE_FOR_MANUAL_REVIEW: 'Chuyển điều tra thủ công',
  ready_for_review: 'Sẵn sàng chờ duyệt',
  missing_evidence: 'Thiếu bằng chứng',
  source_error: 'Lỗi nguồn dữ liệu',
  validation_failed: 'Lỗi kiểm chứng',
  high: 'Cao',
  medium: 'Trung bình',
  low: 'Thấp',
}
const label = (s: string) => labels[s] || s

const DEMO_ACCOUNTS = [
  {
    username: 'investigator',
    title: 'Điều tra viên',
    badge: 'tenant-a · investigator',
    desc: 'Chạy luồng Mode B/C, đối chiếu nguồn & xuất báo cáo',
  },
  {
    username: 'reviewer',
    title: 'Người phê duyệt',
    badge: 'tenant-a · reviewer',
    desc: 'Thẩm định bằng chứng & ra quyết định xử lý cuối cùng',
  },
  {
    username: 'auditor',
    title: 'Kiểm toán nội bộ',
    badge: 'tenant-a · auditor',
    desc: 'Tra cứu nhật ký công cụ, chính sách & lịch sử chạy',
  },
  {
    username: 'other-investigator',
    title: 'Điều tra viên Tenant B',
    badge: 'tenant-b · investigator',
    desc: 'Kiểm chứng cô lập dữ liệu đa thuê bao (TL-1043)',
  },
]

function statusTone(s?: string): string {
  if (!s) return 'neutral'
  if (s === 'ESCALATE_FOR_REVIEW' || s === 'needs_manual_review' || s === 'failed' || s === 'high') return 'danger'
  if (s === 'NO_ACTION_SUGGESTED' || s === 'reviewed' || s === 'completed' || s === 'CLOSE_NO_ACTION') return 'success'
  if (s === 'INSUFFICIENT_EVIDENCE' || s === 'awaiting_review' || s === 'needs_information' || s === 'medium')
    return 'warning'
  return 'info'
}

function Banner({
  providerMode = 'mock',
  retrievalMode = 'lexical',
}: {
  providerMode?: string
  retrievalMode?: string
}) {
  return (
    <div className="banner">
      <div className="banner-inner">
        <span className="banner-pill">
          <span className="dot" /> Dữ liệu tổng hợp (Synthetic)
        </span>
        <span className="divider">•</span>
        <span>
          {providerMode === 'mock' ? 'Mock workflow — chưa đo năng lực LLM thật' : `Provider: ${providerMode}`}
        </span>
        <span className="divider">•</span>
        <span>
          Retrieval: <strong>{retrievalMode}</strong>
        </span>
      </div>
    </div>
  )
}

function Login({ onLogin }: { onLogin: (actor: Actor) => void }) {
  const [username, setUsername] = useState('investigator')
  const [password, setPassword] = useState('tracelens-local')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  return (
    <div className="login-layout">
      <div className="login-story">
        <div className="brand">
          <b>◈</b> TraceLens
        </div>
        <div className="login-hero-copy">
          <p className="eyebrow">FRAUD INVESTIGATION COPILOT</p>
          <h1>
            Theo dấu bằng chứng.
            <br />
            Kiểm tra giả thuyết.
          </h1>
          <p>
            Không gian điều tra có giới hạn ngân sách công cụ, chủ động truy tìm cả bằng chứng ủng hộ lẫn bằng chứng
            phản bác trước khi trình người có thẩm quyền phê duyệt.
          </p>
          <div className="flow">
            <span className="flow-step">1. Cảnh báo</span>
            <span className="flow-arrow">→</span>
            <span className="flow-step">2. Thu thập & Phản chứng</span>
            <span className="flow-arrow">→</span>
            <span className="flow-step">3. Kiểm chứng Trích dẫn</span>
            <span className="flow-arrow">→</span>
            <span className="flow-step">4. Người duyệt</span>
          </div>
          <div className="hero-highlights">
            <div>
              <strong>Hồ sơ có nguồn</strong>
              <span>Workflow có bước người duyệt</span>
            </div>
            <div>
              <strong>7 công cụ</strong>
              <span>Read-only · Ký HMAC scope</span>
            </div>
            <div>
              <strong>Kiểm chứng</strong>
              <span>Trích dẫn theo snapshot</span>
            </div>
          </div>
        </div>
        <small>Sandbox độc lập · Không tự động khóa tài khoản thật · Hỗ trợ Desktop & Mobile</small>
      </div>
      <main className="login-panel">
        <Banner />
        <div className="login-mobile-hero">
          <div className="brand">
            <b>◈</b> TraceLens
          </div>
          <p>Trợ lý điều tra gian lận dựa trên bằng chứng & tìm kiếm phản chứng.</p>
        </div>
        <form
          onSubmit={async e => {
            e.preventDefault()
            setBusy(true)
            setError('')
            try {
              const data = await api<{ actor: Actor; csrf_token: string }>('/session/login', {
                username,
                password,
              })
              setCSRF(data.csrf_token)
              onLogin(data.actor)
            } catch (err) {
              setError(String(err))
            } finally {
              setBusy(false)
            }
          }}
        >
          <p className="eyebrow">KHÔNG GIAN ĐIỀU TRA</p>
          <h2>Đăng nhập demo</h2>
          <p>Chọn nhanh vai trò bên dưới để trải nghiệm phân quyền và cô lập dữ liệu trên server.</p>

          <div className="role-cards" role="radiogroup" aria-label="Chọn nhanh vai trò demo">
            {DEMO_ACCOUNTS.map(acc => (
              <button
                type="button"
                key={acc.username}
                className={`role-card ${username === acc.username ? 'active' : ''}`}
                onClick={() => setUsername(acc.username)}
              >
                <div className="role-card-head">
                  <b>{acc.title}</b>
                  <code>{acc.badge}</code>
                </div>
                <span>{acc.desc}</span>
              </button>
            ))}
          </div>

          <label>
            Tài khoản
            <select value={username} onChange={e => setUsername(e.target.value)}>
              <option value="investigator">investigator · Điều tra viên</option>
              <option value="reviewer">reviewer · Người duyệt</option>
              <option value="auditor">auditor · Kiểm toán</option>
              <option value="other-investigator">other-investigator · Tenant B</option>
            </select>
          </label>
          <label>
            Mật khẩu
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
            />
          </label>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button className="primary login-submit" disabled={busy}>
            {busy ? 'Đang đăng nhập…' : 'Vào không gian điều tra →'}
          </button>
          <p className="hint">
            Tài khoản local được seed sẵn. Mật khẩu mặc định: <code>tracelens-local</code>.
          </p>
        </form>
      </main>
    </div>
  )
}

function Drawer({
  value,
  onClose,
  onSource,
}: {
  value: { title: string; data: Row }
  onClose: () => void
  onSource: (ref: string) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    dialog.current?.showModal()
  }, [])
  return (
    <dialog
      ref={dialog}
      className="drawer"
      onCancel={e => {
        e.preventDefault()
        onClose()
      }}
      onClick={e => {
        if (e.target === dialog.current) onClose()
      }}
    >
      <div className="drawer-handle" aria-hidden="true" />
      <div className="section-head drawer-head">
        <div>
          <p className="eyebrow">ĐỐI CHIẾU NGUỒN GỐC (EVIDENCE DRAWER)</p>
          <h2>{value.title}</h2>
        </div>
        <button className="icon-close" onClick={onClose} aria-label="Đóng nguồn">
          ✕
        </button>
      </div>
      <p className="hint">Nguồn nội bộ được kiểm tra lại quyền theo tenant, hồ sơ và snapshot cutoff.</p>
      {Array.isArray(value.data.source_refs) && value.data.source_refs.length > 0 && (
        <div className="source-links">
          <small className="eyebrow">LIÊN KẾT BẢN GHI GỐC</small>
          <div className="source-chip-list">
            {value.data.source_refs.map((ref: string) => (
              <button key={ref} className="source" onClick={() => onSource(ref)}>
                🔗 {ref} ↗
              </button>
            ))}
          </div>
        </div>
      )}
      <dl className="drawer-dl">
        {Object.entries(value.data)
          .filter(([key]) => key !== 'source_refs')
          .map(([key, item]) => (
            <div className="drawer-row" key={key}>
              <dt>{key}</dt>
              <dd>{typeof item === 'object' ? <pre>{JSON.stringify(item, null, 2)}</pre> : String(item ?? '—')}</dd>
            </div>
          ))}
      </dl>
    </dialog>
  )
}

function Relationship({ detail, onSource }: { detail: Detail; onSource: (ref: string) => void }) {
  const riders = detail.data.records.rider || []
  const devices = detail.data.records.device || []
  const drivers = detail.data.records.driver || []
  const sessions = detail.data.records.session || []
  if (!devices.length) return <p className="hint">Không có nguồn thiết bị (nguồn dữ liệu bị khuyết hoặc không khả dụng).</p>
  return (
    <div className="relationship">
      <div className="relationship-tier">
        <span className="tier-label">Thiết bị trung tâm</span>
        <div className="relationship-device">
          {devices.map(dev => (
            <button key={String(dev.id)} className="node-chip node-device" onClick={() => onSource(`device:${dev.id}`)}>
              ▣ {String(dev.label || 'Thiết bị dùng chung')}
            </button>
          ))}
        </div>
      </div>
      <div className="relationship-tier">
        <span className="tier-label">Tài khoản hành khách liên kết ({riders.length})</span>
        <div className="relationship-riders">
          {riders.map(row => (
            <button key={String(row.id)} className="node-chip node-rider" onClick={() => onSource(`rider:${row.id}`)}>
              ○ {String(row.display_name)}
            </button>
          ))}
        </div>
      </div>
      <div className="relationship-tier">
        <span className="tier-label">Tài xế & Dải mạng IP</span>
        <div className="relationship-riders">
          {drivers.slice(0, 3).map(drv => (
            <button key={String(drv.id)} className="node-chip node-driver" onClick={() => onSource(`driver:${drv.id}`)}>
              ◇ {String(drv.display_name)}
            </button>
          ))}
          {sessions.slice(0, 1).map(s => (
            <button key={String(s.id)} className="node-chip node-ip" onClick={() => onSource(`session:${s.id}`)}>
              ◈ IP {String(s.ip_prefix)}
            </button>
          ))}
        </div>
      </div>
      <p className="hint">
        Bấm vào từng nút thực thể để mở bản ghi gốc trong Evidence Drawer. Liên kết chỉ là tín hiệu cần kiểm chứng.
      </p>
    </div>
  )
}

function Facts({ detail, onSource }: { detail: Detail; onSource: (ref: string) => void }) {
  const { facts, records } = detail.data
  const qualityRows = records.quality || []
  return (
    <>
      <div className="metrics">
        <div className="metric-card accent-teal">
          <span>Ưu đãi ròng tại cutoff</span>
          <strong>{money(facts.net_vnd)}</strong>
          <small>Chưa phải thiệt hại đã xác nhận</small>
        </div>
        <div className="metric-card accent-blue">
          <span>Chuyến trong phạm vi</span>
          <strong>{facts.trip_count}</strong>
          <small>
            {facts.driver_count} tài xế · cửa sổ {facts.trip_span_minutes} phút
          </small>
        </div>
        <div className={`metric-card ${facts.overlap_pairs > 0 ? 'accent-amber' : 'accent-emerald'}`}>
          <span>Cặp sử dụng chồng lấn</span>
          <strong>{facts.overlap_pairs}</strong>
          <small>{facts.new_accounts} tài khoản mới (≤ 7 ngày)</small>
        </div>
      </div>
      <div className="two-col">
        <section className="card">
          <div className="section-head">
            <h3>Liên kết cần kiểm tra (Subgraph)</h3>
            <span className="badge tone-info">Đồ thị thực thể</span>
          </div>
          <Relationship detail={detail} onSource={onSource} />
        </section>
        <section className="card">
          <div className="section-head">
            <h3>Đối chiếu sổ voucher</h3>
            <span className="badge tone-neutral">Ledger</span>
          </div>
          <div className="ledger-totals">
            <p>
              <span>Tổng áp mã (Redemption)</span>
              <b>{money(facts.redemption_vnd)}</b>
            </p>
            <p>
              <span>Bút toán đảo / Hoàn trả (Reversal)</span>
              <b>{money(facts.reversal_vnd)}</b>
            </p>
            <p className="net">
              <span>Giá trị ưu đãi ròng (Net)</span>
              <b>{money(facts.net_vnd)}</b>
            </p>
          </div>
          <p className="hint">Tính bằng số nguyên VND; chỉ bao gồm các bút toán phát sinh trước mốc cutoff.</p>
          {qualityRows.some(q => q.completeness !== 'complete') && (
            <p className="notice">
              ⚠ Cảnh báo chất lượng nguồn:{' '}
              {qualityRows
                .filter(q => q.completeness !== 'complete')
                .map(q => `${q.source_system} (${q.completeness})`)
                .join(', ')}
            </p>
          )}
        </section>
      </div>
      <section className="card">
        <div className="section-head">
          <h3>Timeline chuyến đi ({records.trip.length})</h3>
          <small>Asia/Ho_Chi_Minh (UTC+7)</small>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Bắt đầu</th>
                <th>Người đi</th>
                <th>Tuyến mô phỏng</th>
                <th>Cước</th>
                <th>Nguồn</th>
              </tr>
            </thead>
            <tbody>
              {[...records.trip]
                .sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)))
                .map(row => (
                  <tr key={String(row.id)}>
                    <td>{time(row.started_at)}</td>
                    <td>
                      <b>{String(records.rider.find(r => r.id === row.rider_id)?.display_name || row.rider_id)}</b>
                    </td>
                    <td>{String(row.route)}</td>
                    <td>{money(Number(row.fare_vnd))}</td>
                    <td>
                      <button className="text-button" onClick={() => onSource(`trip:${row.id}`)}>
                        Mở nguồn ↗
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </section>
      <div className="two-col">
        <section className="card">
          <div className="section-head">
            <h3>Khoảng sử dụng thiết bị</h3>
            <small>{records.usage.length} phiên</small>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Người đi</th>
                  <th>Từ (UTC+7)</th>
                  <th>Đến (UTC+7)</th>
                  <th>Nguồn</th>
                </tr>
              </thead>
              <tbody>
                {records.usage.map(row => (
                  <tr key={String(row.id)}>
                    <td>
                      <b>{String(records.rider.find(r => r.id === row.rider_id)?.display_name)}</b>
                    </td>
                    <td>{time(row.start_at)}</td>
                    <td>{time(row.end_at)}</td>
                    <td>
                      <button className="text-button" onClick={() => onSource(`usage:${row.id}`)}>
                        Mở khoảng sử dụng ↗
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="card">
          <div className="section-head">
            <h3>Bút toán voucher</h3>
            <small>{records.ledger.length} giao dịch</small>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Thời điểm (UTC+7)</th>
                  <th>Loại</th>
                  <th>Số tiền</th>
                  <th>Nguồn</th>
                </tr>
              </thead>
              <tbody>
                {[...records.ledger]
                  .sort((a, b) => String(a.occurred_at).localeCompare(String(b.occurred_at)))
                  .map(row => (
                    <tr key={String(row.id)}>
                      <td>{time(row.occurred_at)}</td>
                      <td>
                        <span className={`badge ${row.kind === 'reversal' ? 'tone-success' : 'tone-warning'}`}>
                          {String(row.kind)}
                        </span>
                      </td>
                      <td>{money(Number(row.amount_vnd))}</td>
                      <td>
                        <button className="text-button" onClick={() => onSource(`ledger:${row.id}`)}>
                          Mở bút toán ↗
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      <section className="card">
        <div className="section-head">
          <h3>SOP giả định · lexical retrieval</h3>
          <span className="badge tone-info">{detail.data.policies.length} điều khoản</span>
        </div>
        <p className="hint">Áp dụng tại cutoff của hồ sơ; lọc quyền (ACL) và hiệu lực thời gian trước khi xếp hạng.</p>
        <div className="policy-grid">
          {detail.data.policies.map(p => (
            <button key={String(p.id)} className="policy" onClick={() => onSource(`policy:${p.id}`)}>
              <div className="policy-top">
                <b>
                  {String(p.section_id)} · v{String(p.version)}
                </b>
                <small>Mở đoạn chính sách ↗</small>
              </div>
              <span>{String(p.content)}</span>
            </button>
          ))}
        </div>
      </section>
    </>
  )
}

function Claims({
  title,
  claims,
  onEvidence,
  tone = '',
}: {
  title: string
  claims: Claim[]
  onEvidence: (id: string) => void
  tone?: string
}) {
  return (
    <section className={`card claims ${tone}`}>
      <div className="section-head">
        <h3>{title}</h3>
        <span className="count">{claims.length}</span>
      </div>
      {!claims.length && <p className="hint">Chưa có nhận định được ghi ở nhóm này.</p>}
      {claims.map(claim => (
        <div className="claim" key={claim.claim_id}>
          <span className={`claim-tag ${claim.kind === 'OBSERVED' ? 'observed' : 'inferred'}`}>
            {claim.kind === 'OBSERVED' ? 'QUAN SÁT TRỰC TIẾP' : 'SUY LUẬN — CẦN NGƯỜI DUYỆT'}
          </span>
          <p>{claim.text}</p>
          <div className="citation-list">
            {claim.evidence_ids.map((id, i) => (
              <button className="citation" key={id} onClick={() => onEvidence(id)}>
                📎 Bằng chứng {i + 1} ↗
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

function Workspace({ actor, logout }: { actor: Actor; logout: () => void }) {
  const [cases, setCases] = useState<CaseItem[]>([])
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<Detail | null>(null)
  const [reports, setReports] = useState<Report[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [events, setEvents] = useState<Audit[]>([])
  const [tab, setTab] = useState('sources')
  const [mode, setMode] = useState('mock')
  const [wfMode, setWfMode] = useState<'B' | 'C'>('C')
  const [version, setVersion] = useState(0)
  const [filter, setFilter] = useState('')
  const [status, setStatus] = useState('')
  const [priorityFilter, setPriorityFilter] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [drawer, setDrawer] = useState<{ title: string; data: Row } | null>(null)
  const [rationale, setRationale] = useState('')
  const [disposition, setDisposition] = useState('')
  const [baseline, setBaseline] = useState<Row | null>(null)
  const [notes, setNotes] = useState('')
  const [evaluation, setEvaluation] = useState<Row | null>(null)
  const [mobileQueueOpen, setMobileQueueOpen] = useState(false)

  const currentCase = detail?.data.case
  const report = reports.find(r => r.version === version) || reports[0]

  const action = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const refreshList = useCallback(async () => {
    const data = await api<CaseItem[]>('/cases')
    setCases(data)
    setSelected(old => old || data[0]?.id || '')
  }, [])

  const refreshCase = useCallback(async (id: string) => {
    const result = await Promise.all([
      api<Detail>(`/cases/${id}`),
      api<Report[]>(`/cases/${id}/reports`),
      api<Run[]>(`/cases/${id}/runs`),
      api<Audit[]>(`/cases/${id}/audit`),
    ])
    return result
  }, [])

  useEffect(() => {
    refreshList().catch(e => setError(String(e)))
  }, [refreshList])

  useEffect(() => {
    if (!selected) return
    let active = true
    setLoaded(false)
    setDetail(null)
    setReports([])
    setVersion(0)
    setRationale('')
    setDisposition('')
    setDrawer(null)
    setNotes('')
    const load = async () => {
      try {
        const [d, r, run, a] = await refreshCase(selected)
        if (active) {
          setDetail(d)
          setReports(r)
          setRuns(run)
          setEvents(a)
          setLoaded(true)
        }
      } catch (e) {
        if (active) setError(String(e))
      }
    }
    void load()
    const timer = setInterval(load, 2000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [selected, refreshCase])

  useEffect(() => {
    if (mode === 'A' && selected) {
      let active = true
      setBaseline(null)
      api<Row>(`/cases/${selected}/baseline`)
        .then(v => {
          if (active) setBaseline(v)
        })
        .catch(e => {
          if (active) setError(String(e))
        })
      return () => {
        active = false
      }
    }
  }, [mode, selected])

  const onSource = (ref: string) => {
    void action(async () =>
      setDrawer({
        title: 'Bản ghi nguồn',
        data: await api<Row>(`/cases/${selected}/sources/${encodeURIComponent(ref)}`),
      })
    )
  }

  const onEvidence = (id: string) => {
    void action(async () =>
      setDrawer({
        title: 'Bằng chứng & phép tính',
        data: await api<Row>(`/cases/${selected}/evidence/${id}`),
      })
    )
  }

  const start = () =>
    action(async () => {
      await api(`/cases/${selected}/runs`, {
        idempotency_key: crypto.randomUUID(),
        workflow_mode: wfMode,
        transport: 'direct',
      })
      const [d, r, run, a] = await refreshCase(selected)
      setDetail(d)
      setReports(r)
      setRuns(run)
      setEvents(a)
      setVersion(0)
      setTab('report')
      await refreshList()
    })

  const filteredCases = cases.filter(c => {
    const matchQ = c.id.toLowerCase().includes(filter.toLowerCase()) || c.title.toLowerCase().includes(filter.toLowerCase())
    const effectiveStatus = c.id === selected && currentCase ? currentCase.status : c.status
    const matchStatus = !status || effectiveStatus === status
    const matchPriority = !priorityFilter || c.alert?.priority_band === priorityFilter
    return matchQ && matchStatus && matchPriority
  })

  const mockBench = evaluation?.mock_benchmark as
    | {
        experiment_id?: string
        disclaimer?: string
        dataset?: { split?: string; case_count?: number; seed?: number; dataset_sha256?: string }
        modes?: Array<{
          mode: string
          sample_size: number
          evidence_recall: { value: number; numerator: number; denominator: number }
          counterevidence_recall: {
            value: number | null
            numerator: number
            denominator: number
            applicable_cases: number
          }
          citation_validity: { value: number; numerator: number; denominator: number }
          unsupported_claim_rate: { value: number; numerator: number; denominator: number }
          recommendation_agreement: {
            value: number
            numerator: number
            denominator: number
            ci_95_bootstrap: number[] | null
          }
          efficiency: { mean_tool_calls: number; latency_p50_ms: number; latency_p95_ms: number }
        }>
      }
    | undefined

  const navItems: Array<[string, string, string]> = [
    ['sources', 'Dữ liệu & nguồn', '📊'],
    ['report', 'Hồ sơ & bằng chứng', '📑'],
    ['review', 'Người duyệt', '⚖️'],
    ['audit', 'Audit & lượt chạy', '🛡️'],
    ['evaluation', 'Đánh giá', '📈'],
  ]

  const selectTab = (key: string) => {
    setTab(key)
    if (key === 'evaluation') void action(async () => setEvaluation(await api<Row>('/evaluation')))
  }

  return (
    <>
      <Banner
        providerMode={report?.payload.provider_mode || 'mock'}
        retrievalMode={report?.payload.retrieval_mode || 'lexical'}
      />
      <header className="topbar">
        <div className="topbar-left">
          <button
            type="button"
            className="mobile-queue-toggle"
            onClick={() => setMobileQueueOpen(v => !v)}
            aria-expanded={mobileQueueOpen}
            aria-label="Đóng mở danh sách hồ sơ"
          >
            ☰ Hồ sơ ({filteredCases.length})
          </button>
          <div className="brand">
            <b>◈</b> TraceLens <span>Investigation Workspace</span>
          </div>
        </div>
        <div className="identity">
          <div className="identity-meta">
            <strong>{actor.username}</strong>
            <small>
              {actor.tenant_id} · {actor.role}
            </small>
          </div>
          <button
            className="logout-btn"
            onClick={() =>
              void action(async () => {
                await api('/session/logout', {})
                logout()
              })
            }
          >
            Đăng xuất
          </button>
        </div>
      </header>
      <div className="app-layout">
        <aside className={`sidebar ${mobileQueueOpen ? 'mobile-open' : ''}`}>
          <p className="eyebrow">KHÔNG GIAN LÀM VIỆC</p>
          <div className="section-head">
            <h2>Hồ sơ điều tra</h2>
            <span className="count">{filteredCases.length}</span>
          </div>
          <div className="sidebar-filters">
            <label className="sr-only" htmlFor="search">
              Tìm ID hồ sơ
            </label>
            <input
              id="search"
              placeholder="🔍 Tìm ID hoặc tiêu đề hồ sơ…"
              value={filter}
              onChange={e => setFilter(e.target.value)}
            />
            <div className="filter-row">
              <label className="sr-only" htmlFor="status-filter">
                Lọc trạng thái
              </label>
              <select id="status-filter" value={status} onChange={e => setStatus(e.target.value)}>
                <option value="">Tất cả trạng thái</option>
                {['open', 'investigating', 'awaiting_review', 'reviewed', 'needs_information', 'needs_manual_review'].map(
                  s => (
                    <option key={s} value={s}>
                      {label(s)}
                    </option>
                  )
                )}
              </select>
              <label className="sr-only" htmlFor="priority-filter">
                Lọc mức độ ưu tiên
              </label>
              <select id="priority-filter" value={priorityFilter} onChange={e => setPriorityFilter(e.target.value)}>
                <option value="">Tất cả mức ưu tiên</option>
                <option value="high">Ưu tiên: Cao</option>
                <option value="medium">Ưu tiên: Trung bình</option>
              </select>
            </div>
          </div>
          <div className="case-list">
            {filteredCases.map(c => {
              const effStatus = c.id === selected && currentCase ? currentCase.status : c.status
              const pBand = c.alert?.priority_band || 'medium'
              return (
                <button
                  className={`case-item ${selected === c.id ? 'active' : ''}`}
                  key={c.id}
                  onClick={() => {
                    setSelected(c.id)
                    setTab('sources')
                    setError('')
                    setMobileQueueOpen(false)
                  }}
                >
                  <div className="case-item-top">
                    <b>{c.id}</b>
                    <span className={`priority priority-${pBand}`}>{label(pBand)}</span>
                  </div>
                  <p>{c.title}</p>
                  <div className="case-item-foot">
                    <span className={`status-dot tone-${statusTone(effStatus)}`} />
                    <small>{label(effStatus)}</small>
                  </div>
                </button>
              )
            })}
          </div>
          <div className="sidebar-note">
            <b>⚖️ Quyền quyết định thuộc người duyệt</b>
            <p>Cảnh báo là tín hiệu. Recommendation là đề xuất. Không có thao tác khóa tài khoản thật.</p>
          </div>
        </aside>
        <main className="workspace">
          {error && (
            <div className="error" role="alert">
              <span>{error}</span>
              <button onClick={() => setError('')} aria-label="Đóng lỗi">
                ✕
              </button>
            </div>
          )}
          {!loaded || !detail ? (
            <div className="empty">
              {error ? 'Không tải được hồ sơ. Kiểm tra phiên đăng nhập và backend.' : 'Đang tải dữ liệu từ database…'}
            </div>
          ) : (
            <>
              <div className="case-header">
                <div className="case-header-main">
                  <p className="eyebrow">ĐIỀU TRA VOUCHER / {selected}</p>
                  <h1>Đối chiếu trước khi kết luận.</h1>
                  <p className="case-subtitle">
                    <span>
                      <strong>{detail.data.alert.observed_features.linked_accounts}</strong> tài khoản liên kết thiết bị
                    </span>
                    <span className="sep">·</span>
                    <span>
                      <strong>{detail.data.alert.observed_features.redemption_count}</strong> redemption
                    </span>
                    <span className="sep">·</span>
                    <span>
                      Luật: <code>{(detail.data.alert.rule_ids || []).join(', ')}</code>
                    </span>
                    <span className="sep">·</span>
                    <span className={`status badge tone-${statusTone(currentCase!.status)}`}>
                      {label(currentCase!.status)}
                    </span>
                  </p>
                </div>
                <div className="header-actions">
                  <a className="button" href={`/api/cases/${selected}/export`} download>
                    Export JSON ↗
                  </a>
                  <a className="button button-subtle" href={`/api/cases/${selected}/export?format=markdown`} download>
                    Markdown ↗
                  </a>
                </div>
              </div>
              <div className="context-line">
                <span>
                  Snapshot <code>{detail.snapshot_id.slice(0, 8)}</code>
                </span>
                <span>Cutoff {time(detail.as_of)} · UTC+7</span>
                <span>
                  Nguồn: <strong>{detail.completeness === 'complete' ? 'đầy đủ trong phạm vi demo' : 'chưa đầy đủ'}</strong>
                  {detail.truncated ? ' · bị cắt' : ''}
                </span>
              </div>
              <div className="modebar">
                <div className="mode-pill-group">
                  <button className={mode === 'A' ? 'selected' : ''} onClick={() => setMode('A')}>
                    A · Dashboard & mẫu
                  </button>
                  <button
                    className={mode === 'mock' && wfMode === 'B' ? 'selected' : ''}
                    onClick={() => {
                      setMode('mock')
                      setWfMode('B')
                    }}
                  >
                    B · Checklist cố định
                  </button>
                  <button
                    className={mode === 'mock' && wfMode === 'C' ? 'selected' : ''}
                    onClick={() => {
                      setMode('mock')
                      setWfMode('C')
                    }}
                  >
                    Mock · Luồng điều tra (Mode C)
                  </button>
                </div>
                {mode === 'mock' && actor.role === 'investigator' && (
                  <button
                    className="primary run-btn"
                    disabled={busy || currentCase!.status === 'investigating'}
                    onClick={() => void start()}
                  >
                    {currentCase!.status === 'investigating'
                      ? 'Đang xử lý…'
                      : reports.length
                        ? `Điều tra lại (Mode ${wfMode}) · tạo phiên bản mới`
                        : 'Bắt đầu điều tra →'}
                  </button>
                )}
              </div>
              {mode === 'A' ? (
                <>
                  <Facts detail={detail} onSource={onSource} />
                  <section className="card">
                    <h3>Mẫu báo cáo baseline A · không LLM</h3>
                    <p>
                      Ghi quan sát, bằng chứng ủng hộ/phản bác, dữ liệu thiếu và đề xuất. Bản nháp này chỉ ở trình duyệt cho
                      đến khi tải xuống.
                    </p>
                    <label>
                      Ghi chép điều tra
                      <textarea rows={6} value={notes} onChange={e => setNotes(e.target.value)} />
                    </label>
                    <button
                      className="primary"
                      disabled={!baseline}
                      onClick={() =>
                        download(
                          `${selected}-baseline.json`,
                          JSON.stringify({ ...baseline, investigator_notes: notes }, null, 2)
                        )
                      }
                    >
                      Tải mẫu và ghi chép
                    </button>
                  </section>
                </>
              ) : (
                <>
                  <nav className="tabs" aria-label="Các phần hồ sơ">
                    {navItems.map(([key, name, icon]) => (
                      <button
                        className={tab === key ? 'selected' : ''}
                        key={key}
                        onClick={() => selectTab(key)}
                      >
                        <span className="tab-icon" aria-hidden="true">
                          {icon}
                        </span>{' '}
                        {name}
                      </button>
                    ))}
                  </nav>
                  {tab === 'sources' && <Facts detail={detail} onSource={onSource} />}
                  {(tab === 'report' || tab === 'review') &&
                    (!report ? (
                      <section className="card empty">
                        <h3>Chưa có báo cáo</h3>
                        <p>
                          {runs[0]
                            ? `${label(runs[0].status)} · ${runs[0].error_code || 'Worker riêng sẽ xử lý hàng đợi.'}`
                            : 'Điều tra viên bắt đầu lượt chạy để thu thập và kiểm chứng nguồn.'}
                        </p>
                      </section>
                    ) : (
                      <>
                        <div className="section-head version">
                          <label>
                            Phiên bản báo cáo
                            <select value={report.version} onChange={e => setVersion(Number(e.target.value))}>
                              {reports.map(r => (
                                <option key={r.id} value={r.version}>
                                  v{r.version}
                                  {r === reports[0] ? ' · mới nhất' : ' · lịch sử'}
                                </option>
                              ))}
                            </select>
                          </label>
                          <small>
                            Mode <b>{report.payload.workflow_mode || 'C'}</b> · {report.payload.provider_mode} ·{' '}
                            <code>{report.payload.model_id}</code> · {report.payload.transport || 'direct'} · Trạng thái:{' '}
                            <b>{label(report.payload.readiness_state || 'ready_for_review')}</b>
                          </small>
                        </div>
                        <section className={`recommendation rec-${statusTone(report.payload.recommendation)}`}>
                          <p className="eyebrow">ĐỀ XUẤT — CHƯA PHẢI QUYẾT ĐỊNH</p>
                          <h2>{label(report.payload.recommendation)}</h2>
                          <p>{report.payload.summary}</p>
                          <div className="report-facts">
                            <span>
                              Ròng: <b>{money(report.payload.facts.net_vnd)}</b>
                            </span>
                            <span>
                              Chồng lấn: <b>{report.payload.facts.overlap_pairs}</b>
                            </span>
                            <span>
                              Chuyến: <b>{report.payload.facts.trip_count}</b>
                            </span>
                            {report.payload.usage && (
                              <span>
                                Ngân sách tool:{' '}
                                <b>
                                  {report.payload.usage.tool_calls}/{report.payload.usage.max_tool_calls}
                                </b>
                              </span>
                            )}
                          </div>
                        </section>
                        <div className="two-col">
                          <Claims
                            title="Bằng chứng cần kiểm tra"
                            claims={report.payload.supporting_claims}
                            onEvidence={onEvidence}
                          />
                          <Claims
                            title="Bằng chứng phản bác"
                            claims={report.payload.counterevidence_claims}
                            onEvidence={onEvidence}
                            tone="counter"
                          />
                        </div>
                        <Claims
                          title="Thông tin còn thiếu"
                          claims={report.payload.missing_information}
                          onEvidence={onEvidence}
                        />
                        <section className="card">
                          <h3>Giả thuyết & bước tiếp theo</h3>
                          <div className="hypothesis-list">
                            {report.payload.hypotheses.map(h => (
                              <p key={h} className="hypothesis-item">
                                ○ {h}
                              </p>
                            ))}
                          </div>
                          {report.payload.planned_steps && report.payload.planned_steps.length > 0 && (
                            <div className="table-wrap" style={{ margin: '14px 0' }}>
                              <table>
                                <thead>
                                  <tr>
                                    <th>Tool chọn</th>
                                    <th>Câu hỏi kiểm chứng</th>
                                    <th>Lý do kiểm toán (không lưu CoT)</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {report.payload.planned_steps.map((ps, idx) => (
                                    <tr key={idx}>
                                      <td>
                                        <code>{ps.tool_name}</code>
                                      </td>
                                      <td>{ps.question}</td>
                                      <td>{ps.rationale}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                          <div className="next-steps-list">
                            {report.payload.next_steps.map(s => (
                              <p key={s}>→ {s}</p>
                            ))}
                          </div>
                          <p className="hint">
                            Đã kiểm tra schema, nguồn, scope, snapshot và facts. Suy luận vẫn cần người duyệt.
                          </p>
                          <div className="citation-list">
                            {report.payload.policy_refs.map(ref => (
                              <button className="citation" key={ref} onClick={() => onSource(ref)}>
                                📜 {ref} ↗
                              </button>
                            ))}
                          </div>
                        </section>
                        {tab === 'review' && (
                          <section className="card review">
                            <h3>Quyết định trong sandbox</h3>
                            <p>
                              Quyết định gắn với v{report.version}, hash{' '}
                              <code>{report.content_hash.slice(0, 12)}</code> và snapshot hiện tại.
                            </p>
                            {actor.role !== 'reviewer' ? (
                              <p className="notice">
                                Đăng nhập bằng tài khoản <b>reviewer</b> để gửi quyết định. Vai trò hiện tại ({actor.role})
                                chỉ được phép xem.
                              </p>
                            ) : currentCase!.status !== 'awaiting_review' ? (
                              <p className="notice">
                                {currentCase!.disposition ? label(currentCase!.disposition) : label(currentCase!.status)}.
                                Lịch sử quyết định được giữ trong audit/export.
                              </p>
                            ) : (
                              <form
                                onSubmit={e => {
                                  e.preventDefault()
                                  void action(async () => {
                                    await api(`/cases/${selected}/review`, {
                                      expected_version: report.version,
                                      report_hash: report.content_hash,
                                      snapshot_id: report.snapshot_id,
                                      disposition,
                                      rationale,
                                      idempotency_key: crypto.randomUUID(),
                                    })
                                    const [d, r, run, a] = await refreshCase(selected)
                                    setDetail(d)
                                    setReports(r)
                                    setRuns(run)
                                    setEvents(a)
                                    await refreshList()
                                  })
                                }}
                              >
                                <label>
                                  Disposition
                                  <select
                                    required
                                    value={disposition}
                                    onChange={e => setDisposition(e.target.value)}
                                  >
                                    <option value="">Chọn sau khi xem bằng chứng…</option>
                                    {['CLOSE_NO_ACTION', 'REQUEST_MORE_INFORMATION', 'ESCALATE_FOR_MANUAL_REVIEW'].map(
                                      d => (
                                        <option key={d} value={d}>
                                          {label(d)}
                                        </option>
                                      )
                                    )}
                                  </select>
                                </label>
                                <label>
                                  Lý do / thông tin cụ thể cần bổ sung
                                  <textarea
                                    required
                                    minLength={10}
                                    maxLength={3000}
                                    rows={4}
                                    placeholder="Nhập lý do thẩm định tối thiểu 10 ký tự..."
                                    value={rationale}
                                    onChange={e => setRationale(e.target.value)}
                                  />
                                </label>
                                <button
                                  className="primary"
                                  disabled={busy || !disposition || rationale.trim().length < 10}
                                >
                                  Ghi quyết định sandbox
                                </button>
                              </form>
                            )}
                          </section>
                        )}
                      </>
                    ))}
                  {tab === 'audit' && (
                    <>
                      <section className="card">
                        <h3>Lượt chạy · polling từ trạng thái đã lưu</h3>
                        {runs.length ? (
                          runs.map(r => (
                            <div className="run" key={r.id}>
                              <span className={`badge tone-${statusTone(r.status)}`}>{label(r.status)}</span>
                              <code>{r.id.slice(0, 8)}</code>
                              <span>
                                Mode <b>{r.workflow_mode || 'C'}</b> · {r.provider_mode} / {r.transport || 'direct'} ·{' '}
                                {r.retrieval_mode || 'lexical'}
                              </span>
                              <small>{time(r.created_at)}</small>
                              {r.error_code && <span className="error">{r.error_code}</span>}
                            </div>
                          ))
                        ) : (
                          <p className="hint">Chưa có lượt chạy.</p>
                        )}
                      </section>
                      <section className="card">
                        <h3>Audit trail</h3>
                        <ol className="audit">
                          {events.map(e => (
                            <li key={e.id}>
                              <div>
                                <b>{e.action}</b>
                                <small>
                                  {time(e.created_at)} · {e.actor_id}
                                </small>
                              </div>
                              <pre>{JSON.stringify(e.payload, null, 2)}</pre>
                            </li>
                          ))}
                        </ol>
                      </section>
                    </>
                  )}
                  {tab === 'evaluation' && (
                    <section className="card">
                      <p className="eyebrow">TRẠNG THÁI THỰC TẾ</p>
                      <h2>Benchmark Live / Human Study: {String(evaluation?.status || 'đang tải')}</h2>
                      <p>
                        Live model: <code>{String(evaluation?.live_model || 'not_run')}</code> · Human study:{' '}
                        <code>{String(evaluation?.human_study || 'not_run')}</code>. Hai hồ sơ demo không chứng minh khả
                        năng phát hiện gian lận hay tiết kiệm thời gian ngoài thực tế.
                      </p>
                      {mockBench && mockBench.modes && (
                        <div style={{ marginTop: 18 }}>
                          <h3>Kiểm chứng đường ống A / B / C (Offline Mock Plumbing)</h3>
                          <p className="hint">
                            Split: <code>{mockBench.dataset?.split}</code> ({mockBench.dataset?.case_count} ca, seed{' '}
                            {mockBench.dataset?.seed}) · SHA-256:{' '}
                            <code>{mockBench.dataset?.dataset_sha256?.slice(0, 12)}</code>. {mockBench.disclaimer}
                          </p>
                          <div className="table-wrap">
                            <table>
                              <thead>
                                <tr>
                                  <th>Chế độ</th>
                                  <th>Evidence Recall</th>
                                  <th>Counterevidence Recall</th>
                                  <th>Citation Validity</th>
                                  <th>Unsupported Claims</th>
                                  <th>Khớp Rubric (95% CI)</th>
                                  <th>Số Tool TB</th>
                                  <th>Latency P50/P95</th>
                                </tr>
                              </thead>
                              <tbody>
                                {mockBench.modes.map(m => (
                                  <tr key={m.mode}>
                                    <td>
                                      <b>Mode {m.mode}</b> (n={m.sample_size})
                                    </td>
                                    <td>
                                      {(m.evidence_recall.value * 100).toFixed(1)}% ({m.evidence_recall.numerator}/
                                      {m.evidence_recall.denominator})
                                    </td>
                                    <td>
                                      {m.counterevidence_recall.value === null
                                        ? 'N/A'
                                        : `${(m.counterevidence_recall.value * 100).toFixed(1)}% (${m.counterevidence_recall.numerator}/${m.counterevidence_recall.denominator})`}
                                    </td>
                                    <td>
                                      {(m.citation_validity.value * 100).toFixed(1)}% ({m.citation_validity.numerator}/
                                      {m.citation_validity.denominator})
                                    </td>
                                    <td>
                                      {(m.unsupported_claim_rate.value * 100).toFixed(1)}% (
                                      {m.unsupported_claim_rate.numerator}/{m.unsupported_claim_rate.denominator})
                                    </td>
                                    <td>
                                      <strong>{(m.recommendation_agreement.value * 100).toFixed(1)}%</strong> (
                                      {m.recommendation_agreement.numerator}/{m.recommendation_agreement.denominator})
                                      {m.recommendation_agreement.ci_95_bootstrap
                                        ? ` [${(m.recommendation_agreement.ci_95_bootstrap[0] * 100).toFixed(1)}–${(m.recommendation_agreement.ci_95_bootstrap[1] * 100).toFixed(1)}%]`
                                        : ''}
                                    </td>
                                    <td>{m.efficiency.mean_tool_calls}</td>
                                    <td>
                                      {m.efficiency.latency_p50_ms} / {m.efficiency.latency_p95_ms} ms
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                      <p className="hint" style={{ marginTop: 14 }}>
                        Mock chỉ kiểm chứng đường truyền dữ liệu, phép tính, quyền, workflow và UI.
                      </p>
                    </section>
                  )}
                </>
              )}
            </>
          )}
        </main>
      </div>
      {mode !== 'A' && loaded && detail && (
        <nav className="mobile-bottom-nav" aria-label="Điều hướng nhanh trên di động">
          {navItems.map(([key, name, icon]) => (
            <button
              type="button"
              key={key}
              className={tab === key ? 'selected' : ''}
              onClick={() => selectTab(key)}
            >
              <span className="mobile-nav-icon" aria-hidden="true">
                {icon}
              </span>
              <span className="mobile-nav-label">{name.split(' ')[0]}</span>
            </button>
          ))}
        </nav>
      )}
      {drawer && <Drawer value={drawer} onClose={() => setDrawer(null)} onSource={onSource} />}
    </>
  )
}

function App() {
  const [actor, setActor] = useState<Actor | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    api<{ actor: Actor; csrf_token: string }>('/session')
      .then(v => {
        setCSRF(v.csrf_token)
        setActor(v.actor)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])
  return loading ? (
    <div className="empty">Đang kiểm tra phiên…</div>
  ) : actor ? (
    <Workspace
      actor={actor}
      logout={() => {
        setActor(null)
        setCSRF('')
      }}
    />
  ) : (
    <Login onLogin={setActor} />
  )
}

createRoot(document.getElementById('root')!).render(<App />)
