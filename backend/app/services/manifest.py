"""单证处理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from typing import Any

from app.store import store

MODULE = "manifest"
REQUIRED_FIELDS = ["单证编号", "单证类型", "关联航次"]
STATUS_ORDER = ["待提交", "已提交", "已审核", "已退回"]
ACTION_RULES = {"提交单证": "已提交", "审核通过": "已审核", "退回单证": "已退回"}
NEGATIVE_ACTIONS = []

# 列表页与导出共用的列口径，保证“导出内容与页面看到的一致”。
PAGE_COLUMNS = ["单证编号", "单证类型", "关联航次", "申报箱量", "申报人", "提交时间", "审核人员", "单证状态"]
# 模板可填写的列（单证状态由系统流转，不开放导入）。
TEMPLATE_COLUMNS = ["单证编号", "单证类型", "关联航次", "申报箱量", "申报人", "提交时间", "审核人员"]
ALLOWED_TYPES = ["进口舱单", "出口舱单", "装箱单", "提单", "卸货报告", "理货证明"]
EMPTY_MARK = "—"
BOM = "\ufeff"


def _text(value: Any) -> str:
    return str(value or "").strip()


class ManifestService:
    def __init__(self) -> None:
        # 已导入文件的指纹 -> 文件名；与 store 一样随进程存续，重启后随示例数据一起重置。
        self._imported_files: dict[str, str] = {}

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
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        entry = self._build_entry(values, self._next_id())
        store.rows(MODULE).append(entry)
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
        entry["单证状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"单证已{action}"

    # ---------- 批量导入 ----------

    def template_csv(self) -> str:
        """导入模板：只有表头，列与页面一致（去掉系统维护的单证状态）。"""
        return BOM + ",".join(TEMPLATE_COLUMNS) + "\r\n"

    def import_entries(self, *, filename: str, content: str) -> dict[str, Any]:
        """按模板整批导入：逐条核对，合格的落库，不合格的只报错；同一文件不重复落库。"""
        fingerprint = hashlib.sha256(content.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        if fingerprint in self._imported_files:
            first = self._imported_files[fingerprint]
            return {
                "ok": False,
                "message": f"文件「{filename}」与已导入的「{first}」内容相同，本次新增 0 条，未重复落库",
                "created": 0,
                "failed": 0,
                "duplicated_file": True,
                "errors": [],
            }

        rows, header_error = self._parse_template(content)
        if header_error:
            return {
                "ok": False,
                "message": header_error,
                "created": 0,
                "failed": 0,
                "duplicated_file": False,
                "errors": [],
            }
        if not rows:
            return {
                "ok": False,
                "message": f"文件「{filename}」里没有可导入的单证行",
                "created": 0,
                "failed": 0,
                "duplicated_file": False,
                "errors": [],
            }

        existing_numbers = {_text(row.get("单证编号")) for row in store.rows(MODULE)}
        voyage_numbers = {_text(row.get("航次编号")) for row in store.rows("voyage")}
        seen_in_file: set[str] = set()
        errors: list[dict[str, Any]] = []
        created: list[dict[str, Any]] = []
        next_id = self._next_id()

        for line, values in rows:
            number = _text(values.get("单证编号"))
            problems: list[str] = []
            missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
            if missing:
                problems.append(f"缺少必填字段：{'、'.join(missing)}")
            if number:
                if number in existing_numbers:
                    problems.append("单证编号与已有单证重复")
                elif number in seen_in_file:
                    problems.append("单证编号在文件内重复")
            doc_type = _text(values.get("单证类型"))
            if doc_type and doc_type not in ALLOWED_TYPES:
                problems.append(f"单证类型「{doc_type}」不在允许范围（{'、'.join(ALLOWED_TYPES)}）")
            voyage = _text(values.get("关联航次"))
            if voyage and voyage not in voyage_numbers:
                problems.append(f"关联航次「{voyage}」不在册")
            if problems:
                errors.append({"line": line, "key": number or None, "reason": "；".join(problems)})
                continue
            seen_in_file.add(number)
            created.append(self._build_entry(values, next_id))
            next_id += 1

        store.rows(MODULE).extend(created)
        if created:
            self._imported_files[fingerprint] = filename
        message = f"导入完成：新增 {len(created)} 条"
        if errors:
            message += f"，{len(errors)} 条不合格未落库"
        return {
            "ok": not errors,
            "message": message,
            "created": len(created),
            "failed": len(errors),
            "duplicated_file": False,
            "errors": errors,
        }

    def _parse_template(self, content: str) -> tuple[list[tuple[int, dict[str, Any]]], str | None]:
        """解析模板 CSV：按表头取列，返回 (行号, 字段值) 列表；表头缺列时给出错误说明。"""
        text = content.lstrip(BOM).strip()
        if not text:
            return [], "导入文件为空，请按模板填写后再上传"
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            return [], "导入文件为空，请按模板填写后再上传"
        header = [_text(cell) for cell in header]
        missing_columns = [field for field in REQUIRED_FIELDS if field not in header]
        if missing_columns:
            return [], f"模板表头缺少列：{'、'.join(missing_columns)}，请使用系统提供的导入模板"
        rows: list[tuple[int, dict[str, Any]]] = []
        for line, raw in enumerate(reader, start=2):
            if not any(_text(cell) for cell in raw):
                continue
            values = {column: _text(raw[header.index(column)]) if header.index(column) < len(raw) else "" for column in TEMPLATE_COLUMNS if column in header}
            rows.append((line, values))
        return rows, None

    def _next_id(self) -> int:
        return max((int(row.get("id", 0)) for row in store.rows(MODULE)), default=0) + 1

    def _build_entry(self, values: dict[str, Any], entry_id: int) -> dict[str, Any]:
        entry = {"id": entry_id}
        for column in TEMPLATE_COLUMNS:
            entry[column] = _text(values.get(column))
        entry["单证状态"] = STATUS_ORDER[0]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        return entry

    # ---------- 按航次打包导出 ----------

    def export_package(self, entry_ids: list[int]) -> tuple[bytes | None, str]:
        """把选中的单证按航次分文件打包成 ZIP；每个 CSV 的列与页面一致。"""
        selected = [entry for entry_id in entry_ids if (entry := store.find(MODULE, entry_id)) is not None]
        if not selected:
            return None, "选中的单证不存在或已归档，请刷新列表后重试"
        by_voyage: dict[str, list[dict[str, Any]]] = {}
        for entry in selected:
            by_voyage.setdefault(_text(entry.get("关联航次")) or "未指定航次", []).append(entry)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
            for voyage in sorted(by_voyage):
                package.writestr(f"单证_{voyage}.csv", self._page_csv(by_voyage[voyage]))
        return buffer.getvalue(), f"已按 {len(by_voyage)} 个航次打包 {len(selected)} 条单证"

    def _page_csv(self, rows: list[dict[str, Any]]) -> str:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(PAGE_COLUMNS)
        for row in sorted(rows, key=lambda item: _text(item.get("单证编号"))):
            writer.writerow([_text(row.get(column)) or EMPTY_MARK for column in PAGE_COLUMNS])
        return BOM + output.getvalue()
