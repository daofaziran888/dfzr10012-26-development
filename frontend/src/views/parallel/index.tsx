import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/parallel'
const columns = [
  "记录编号",
  "流算子名",
  "当前并行度",
  "建议并行度",
  "吞吐提升",
  "资源成本",
  "调优时间",
  "记录状态"
]
const actions = [
  "执行调优",
  "回滚调优",
  "归档记录"
]
const statuses = [
  "待调优",
  "已调优",
  "已回滚",
  "已归档"
]
const stats = [
  {
    "label": "调优记录总数",
    "value": 0
  },
  {
    "label": "已调优记录",
    "value": 0
  },
  {
    "label": "已回滚记录",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

const emptyForm = { 记录编号: '', 流算子名: '', 当前并行度: '', 建议并行度: '', 吞吐提升: '', 资源成本: '' }

export default function ParallelView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [form, setForm] = useState<Record<string, string>>(emptyForm)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (filters["记录编号"]) query.set('keyword', filters["记录编号"])
    if (filters["记录状态"]) query.set('status', filters["记录状态"])
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('调优记录列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '并行度调优列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function createEntry() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: { ...form } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.detail ?? payload.message ?? '调优记录登记失败')
      }
      setNotice(payload.message)
      setForm(emptyForm)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '调优记录登记失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.detail ?? payload.message ?? '并行度调优动作未生效，请稍后重试')
      }
      setNotice(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '并行度调优操作失败')
    }
  }

  return (
    <section className="page" data-module="parallel">
      <header className="page-head">
        <div>
          <h2>并行度调优管理</h2>
          <p className="page-desc">维护并行度调优的当前配置、优化建议与收益评估，为并行度调优提供可审计的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open('/parallel-suggest', '_blank')}>
            并行度建议与审批
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出并行度调优清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => { event.preventDefault(); void createEntry() }}
      >
        {Object.keys(emptyForm).map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={form[field] ?? ''}
              placeholder={`填写${field}`}
              onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn primary" type="submit">登记调优记录</button>
      </form>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>记录编号</span>
          <input
            value={filters["记录编号"] ?? ''}
            placeholder="按记录编号检索"
            onChange={(event) => setFilters((prev) => ({ ...prev, 记录编号: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>记录状态</span>
          <select
            value={filters["记录状态"] ?? ''}
            onChange={(event) => setFilters((prev) => ({ ...prev, 记录状态: event.target.value }))}
          >
            <option value="">全部</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
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
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无并行度调优数据，可先登记调优记录</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条并行度调优记录</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
