"""单证处理接口：维护单证，覆盖提交单证、审核通过、退回单证等动作，支持整批导入与按航次打包导出。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, ImportPayload, ImportResult, PageResult
from app.services.manifest import ManifestService

router = APIRouter(prefix="/api/manifest", tags=["单证处理"])

service = ManifestService()

LIST_FIELDS = ["单证编号", "单证类型", "关联航次", "申报箱量", "申报人", "提交时间", "审核人员", "单证状态"]
STATUSES = ["待提交", "已提交", "已审核", "已退回"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按单证编号检索"),
    status: str | None = Query(default=None, description="待提交、已提交、已审核、已退回"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按单证编号与状态过滤单证处理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/import", response_model=ImportResult)
def import_entries(payload: ImportPayload) -> ImportResult:
    """按模板整批导入单证：逐条核对编号、类型、航次；不合格的只报错不落库，结束后汇报新增条数。"""
    if not payload.rows:
        return ImportResult(ok=False, message="导入文件里没有可处理的条目", created=0, failed=[])
    created, failed = service.import_entries(payload.rows)
    message = f"导入完成：新增 {len(created)} 条"
    if failed:
        message += f"，{len(failed)} 条不合格未入库"
    return ImportResult(ok=True, message=message, created=len(created), failed=failed)


@router.get("/export")
def export_entries(
    ids: str | None = Query(default=None, description="逗号分隔的单证 id，只导出选中的单证"),
    voyage: str | None = Query(default=None, description="只导出该航次下的单证"),
) -> dict[str, Any]:
    """导出单证处理清单：列口径与页面一致；指定选中 id 或航次时按航次分组打包。"""
    id_list: list[int] | None = None
    if ids:
        try:
            id_list = [int(part) for part in ids.split(",") if part.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="ids 参数应为逗号分隔的数字")
    return service.export_entries(ids=id_list, voyage=voyage)


# 注意：/{entry_id} 这类带路径参数的路由要放在具体路径（/import、/export）之后，
# 否则 /export 会先被当成 entry_id 匹配，int 校验失败直接 422，具体路径永远到不了。
@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条单证明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"单证 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条单证，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="单证已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条单证执行提交单证、审核通过、退回单证；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
