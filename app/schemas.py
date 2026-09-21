"""Pydantic models for the intelligent exam-paper composition API."""
from __future__ import annotations

from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

QuestionId = Union[int, str]


class QuestionIn(BaseModel):
    """题库快照中的一道题。"""

    id: QuestionId
    score: int = Field(..., description="题目分值（正整数）")
    type: str = Field(..., description="题型，如 single_choice / fill_blank / essay")
    difficulty: float = Field(..., ge=0.0, le=1.0, description="难度系数，区间 [0,1]")
    knowledge_points: List[str] = Field(default_factory=list, description="该题命中的知识点")

    @field_validator("score")
    @classmethod
    def _score_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("score 必须为正整数")
        return v

    @field_validator("type")
    @classmethod
    def _type_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("type 不能为空")
        return v.strip()


class ExamTaskIn(BaseModel):
    """组卷请求：题库快照 + 全部约束。"""

    name: Optional[str] = Field(None, description="任务名称，便于教研组分发管理")
    questions: List[QuestionIn] = Field(..., min_length=1, description="题库快照（提交后冻结）")
    total_score: int = Field(..., description="试卷总分（精确等于）")
    type_counts: Dict[str, int] = Field(
        default_factory=dict, description="各题型的精确数量，如 {'single_choice': 10, 'essay': 3}"
    )
    knowledge_coverage: Dict[str, int] = Field(
        default_factory=dict, description="知识点覆盖要求：知识点 -> 试卷中至少出现的题目数"
    )
    difficulty_range: List[float] = Field(
        ..., min_length=2, max_length=2, description="试卷平均难度的闭区间 [min, max]，取值 [0,1]"
    )
    target_difficulty: Optional[float] = Field(
        None, description="期望平均难度，用于计算难度偏差；缺省时取难度区间中点"
    )
    mutex_groups: List[List[QuestionId]] = Field(
        default_factory=list, description="互斥题组：每组至多选取 1 题"
    )
    paper_count: Optional[int] = Field(
        None,
        ge=1,
        le=5,
        description="平行卷份数：2~5 一次生成多份平行卷；缺省（或 1）为单卷，响应结构保持兼容",
    )
    max_shared_questions: Optional[int] = Field(
        None,
        ge=0,
        description="任意两卷共同题数上限，仅 paper_count>=2 时生效；缺省为 0（平行卷互不重复）",
    )
    seed: int = Field(42, description="随机种子，保证同请求结果可复现")

    @field_validator("total_score")
    @classmethod
    def _total_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("total_score 必须为正整数")
        return v

    @model_validator(mode="after")
    def _validate(self) -> "ExamTaskIn":
        # 难度区间合法性
        lo, hi = self.difficulty_range
        if not (0.0 <= lo <= hi <= 1.0):
            raise ValueError("difficulty_range 需满足 0 <= min <= max <= 1")
        if self.target_difficulty is not None and not (0.0 <= self.target_difficulty <= 1.0):
            raise ValueError("target_difficulty 必须在 [0,1] 内")

        # 题目编号唯一
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("questions 中存在重复的题目编号 id")
        id_set = set(ids)

        # 题型数量必须为正整数
        for t, c in self.type_counts.items():
            if c <= 0:
                raise ValueError(f"题型 {t} 的数量必须为正整数")

        # 覆盖要求非负
        for kp, c in self.knowledge_coverage.items():
            if c <= 0:
                raise ValueError(f"知识点 {kp} 的覆盖数量必须为正整数")

        # 互斥题组：成员必须存在，至少 2 题才有约束意义
        for gi, group in enumerate(self.mutex_groups):
            if len(group) != len(set(group)):
                raise ValueError(f"第 {gi} 个互斥题组内存在重复题目编号")
            missing = [qid for qid in group if qid not in id_set]
            if missing:
                raise ValueError(f"第 {gi} 个互斥题组引用了题库中不存在的题目: {missing}")

        # max_shared_questions 仅在多卷请求下有意义
        if self.max_shared_questions is not None and (self.paper_count or 1) < 2:
            raise ValueError("max_shared_questions 仅在 paper_count 为 2~5 时可设置")
        return self


class QuestionOut(BaseModel):
    id: QuestionId
    score: int
    type: str
    difficulty: float
    knowledge_points: List[str]


class ConflictConstraint(BaseModel):
    kind: str = Field(..., description="约束类别")
    scope: Optional[str] = Field(
        None, description="per_paper（单卷约束，detail.paper 为卷序号）/ cross_paper（跨卷重叠上限）"
    )
    detail: dict = Field(..., description="约束内容（原始请求中的表达形式）")


class PaperResult(BaseModel):
    """单份卷的组卷结果（多卷响应中的 papers 元素）。"""

    paper: List[QuestionOut]
    selected_ids: List[QuestionId]
    total_score: int
    average_difficulty: float
    difficulty_deviation: float
    type_counts: Dict[str, int]
    knowledge_point_counts: Dict[str, int]
    repeated_knowledge_points: int


class SharedQuestionPair(BaseModel):
    papers: List[int] = Field(..., description="两份卷的序号（从 0 起）")
    count: int = Field(..., description="两卷共同题数")
    question_ids: List[QuestionId] = Field(..., description="共同题编号（按题库顺序）")


class TaskResult(BaseModel):
    status: str = Field(..., description="feasible / infeasible / error")

    # —— 单卷响应（未传 paper_count 时结构保持兼容）——
    paper: Optional[List[QuestionOut]] = None
    selected_ids: Optional[List[QuestionId]] = None
    total_score: Optional[int] = None
    average_difficulty: Optional[float] = None
    difficulty_deviation: Optional[float] = None
    type_counts: Optional[Dict[str, int]] = None
    knowledge_point_counts: Optional[Dict[str, int]] = None
    repeated_knowledge_points: Optional[int] = None

    # —— 多卷响应（paper_count 为 2~5）——
    paper_count: Optional[int] = None
    max_shared_questions: Optional[int] = None
    papers: Optional[List[PaperResult]] = None
    total_difficulty_deviation: Optional[float] = None
    total_shared_questions: Optional[int] = None
    shared_question_pairs: Optional[List[SharedQuestionPair]] = None

    seed: Optional[int] = None
    conflict_constraints: Optional[List[ConflictConstraint]] = None
    message: Optional[str] = None
    error: Optional[str] = None


class TaskCreated(BaseModel):
    task_id: int
    status: str


class TaskStatus(BaseModel):
    task_id: int
    status: str
    name: Optional[str] = None
    seed: int
    created_at: str
    result: Optional[TaskResult] = None
