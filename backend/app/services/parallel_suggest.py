"""并行度建议与资源审批业务规则。

链路：反压指标（BACK）采样 → 按流算子名、当前并行度和建议并行度匹配调优记录（PARA）
→ 逐项核对取值范围、标准上限、资源成本与吞吐收益 → 写入并行度建议数据记录
→ 审批通过时，建议结果与作业实例容量在同一次处理里一起落库。

几条硬约束都收在这里，路由层只负责转发：
- 同一算子并发采样只保留最新建议：旧的待审批建议置为「已失效」，数据版本逐次递增；
- 资源成本超过节点容量时建议直接置为「已拦截」，不得进入审批；
- 作业实例容量只能引用一套经过验证的建议，撤销旧建议前新建议不能同时生效；
- 跨模块统一走 ``resolve_capacity`` 读取同一份建议数据版本，消费侧沿用该版本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "parallel_suggest"
PARALLEL_MODULE = "parallel"
BACKPRESS_MODULE = "backpress"
JOBOPS_MODULE = "jobops"

MIN_PARALLELISM = 1
MAX_PARALLELISM = 64
APPROVABLE_BACKPRESSURE = "严重反压"

STATUS_PENDING = "待审批"
STATUS_APPROVED = "已批准"
STATUS_REJECTED = "已驳回"
STATUS_BLOCKED = "已拦截"
STATUS_SUPERSEDED = "已失效"
STATUS_ORDER = [STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_BLOCKED, STATUS_SUPERSEDED]
ACTIVE_STATUSES = [STATUS_PENDING, STATUS_APPROVED]
DECISIONS = {"批准": STATUS_APPROVED, "驳回": STATUS_REJECTED}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_int(value: Any) -> int | None:
    """把并行度、资源成本等字段转成整数；转不了就返回 None，由核对项说明原因。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_percent(value: Any) -> float | None:
    """解析「吞吐提升」里的百分比；支持 40%、+40、-15% 这类写法。"""
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class ParallelSuggestService:
    # ---------- 读取 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        operator: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("建议编号", ""))
                or keyword in str(row.get("流算子名", ""))
                or keyword in str(row.get("调优记录编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("审批状态") == status]
        if operator:
            rows = [row for row in rows if row.get("流算子名") == operator]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def resolve_capacity(self, job_instance_id: int) -> dict[str, Any] | None:
        """跨模块统一读取口径：作业实例容量 + 它引用的那一份建议记录，消费侧沿用该版本。"""
        job = store.find(JOBOPS_MODULE, job_instance_id)
        if job is None:
            return None
        reference = str(job.get("并行度建议编号") or "")
        suggestion = None
        if reference:
            suggestion = next(
                (row for row in store.rows(MODULE) if row.get("建议编号") == reference),
                None,
            )
        return {
            "实例编号": job.get("实例编号"),
            "作业名称": job.get("作业名称"),
            "运行节点": job.get("运行节点"),
            "并行度容量": to_int(job.get("并行度容量")),
            "节点容量": to_int(job.get("节点容量")),
            "并行度建议编号": reference,
            "建议数据版本": to_int(job.get("建议数据版本")) or 0,
            "容量更新时间": job.get("容量更新时间"),
            "建议记录": suggestion,
        }

    # ---------- 生成建议 ----------
    def generate(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        """按反压采样生成并行度建议数据记录。

        记录无论是否通过核对都会落库；未通过时审批状态为「已拦截」并写明原因，
        方便回看这次采样为什么不满足条件。
        """
        back_id = values.get("指标编号") or values.get("backpress_id")
        back = self._find_backpress(back_id)
        if back is None:
            return None, f"反压指标 {back_id} 不存在，无法生成并行度建议", []

        para_id = values.get("调优记录编号") or values.get("parallel_id")
        if para_id is not None and str(para_id).strip():
            para = self._find_parallel(para_id, "")
            if para is None:
                return None, (
                    f"调优记录 {para_id} 不存在，请先用流算子名、当前并行度和建议并行度"
                    "核对资源建议"
                ), []
        else:
            para = self._find_parallel(None, str(back.get("阻塞算子") or ""))
        if para is None:
            return None, (
                f"未找到阻塞算子「{back.get('阻塞算子')}」对应的调优记录，"
                "请先用流算子名、当前并行度和建议并行度核对资源建议"
            ), []

        operator = str(para.get("流算子名") or "").strip()
        job = self._find_job(values, operator)
        node_capacity = to_int(job.get("节点容量")) if job else None
        job_capacity = to_int(job.get("并行度容量")) if job else None

        current = to_int(para.get("当前并行度"))
        suggested = to_int(para.get("建议并行度"))
        cost = to_int(para.get("资源成本"))
        gain = parse_percent(para.get("吞吐提升"))
        level = str(back.get("反压级别") or "").strip()

        checks: list[dict[str, Any]] = []

        def add(
            name: str,
            value: Any,
            standard: str,
            passed: bool,
            reason: str,
            *,
            blocking: bool = True,
        ) -> None:
            checks.append({
                "核对项": name,
                "取值": value,
                "标准": standard,
                "通过": passed,
                "阻断审批": blocking,
                "说明": "通过" if passed else reason,
            })

        add("反压指标存在", str(back.get("指标编号") or ""), "反压指标编号有效", True, "")
        add("调优记录存在", str(para.get("记录编号") or ""), "调优记录编号有效", True, "")
        add(
            "流算子名核对",
            f"调优记录={operator} / 反压阻塞算子={back.get('阻塞算子')}",
            "两者一致",
            bool(operator) and operator == str(back.get("阻塞算子") or "").strip(),
            "调优记录的流算子名与反压指标的阻塞算子不一致，资源建议无法对齐",
        )
        add(
            "作业实例存在",
            str(job.get("实例编号") or "") if job else "未匹配",
            "存在关联的作业实例",
            job is not None,
            f"流算子「{operator}」没有关联的作业实例，建议无法写入容量",
        )
        add(
            "当前并行度核对",
            f"调优记录={current} / 作业实例容量={job_capacity}",
            "两者一致",
            current is not None and current == job_capacity,
            "调优记录的当前并行度与作业实例容量不一致，请先核对调优记录",
            blocking=False,
        )
        add(
            "当前并行度取值",
            current,
            f"整数且位于 {MIN_PARALLELISM}-{MAX_PARALLELISM}",
            current is not None and MIN_PARALLELISM <= current <= MAX_PARALLELISM,
            f"当前并行度缺失或超出标准取值范围 {MIN_PARALLELISM}-{MAX_PARALLELISM}",
        )
        add(
            "建议并行度取值",
            suggested,
            f"整数且位于 {MIN_PARALLELISM}-{MAX_PARALLELISM}",
            suggested is not None and MIN_PARALLELISM <= suggested <= MAX_PARALLELISM,
            f"建议并行度缺失或超出标准取值范围 {MIN_PARALLELISM}-{MAX_PARALLELISM}",
        )
        add(
            "扩容方向",
            f"当前={current} / 建议={suggested}",
            "建议并行度大于当前并行度",
            current is not None and suggested is not None and suggested > current,
            "建议并行度未高于当前并行度，不属于扩容建议",
        )
        add(
            "标准上限",
            f"建议={suggested} / 上限={MAX_PARALLELISM}",
            f"建议并行度不超过 {MAX_PARALLELISM}",
            suggested is not None and suggested <= MAX_PARALLELISM,
            f"建议并行度超过平台标准上限 {MAX_PARALLELISM}",
        )
        add(
            "节点容量上限",
            f"建议={suggested} / 节点容量={node_capacity}",
            "建议并行度不超过节点容量",
            suggested is not None and node_capacity is not None and suggested <= node_capacity,
            "建议并行度超过作业实例所在节点容量",
        )
        add(
            "资源成本取值",
            cost,
            "正整数",
            cost is not None and cost > 0,
            "资源成本缺失或不是正整数",
        )
        add(
            "资源成本与节点容量",
            f"资源成本={cost} / 节点容量={node_capacity}",
            "资源成本不超过节点容量，超过则不得进入审批",
            cost is not None and node_capacity is not None and cost <= node_capacity,
            f"资源成本 {cost} 超过节点容量 {node_capacity}，建议不得进入审批",
        )
        add(
            "吞吐收益",
            f"{para.get('吞吐提升')}",
            "吞吐提升为正收益",
            gain is not None and gain > 0,
            "吞吐收益缺失或不为正，扩容缺乏收益依据",
        )
        add(
            "反压级别",
            level,
            f"达到「{APPROVABLE_BACKPRESSURE}」才可进入审批",
            level == APPROVABLE_BACKPRESSURE,
            f"反压级别为「{level or '未知'}」，未达到「{APPROVABLE_BACKPRESSURE}」扩容阈值",
        )

        rows = store.rows(MODULE)
        same_operator = [row for row in rows if row.get("流算子名") == operator]
        version = max([int(row.get("数据版本", 0)) for row in same_operator] + [0]) + 1

        conflict_note = ""
        blocking = [item for item in checks if not item["通过"] and item["阻断审批"]]
        if not blocking:
            # 只有新采样本身通过核对，才参与「只保留最新建议」的替换；
            # 被拦下的采样不能顺手把一条有效的待审批建议置为失效。
            bound = next(
                (
                    row for row in self._active_suggestions(operator)
                    if row.get("审批状态") == STATUS_APPROVED
                    and job is not None
                    and str(job.get("并行度建议编号") or "") == str(row.get("建议编号") or "")
                ),
                None,
            )
            if bound is not None:
                conflict_note = (
                    f"作业实例 {job.get('实例编号')} 的容量已引用建议 {bound.get('建议编号')}"
                    f"（数据版本 {bound.get('数据版本')}，生效并行度 {bound.get('生效并行度')}），"
                    "不能同时保留旧容量和本次新建议；请先撤销旧建议再审批"
                )
                add(
                    "容量冲突",
                    f"已生效建议={bound.get('建议编号')} / 本次建议版本={version}",
                    "作业容量只能采用一套经过验证的建议记录",
                    False,
                    conflict_note,
                )
            else:
                for row in self._active_suggestions(operator):
                    row["审批状态"] = STATUS_SUPERSEDED
                    row["status"] = STATUS_SUPERSEDED
                    row["pending"] = False
                    row["失效原因"] = (
                        f"同一算子产生更新的采样建议（数据版本 {version}），仅保留最新建议"
                    )
                    row["失效时间"] = _now()
                    conflict_note = (
                        f"同一算子已有建议 {row.get('建议编号')}（数据版本 "
                        f"{row.get('数据版本')}），已置为已失效，仅保留本次最新建议"
                    )

        failed = [item for item in checks if not item["通过"] and item["阻断审批"]]
        warnings = [item for item in checks if not item["通过"] and not item["阻断审批"]]

        entry = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "建议编号": self._next_code(rows),
            "流算子名": operator,
            "阻塞算子": str(back.get("阻塞算子") or ""),
            "当前并行度": current,
            "建议值": suggested,
            "建议并行度": suggested,
            "吞吐收益": str(para.get("吞吐提升") or ""),
            "资源成本": cost,
            "节点容量": node_capacity,
            "指标编号": str(back.get("指标编号") or ""),
            "反压级别": level,
            "调优记录编号": str(para.get("记录编号") or ""),
            "作业实例编号": str(job.get("实例编号") or "") if job else "",
            "作业实例ID": int(job.get("id", 0)) if job else None,
            "数据版本": version,
            "审批状态": STATUS_PENDING if not failed else STATUS_BLOCKED,
            "审批人": "",
            "审批意见": "",
            "审批时间": "",
            "生效并行度": None,
            "校验结果": checks,
            "未通过项": [item["核对项"] for item in failed],
            "提示项": [item["核对项"] for item in warnings],
            "核对说明": "；".join(f"{item['核对项']}：{item['说明']}" for item in failed),
            "冲突说明": conflict_note,
            "生成时间": _now(),
        }
        entry["status"] = entry["审批状态"]
        entry["pending"] = entry["审批状态"] == STATUS_PENDING
        entry["abnormal"] = bool(failed) or bool(conflict_note)
        rows.append(entry)

        if failed:
            detail = "；".join(
                "{}（{}）".format(item["核对项"], item["说明"]) for item in failed
            )
            message = (
                f"并行度建议已生成但未通过核对，审批状态为「{STATUS_BLOCKED}」；"
                f"原因：{detail}"
            )
        else:
            message = "并行度建议已生成，等待资源审批；审批前作业实例保持原容量"
            if conflict_note:
                message = f"{message}；{conflict_note}"
        if warnings:
            message = (
                f"{message}；提示："
                + "；".join("{}（{}）".format(item["核对项"], item["说明"]) for item in warnings)
            )
        return entry, message, checks

    # ---------- 审批 ----------
    def decide(
        self,
        entry_id: int,
        decision: str,
        reviewer: str,
        comment: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"并行度建议 {entry_id} 不存在或已失效"
        if decision not in DECISIONS:
            return None, f"审批动作「{decision}」不在允许范围内，只能批准或驳回"
        if entry.get("审批状态") != STATUS_PENDING:
            return None, (
                f"并行度建议 {entry.get('建议编号')} 当前审批状态为「{entry.get('审批状态')}」，"
                "只有待审批的建议可以处理"
            )

        job = store.find(JOBOPS_MODULE, int(entry.get("作业实例ID") or 0))
        if job is None:
            return None, "作业实例不存在，审批结果无法与容量一起落库"

        now = _now()
        if decision == "驳回":
            entry["审批状态"] = STATUS_REJECTED
            entry["status"] = STATUS_REJECTED
            entry["pending"] = False
            entry["审批人"] = reviewer
            entry["审批意见"] = comment
            entry["审批时间"] = now
            entry["生效并行度"] = None
            return entry, "并行度建议已驳回，作业实例保持原容量"

        # 批准前复核资源成本与节点容量，避免审批期间容量变化。
        cost = to_int(entry.get("资源成本"))
        node_capacity = to_int(job.get("节点容量"))
        if cost is None or node_capacity is None or cost > node_capacity:
            entry["审批状态"] = STATUS_BLOCKED
            entry["status"] = STATUS_BLOCKED
            entry["pending"] = False
            entry["审批意见"] = (
                f"审批时复核资源成本 {cost} 超过节点容量 {node_capacity}，建议不得进入审批"
            )
            entry["审批时间"] = now
            return None, entry["审批意见"]

        # 作业实例容量只能引用一套建议：已引用的旧建议未撤销前不得写入新建议。
        reference = str(job.get("并行度建议编号") or "")
        if reference and reference != entry.get("建议编号"):
            other = next(
                (row for row in store.rows(MODULE) if row.get("建议编号") == reference),
                None,
            )
            if other is not None and other.get("审批状态") == STATUS_APPROVED:
                return None, (
                    f"作业实例 {job.get('实例编号')} 的容量已引用建议 {reference}"
                    f"（数据版本 {other.get('数据版本')}），不能同时保留旧容量和新建议；"
                    "请先撤销旧建议再审批"
                )

        approved_value = to_int(entry.get("建议值"))
        if approved_value is None:
            return None, "并行度建议缺少可写入的建议值，审批结果无法落库"

        previous_capacity = to_int(job.get("并行度容量"))
        # 审批结果与作业实例容量在同一次处理里一起落库。
        entry["审批状态"] = STATUS_APPROVED
        entry["status"] = STATUS_APPROVED
        entry["pending"] = False
        entry["审批人"] = reviewer
        entry["审批意见"] = comment
        entry["审批时间"] = now
        entry["生效并行度"] = approved_value
        entry["原并行度"] = previous_capacity
        job["并行度容量"] = approved_value
        job["并行度建议编号"] = entry.get("建议编号")
        job["建议数据版本"] = int(entry.get("数据版本") or 0)
        job["容量更新时间"] = now
        job["pending"] = False
        job["abnormal"] = False

        # 同一算子的其他待审批建议随本次生效一并失效，保证只有一套有效建议。
        for row in self._active_suggestions(str(entry.get("流算子名") or "")):
            if row is entry or row.get("审批状态") != STATUS_PENDING:
                continue
            row["审批状态"] = STATUS_SUPERSEDED
            row["status"] = STATUS_SUPERSEDED
            row["pending"] = False
            row["失效原因"] = f"建议 {entry.get('建议编号')} 已审批生效，仅保留一套有效建议"
            row["失效时间"] = now

        return entry, (
            f"并行度建议已批准，作业实例 {job.get('实例编号')} 容量由 "
            f"{previous_capacity} 调整为 {approved_value}，建议记录与容量已一起落库"
        )

    def revoke(
        self,
        entry_id: int,
        reviewer: str,
        comment: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """撤销已生效建议：作业实例回到「无有效建议」状态，保留当前已验证容量。

        撤销后同一算子才可以提交新的并行度建议，避免旧容量和新建议同时存在。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"并行度建议 {entry_id} 不存在或已失效"
        if entry.get("审批状态") != STATUS_APPROVED:
            return None, (
                f"并行度建议 {entry.get('建议编号')} 当前审批状态为「{entry.get('审批状态')}」，"
                "只有已批准的建议可以撤销"
            )
        if not reviewer:
            return None, "缺少操作人，撤销结果无法落库"

        now = _now()
        entry["审批状态"] = STATUS_SUPERSEDED
        entry["status"] = STATUS_SUPERSEDED
        entry["pending"] = False
        entry["撤销人"] = reviewer
        entry["撤销意见"] = comment
        entry["撤销时间"] = now
        entry["失效原因"] = "建议已被撤销，作业实例保留撤销前的已验证容量"
        entry["失效时间"] = now

        job = store.find(JOBOPS_MODULE, int(entry.get("作业实例ID") or 0))
        if job is not None and str(job.get("并行度建议编号") or "") == str(entry.get("建议编号")):
            job["并行度建议编号"] = ""
            job["建议数据版本"] = 0
            job["容量更新时间"] = now
            return entry, (
                f"并行度建议 {entry.get('建议编号')} 已撤销，作业实例 {job.get('实例编号')} "
                f"保留当前容量 {job.get('并行度容量')}，可重新提交新建议"
            )
        return entry, f"并行度建议 {entry.get('建议编号')} 已撤销"

    # ---------- 内部工具 ----------
    def _active_suggestions(self, operator: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(MODULE)
            if row.get("流算子名") == operator and row.get("审批状态") in ACTIVE_STATUSES
        ]

    def _next_code(self, rows: list[dict[str, Any]]) -> str:
        existing = {str(row.get("建议编号") or "") for row in rows}
        index = 1
        while f"SUGG-{index:04d}" in existing:
            index += 1
        return f"SUGG-{index:04d}"

    def _find_backpress(self, identifier: Any) -> dict[str, Any] | None:
        if identifier is None or str(identifier).strip() == "":
            return None
        text = str(identifier).strip()
        for row in store.rows(BACKPRESS_MODULE):
            if str(row.get("指标编号")) == text:
                return row
        if text.isdigit():
            return store.find(BACKPRESS_MODULE, int(text))
        return None

    def _find_parallel(self, identifier: Any, operator: str) -> dict[str, Any] | None:
        if identifier is not None and str(identifier).strip():
            text = str(identifier).strip()
            for row in store.rows(PARALLEL_MODULE):
                if str(row.get("记录编号")) == text:
                    return row
            if text.isdigit():
                found = store.find(PARALLEL_MODULE, int(text))
                if found is not None:
                    return found
        if not operator:
            return None
        return next(
            (row for row in store.rows(PARALLEL_MODULE) if str(row.get("流算子名")) == operator),
            None,
        )

    def _find_job(self, values: dict[str, Any], operator: str) -> dict[str, Any] | None:
        job_id = values.get("作业实例ID") or values.get("job_id")
        if job_id is not None and str(job_id).strip():
            text = str(job_id).strip()
            for row in store.rows(JOBOPS_MODULE):
                if str(row.get("实例编号")) == text:
                    return row
            if text.isdigit():
                return store.find(JOBOPS_MODULE, int(text))
        if not operator:
            return None
        return next(
            (
                row for row in store.rows(JOBOPS_MODULE)
                if str(row.get("关联流算子名") or "") == operator
            ),
            None,
        )
