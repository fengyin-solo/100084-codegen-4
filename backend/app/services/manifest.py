"""单证处理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "manifest"
REQUIRED_FIELDS = ["单证编号", "单证类型", "关联航次"]
STATUS_ORDER = ["待提交", "已提交", "已审核", "已退回"]
ACTION_RULES = {"提交单证": "已提交", "审核通过": "已审核", "退回单证": "已退回"}
NEGATIVE_ACTIONS = []

# 批量导入模板列：列顺序即模板列顺序，调整时前端模板要同步。
IMPORT_FIELDS = ["单证编号", "单证类型", "关联航次", "申报箱量", "申报人", "提交时间", "审核人员"]
ALLOWED_TYPES = ["进口舱单", "出口舱单", "装箱单", "提单", "危险品申报单"]
# 导出列与页面表格保持一致，保证所见即所导。
EXPORT_FIELDS = IMPORT_FIELDS + ["单证状态"]


class ManifestService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("单证编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"单证 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于单证处理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"单证已{action}"

    def import_entries(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """整批导入：逐条核对编号重复、类型允许、航次在册；合格的落库，不合格的只回报原因。

        已落库的编号会并入查重集合，所以同一份文件再次导入时全部判重，不会产生第二批单证。
        """
        existing_codes = {str(row.get("单证编号") or "").strip() for row in store.rows(MODULE)}
        known_voyages = {str(row.get("航次编号") or "").strip() for row in store.rows("voyage")}
        created: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for line, values in enumerate(rows, start=1):
            code = str(values.get("单证编号") or "").strip()
            reason = self._validate_import(values, existing_codes, known_voyages)
            if reason:
                failed.append({"line": line, "code": code or "—", "reason": reason})
                continue
            entry = self._new_entry({field: str(values.get(field) or "").strip() for field in IMPORT_FIELDS})
            store.rows(MODULE).append(entry)
            existing_codes.add(code)
            created.append(entry)
        return created, failed

    def _validate_import(
        self,
        values: dict[str, Any],
        existing_codes: set[str],
        known_voyages: set[str],
    ) -> str | None:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return f"缺少必填字段：{'、'.join(missing)}"
        code = str(values.get("单证编号") or "").strip()
        if code in existing_codes:
            return f"单证编号 {code} 已存在，重复导入不会生成新单证"
        doc_type = str(values.get("单证类型") or "").strip()
        if doc_type not in ALLOWED_TYPES:
            return f"单证类型「{doc_type}」不在允许范围（{'、'.join(ALLOWED_TYPES)}）"
        voyage = str(values.get("关联航次") or "").strip()
        if voyage not in known_voyages:
            return f"关联航次 {voyage} 不在航次名册里"
        return None

    def _new_entry(self, fields: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update(fields)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        return entry

    def export_entries(self, *, ids: list[int] | None = None, voyage: str | None = None) -> dict[str, Any]:
        """按页面列口径导出；传了选中 id 或航次时只导出该范围，并按航次分组打包。"""
        rows = store.rows(MODULE)
        if ids is not None:
            wanted = set(ids)
            rows = [row for row in rows if int(row.get("id", 0)) in wanted]
        if voyage:
            rows = [row for row in rows if str(row.get("关联航次") or "").strip() == voyage]
        items = [
            {field: (row.get(field) if row.get(field) is not None else "—") for field in EXPORT_FIELDS}
            for row in rows
        ]
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            groups.setdefault(str(item["关联航次"]), []).append(item)
        voyages = [{"关联航次": name, "条数": len(group), "items": group} for name, group in groups.items()]
        return {"module": MODULE, "fields": EXPORT_FIELDS, "total": len(items), "items": items, "voyages": voyages}
