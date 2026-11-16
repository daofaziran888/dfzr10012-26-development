"""并行度建议与资源审批接口：从反压采样生成建议，校验资源成本后走审批。

链路：读反压指标（BACK）与调优记录（PARA）→ 逐项核对取值范围、标准上限与资源成本
→ 写入并行度建议数据记录 → 审批通过时与作业实例容量一起落库。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.parallel_suggest import ParallelSuggestService

router = APIRouter(prefix="/api/parallel-suggest", tags=["并行度建议与资源审批"])

service = ParallelSuggestService()

LIST_FIELDS = [
    "建议编号",
    "流算子名",
    "阻塞算子",
    "当前并行度",
    "建议值",
    "吞吐收益",
    "资源成本",
    "节点容量",
    "指标编号",
    "反压级别",
    "数据版本",
    "审批状态",
    "审批时间",
]
STATUSES = ["待审批", "已批准", "已驳回", "已拦截", "已失效"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按建议编号、流算子名或调优记录编号检索"),
    status: str | None = Query(default=None, description="待审批、已批准、已驳回、已拦截、已失效"),
    operator: str | None = Query(default=None, description="按流算子名精确过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按编号、状态与算子过滤并行度建议记录；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, operator=operator, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """按反压采样生成并行度建议；不满足条件时记录会保留并写明逐项原因。"""
    entry, message, checks = service.generate(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message, entry={"核对结果": checks})
    return ActionResult(ok=entry["审批状态"] == "待审批", message=message, entry=entry)


@router.get("/capacity/{job_id}", response_model=dict)
def get_capacity(job_id: int) -> dict:
    """跨模块统一读取口径：返回作业实例容量与它引用的那一份建议记录，供消费侧沿用。"""
    capacity = service.resolve_capacity(job_id)
    if capacity is None:
        raise HTTPException(status_code=404, detail=f"作业实例 {job_id} 不存在")
    return capacity


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条并行度建议明细，包括逐项核对结果与审批信息。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"并行度建议 {entry_id} 不存在或已失效")
    return entry


@router.post("/{entry_id}/approval", response_model=ActionResult)
def decide(entry_id: int, payload: EntryPayload) -> ActionResult:
    """审批单条建议：批准时建议结果与作业实例容量一起落库，驳回时作业实例保持原容量。"""
    decision = str(payload.values.get("decision") or payload.values.get("审批动作") or "").strip()
    reviewer = str(payload.values.get("审批人") or "").strip()
    comment = str(payload.values.get("审批意见") or payload.remark or "").strip()
    if not reviewer:
        return ActionResult(ok=False, message="缺少审批人，审批结果无法落库")
    entry, message = service.decide(entry_id, decision, reviewer, comment)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/revoke", response_model=ActionResult)
def revoke(entry_id: int, payload: EntryPayload) -> ActionResult:
    """撤销已生效建议：作业实例保留当前容量，但解除对该建议的引用，可重新提交新建议。"""
    reviewer = str(payload.values.get("操作人") or payload.values.get("审批人") or "").strip()
    comment = str(payload.values.get("撤销意见") or payload.remark or "").strip()
    entry, message = service.revoke(entry_id, reviewer, comment)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出并行度建议清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "parallel_suggest", "total": total, "items": items}
