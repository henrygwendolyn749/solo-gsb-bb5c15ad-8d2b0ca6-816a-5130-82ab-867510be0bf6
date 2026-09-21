"""FastAPI application: task creation and result query."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import asynccontextmanager

from . import database
from .schemas import ExamTaskIn, TaskCreated, TaskResult, TaskStatus
from .worker import submit


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(
    title="智能组卷 API",
    description="面向教研组的题库快照约束组卷服务（FastAPI + SQLite + OR-Tools CP-SAT），支持 2~5 份平行卷联合组卷",
    version="1.1.0",
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
