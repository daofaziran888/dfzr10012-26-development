import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/jobops'
const columns = [
  "实例编号",
  "作业名称",
  "运行节点",
  "启动时间",
  "运行时长",
  "异常次数",
  "重启次数",
  "实例状态",
  "并行度容量",
  "节点容量",
  "并行度建议编号",
  "建议数据版本"
]
const actions = [
  "启动作业",
  "暂停作业",
  "停止作业"
]
const statuses = [
  "待启动",
  "运行中",
  "已暂停",
  "已停止"
]
const stats = [
  {
    "label": "作业实例总数",
    "value": 0
  },
  {
    "label": "运行中作业",
    "value": 0
  },
  {
    "label": "未绑定建议记录",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function JobopsView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})

  const counters = useMemo(() => {
    const running = rows.filter((row) => row["实例状态"] === "运行中").length
    const waiting = rows.filter((row) => !row["并行度建议编号"]).length
    return [rows.length, running, waiting]
  }, [rows])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (filters["实例编号"]) query.set('keyword', filters["实例编号"])
    if (filters["实例状态"]) query.set('status', filters["实例状态"])
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('作业实例列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流作业运维列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('流作业运维动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流作业运维操作失败')
    }
  }

  return (
    <section className="page" data-module="jobops">
      <header className="page-head">
        <div>
          <h2>流作业运维管理</h2>
          <p className="page-desc">维护流作业的运行节点、启停管理与异常追踪，为流作业运维提供可审计的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('作业实例登记入口尚未接入审批流')}>
            登记作业实例
          </button>
          <button className="btn" type="button" onClick={() => window.open('/parallel-suggest', '_blank')}>
            查看并行度建议
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出流作业运维清单
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

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>实例编号</span>
          <input
            value={filters["实例编号"] ?? ''}
            placeholder="按实例编号检索"
            onChange={(event) => setFilters((prev) => ({ ...prev, 实例编号: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>实例状态</span>
          <select
            value={filters["实例状态"] ?? ''}
            onChange={(event) => setFilters((prev) => ({ ...prev, 实例状态: event.target.value }))}
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无流作业运维数据，可先登记作业实例</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条流作业运维记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
