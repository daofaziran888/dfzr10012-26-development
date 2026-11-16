"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS

MODULE_LABELS = {
    "streamin": "流数据源接入",
    "operator": "流处理算子",
    "window": "窗口计算配置",
    "state": "状态后端管理",
    "checkpoint": "检查点恢复",
    "backpress": "反压监控面板",
    "latency": "延迟追踪管理",
    "jobops": "流作业运维",
    "sink": "流数据输出端",
    "schema": "流数据格式定义",
    "watermark": "水位线管理",
    "parallel": "并行度调优",
    "parallel_suggest": "并行度建议与资源审批",
    "metric": "流作业指标采集",
    "alarm": "流告警规则",
    "udf": "自定义函数库",
    "log": "流处理日志中心",
}


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "label": MODULE_LABELS.get(name, name),
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
