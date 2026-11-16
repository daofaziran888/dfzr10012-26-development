import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/parallel-suggest'
const columns = [
  "建议编号",
  "流算子名",
  "阻塞算子",
  "当前并行度",
  "建议值",
  "吞吐收益",
  "资源成本",
  "节点容量",
  "反压级别",
  "数据版本",
  "审批状态",
  "审批时间"
]
const statuses = [
  "待审批",
  "已批准",
  "已驳回",
  "已拦截",
  "已失效"
]
const stats = [
  {
    "label": "建议记录总数",
    "value": 0
  },
  {
    "label": "待审批建议",
    "value": 0
  },
  {
    "label": "已批准建议",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type CheckResult = {
  "核对项": string
  "取值": string | number | null
  "标准": string
  "通过": boolean
  "阻断审批": boolean
  "说明": string
}

export default function ParallelSuggestView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [form, setForm] = useState({ backId: 'BACK-0002', paraId: 'PARA-0002' })
  const [detail, setDetail] = useState<Row | null>(null)
  const filterFields = useMemo(() => ["建议编号", "流算子名", "审批状态"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('并行度建议列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '并行度建议列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const counters = useMemo(() => {
    const pending = rows.filter((row) => row["审批状态"] === "待审批").length
    const approved = rows.filter((row) => row["审批状态"] === "已批准").length
    return [rows.length, pending, approved]
  }, [rows])

  async function generate() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({
          values: { 指标编号: form.backId.trim(), 调优记录编号: form.paraId.trim() },
        }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload?.detail ?? '并行度建议生成失败')
      }
      if (payload.entry?.["建议编号"]) {
        setDetail(payload.entry)
      }
      setNotice(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '并行度建议生成失败')
    }
  }

  async function decide(row: Row, decision: string) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/approval`, {
        method: 'POST',
        body: JSON.stringify({
          values: { decision, 审批人: '值班管理员' },
        }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload?.detail ?? '建议审批未生效')
      }
      setNotice(payload.message)
      await reload()
      if (payload.entry) {
        setDetail(payload.entry)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '建议审批失败')
    }
  }

  async function revoke(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/revoke`, {
        method: 'POST',
        body: JSON.stringify({ values: { 操作人: '值班管理员' } }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload?.detail ?? '建议撤销未生效')
      }
      setNotice(payload.message)
      await reload()
      setDetail(null)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '建议撤销失败')
    }
  }

  const checks = (detail?.["校验结果"] as CheckResult[] | undefined) ?? []

  return (
    <section className="page" data-module="parallel_suggest">
      <header className="page-head">
        <div>
          <h2>并行度建议与资源审批</h2>
          <p className="page-desc">按反压采样核对调优字段与节点容量，生成并行度建议数据记录；审批通过前作业实例保持原容量。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出建议记录清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item, index) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{counters[index]}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void generate() }}>
        <label className="filter-item">
          <span>反压指标编号</span>
          <input
            value={form.backId}
            placeholder="例如 BACK-0002"
            onChange={(event) => setForm((prev) => ({ ...prev, backId: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>调优记录编号</span>
          <input
            value={form.paraId}
            placeholder="例如 PARA-0002"
            onChange={(event) => setForm((prev) => ({ ...prev, paraId: event.target.value }))}
          />
        </label>
        <button className="btn primary" type="submit">生成并行度建议</button>
      </form>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field === "流算子名" ? "流算子名（精确）" : field}</span>
            {field === "审批状态" ? (
              <select
                value={filters[field] ?? ''}
                onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
              >
                <option value="">全部</option>
                {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
              </select>
            ) : (
              <input
                value={filters[field] ?? ''}
                placeholder={`按${field}检索`}
                onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
              />
            )}
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => setDetail(row)}>查看核对</button>
                {row["审批状态"] === "待审批" && (
                  <>
                    <button className="link" type="button" onClick={() => void decide(row, "批准")}>批准</button>
                    <button className="link" type="button" onClick={() => void decide(row, "驳回")}>驳回</button>
                  </>
                )}
                {row["审批状态"] === "已批准" && (
                  <button className="link" type="button" onClick={() => void revoke(row)}>撤销建议</button>
                )}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无并行度建议记录，可用反压指标生成建议</td>
            </tr>
          )}
        </tbody>
      </table>

      {detail && (
        <section className="detail-panel">
          <header className="detail-head">
            <strong>{String(detail["建议编号"] ?? '')} 核对结果</strong>
            <button className="btn ghost" type="button" onClick={() => setDetail(null)}>收起</button>
          </header>
          <p className="page-desc">
            阻塞算子：{String(detail["阻塞算子"] ?? '—')}；建议值：{String(detail["建议值"] ?? '—')}；
            吞吐收益：{String(detail["吞吐收益"] ?? '—')}；审批状态：{String(detail["审批状态"] ?? '—')}
          </p>
          {detail["冲突说明"] ? <p className="error-text">{String(detail["冲突说明"])}</p> : null}
          <table className="data-table">
            <thead>
              <tr><th>核对项</th><th>取值</th><th>标准</th><th>结果</th><th>说明</th></tr>
            </thead>
            <tbody>
              {checks.map((item) => (
                <tr key={item["核对项"]}>
                  <td>{item["核对项"]}</td>
                  <td>{item["取值"]}</td>
                  <td>{item["标准"]}</td>
                  <td>{item["通过"] ? "通过" : (item["阻断审批"] ? "不通过（阻断审批）" : "不通过（提示）")}</td>
                  <td>{item["说明"]}</td>
                </tr>
              ))}
              {checks.length === 0 && (
                <tr><td className="empty-state" colSpan={5}>该记录没有核对明细</td></tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条并行度建议记录</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
