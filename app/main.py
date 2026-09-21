"""FastAPI application: task creation, targeted revision and result query."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import asynccontextmanager

from . import database
from .schemas import (
    ExamTaskIn,
    ReviseTaskIn,
    TaskCreated,
    TaskResult,
    TaskStatus,
)
from .worker import submit


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(
    title="智能组卷 API",
    description="面向教研组的题库快照约束组卷服务（FastAPI + SQLite + OR-Tools CP-SAT），支持 2~5 份平行卷联合组卷",
    version="1.2.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/tasks", response_model=TaskCreated, status_code=201)
def create_task(payload: ExamTaskIn) -> TaskCreated:
    """提交组卷任务：题库快照 + 总分/题型/知识点/难度/互斥题组约束。"""
    req = payload.model_dump()
    task_id = database.create_task(
        payload.name,
        json.dumps(req, ensure_ascii=False),
        payload.seed,
    )
    submit(task_id)
    return TaskCreated(task_id=task_id, status="pending")


@app.post("/api/tasks/{task_id}/revise", response_model=TaskCreated, status_code=201)
def revise_task(task_id: int, payload: ReviseTaskIn) -> TaskCreated:
    """基于已完成且可行的源任务提交定向改卷：按卷指定必须保留 / 移除的题号。

    源请求与编辑条件在新任务中冻结，联合重求解全部卷；源任务保持不变。
    """
    row = database.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="源任务不存在")
    if row["status"] != "done":
        raise HTTPException(status_code=409, detail="仅已完成的任务可以改卷")

    source_result = json.loads(row["result"]) if row["result"] else None
    if not source_result or source_result.get("status") != "feasible":
        raise HTTPException(status_code=409, detail="仅可行（feasible）的任务可以改卷")

    source_req: Dict[str, Any] = json.loads(row["request"])
    paper_count = int(source_req.get("paper_count") or 1)

    def unprocessable(msg: str) -> HTTPException:
        return HTTPException(status_code=422, detail=msg)

    # 卷序必须存在
    bad_papers = [p for p in payload.edits if p < 0 or p >= paper_count]
    if bad_papers:
        raise unprocessable(f"卷序不存在（合法范围 0~{paper_count - 1}）: {bad_papers}")

    id_set = {q["id"] for q in source_req["questions"]}
    for p, edit in payload.edits.items():
        retain = list(dict.fromkeys(edit.retain_ids))  # 去重保序
        remove = list(dict.fromkeys(edit.remove_ids))

        # 保留与移除不能重叠
        overlap = [qid for qid in retain if qid in set(remove)]
        if overlap:
            raise unprocessable(f"第 {p} 卷 retain_ids 与 remove_ids 存在重叠题号: {overlap}")

        # 题号必须存在于冻结的题库快照
        missing = [qid for qid in retain + remove if qid not in id_set]
        if missing:
            raise unprocessable(f"第 {p} 卷引用了题库中不存在的题号: {missing}")

    # 冻结源任务各卷选题，供求解器做“最大化保留原题”与 retained/added/removed 对照
    if paper_count == 1:
        source_selected = {"0": source_result.get("selected_ids", [])}
    else:
        source_selected = {
            str(p): paper.get("selected_ids", [])
            for p, paper in enumerate(source_result.get("papers", []))
        }

    frozen_req = json.loads(json.dumps(source_req))  # 深拷贝：冻结源请求，求解过程不影响源任务记录
    frozen_req["revision"] = {
        "source_task_id": task_id,
        "edits": {
            str(p): {
                "retain_ids": list(dict.fromkeys(edit.retain_ids)),
                "remove_ids": list(dict.fromkeys(edit.remove_ids)),
            }
            for p, edit in payload.edits.items()
        },
        "source_selected": source_selected,
    }

    name = payload.name
    if name is None:
        name = f"{row['name'] or '未命名任务'}·改卷"

    new_task_id = database.create_task(
        name,
        json.dumps(frozen_req, ensure_ascii=False),
        int(source_req.get("seed", 42)),
    )
    submit(new_task_id)
    return TaskCreated(task_id=new_task_id, status="pending")


@app.get("/api/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: int) -> TaskStatus:
    """查询任务状态与组卷结果（可行卷或最小冲突约束集）。"""
    row = database.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    result: Optional[TaskResult] = None
    if row["result"]:
        result = TaskResult(**json.loads(row["result"]))

    return TaskStatus(
        task_id=row["id"],
        status=row["status"],
        name=row["name"],
        seed=row["seed"],
        created_at=row["created_at"],
        result=result,
    )
