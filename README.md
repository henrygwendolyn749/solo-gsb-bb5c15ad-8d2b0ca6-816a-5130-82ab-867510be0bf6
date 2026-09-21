# 智能组卷 API

面向教研组的约束组卷服务：提交**题库快照**与**总分、题型数量、知识点覆盖、难度区间、互斥题组**等约束，自动生成分值精确、约束满足、结果可复现的试卷；多组可行解按 **难度偏差 → 重复知识点数 → 题目编号序列** 依次择优；无解时返回经逐项删减验证的**最小冲突约束集**。

支持一次请求生成 **2～5 份平行卷**（`paper_count`）：所有卷在同一个 CP-SAT 联合模型中分别满足全部单卷约束，并限制任意两卷共同题数不超过 `max_shared_questions`；多卷可行解按 **各卷难度偏差总和 → 跨卷重复题总数 → 各卷题号序列** 依次择优，无解时冲突集区分**单卷约束**（`per_paper`）与**跨卷重叠上限**（`cross_paper`）。不传 `paper_count` 时为单卷，请求与响应结构完全保持兼容。

技术栈：Python · FastAPI · SQLite · OR-Tools CP-SAT · Docker / Compose。

## 启动

```bash
docker compose up --build
```

容器启动时自动安装依赖并创建 SQLite 数据库，无需任何手动初始化。

- API 地址：http://localhost:8000
- 端口：`8000`（宿主机 `8000` → 容器 `8000`，可在 `docker-compose.yml` 的 `ports` 修改）
- 必要配置（环境变量，均有默认值，一般无需修改）：
  - `EXAM_DB_PATH`：SQLite 数据库文件路径，默认 `/data/exam.db`（已挂载为持久卷 `exam_data`）
- 交互式文档（Swagger）：http://localhost:8000/docs

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/tasks` | 创建组卷任务（异步），返回 `task_id` |
| `GET`  | `/api/tasks/{task_id}` | 查询任务状态与结果 |
| `GET`  | `/health` | 健康检查 |

任务状态：`pending` → `running` → `done`；结果在 `result` 字段中返回（`feasible` / `infeasible` / `error`）。

## 请求示例

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "高三数学月考",
    "questions": [
      {"id": 1, "score": 5,  "type": "single_choice", "difficulty": 0.2, "knowledge_points": ["集合"]},
      {"id": 2, "score": 5,  "type": "single_choice", "difficulty": 0.5, "knowledge_points": ["函数"]},
      {"id": 3, "score": 5,  "type": "single_choice", "difficulty": 0.8, "knowledge_points": ["导数"]},
      {"id": 4, "score": 12, "type": "essay",         "difficulty": 0.6, "knowledge_points": ["函数", "导数"]},
      {"id": 5, "score": 13, "type": "essay",         "difficulty": 0.7, "knowledge_points": ["数列"]}
    ],
    "total_score": 20,
    "type_counts": {"single_choice": 2, "essay": 1},
    "knowledge_coverage": {"函数": 1},
    "difficulty_range": [0.3, 0.7],
    "target_difficulty": 0.5,
    "mutex_groups": [[2, 4]],
    "seed": 42
  }'
```

字段说明：

- `questions`：题库快照，提交后冻结。每题含 `id`（数字或字符串，唯一）、`score`（正整数）、`type`、`difficulty`（0~1）、`knowledge_points`。
- `total_score`：试卷总分，**精确等于**。
- `type_counts`：各题型的精确数量。
- `knowledge_coverage`：知识点 → 试卷中至少出现的题目数。
- `difficulty_range`：试卷平均难度（按分值加权）闭区间 `[min, max]`。
- `target_difficulty`：期望平均难度，缺省取难度区间中点；难度偏差为 `|平均难度 - 目标难度|`。
- `mutex_groups`：互斥题组，每组至多选取 1 题。
- `paper_count`：平行卷份数，取 `2`～`5` 时一次生成多份卷；缺省（或 `1`）为单卷，响应结构与旧版一致。
- `max_shared_questions`：任意两卷**共同题数上限**，仅多卷时可设置；缺省 `0`（平行卷之间不重复）。
- `seed`：随机种子（默认 42），相同请求 + 相同种子结果可复现。

## 多卷请求示例（2～5 份平行卷）

在单卷请求体上增加 `paper_count` 与 `max_shared_questions` 即可。各卷在**同一个联合 CP-SAT 模型**中分别满足总分、题型、知识点覆盖、难度区间、互斥题组约束，且任意两卷共同题数不超过上限：

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "高三数学月考·A/B/C 平行卷",
    "questions": [
      {"id": 1,  "score": 5,  "type": "single_choice", "difficulty": 0.3, "knowledge_points": ["集合"]}
      // ……题库快照（题量需足够分给各卷，允许部分题目各卷都不选）
    ],
    "total_score": 150,
    "type_counts": {"single_choice": 8, "fill_blank": 4, "essay": 4},
    "knowledge_coverage": {"函数": 2, "导数": 1},
    "difficulty_range": [0.45, 0.6],
    "target_difficulty": 0.52,
    "mutex_groups": [[12, 13]],
    "paper_count": 3,
    "max_shared_questions": 1,
    "seed": 42
  }'
```

择优顺序（逐层最优后再优化下一层，保证确定性、与卷的排列无关）：

1. **各卷难度偏差总和**最小；
2. **跨卷重复题总数**（所有卷对共同题数求和）最少；
3. **各卷题号序列**按卷序依次最小（同时唯一确定卷的排列）。

### 多卷可行结果

结果在 `papers` 数组中逐卷返回（每卷字段与单卷结果一致），并附跨卷统计：

```json
{
  "status": "feasible",
  "paper_count": 3,
  "max_shared_questions": 1,
  "papers": [
    {
      "selected_ids": [1, 4, 9, "/* … */"],
      "total_score": 150,
      "average_difficulty": 0.52,
      "difficulty_deviation": 0.0,
      "type_counts": {"single_choice": 8, "fill_blank": 4, "essay": 4},
      "knowledge_point_counts": {"函数": 3, "导数": 2}
    }
  ],
  "total_difficulty_deviation": 0.04,
  "total_shared_questions": 1,
  "shared_question_pairs": [
    {"papers": [0, 1], "count": 1, "question_ids": [9]},
    {"papers": [0, 2], "count": 0, "question_ids": []},
    {"papers": [1, 2], "count": 0, "question_ids": []}
  ],
  "seed": 42
}
```

- `papers[i].paper`：第 `i` 卷的完整题目对象（卷序号从 0 起）。
- `total_difficulty_deviation`：各卷 `difficulty_deviation` 之和（第 1 择优目标）。
- `total_shared_questions`：所有卷对共同题数之和（第 2 择优目标），`shared_question_pairs` 给出每对卷的共同题号，每对的 `count` 均不超过 `max_shared_questions`。

### 多卷无解结果（单卷约束 vs 跨卷上限）

多卷无解时，`conflict_constraints` 用 `scope` 区分两类来源：

- `per_paper`：某一份卷自身的约束，`detail.paper` 标明卷序号（从 0 起）；
- `cross_paper`：跨卷共同题上限（`kind` 为 `shared_questions`，`detail.papers` 标明卷对）。

```json
{
  "status": "infeasible",
  "message": "不存在满足全部约束的平行卷组",
  "conflict_constraints": [
    {"kind": "type", "scope": "per_paper",
     "detail": {"paper": 1, "type": "essay", "count": 2}},
    {"kind": "shared_questions", "scope": "cross_paper",
     "detail": {"papers": [1, 2], "max_shared_questions": 0}}
  ]
}
```

上例表示：第 2 卷（`paper: 1`）的题型数量约束与卷 2/卷 3 的零重叠上限相互冲突（题库中大题不足）；放宽其中任一条（例如允许两卷共用 1 题，或调大题量）后即可组卷。

## 可行结果

```json
{
  "status": "feasible",
  "selected_ids": [1, 2, 4],
  "total_score": 20,
  "average_difficulty": 0.48,
  "difficulty_deviation": 0.02,
  "type_counts": {"single_choice": 2, "essay": 1},
  "knowledge_point_counts": {"集合": 1, "函数": 2, "导数": 1},
  "repeated_knowledge_points": 1,
  "paper": [ /* 完整题目对象 */ ]
}
```

## 无解结果（最小冲突约束集）

当约束无解时，`status` 为 `infeasible`，`conflict_constraints` 给出经**逐项删减验证**的删除极小冲突集——即其中任何一条被放宽/删除后，其余约束即可满足：

```json
{
  "status": "infeasible",
  "message": "不存在满足全部约束的试卷",
  "conflict_constraints": [
    {"kind": "total_score",      "detail": {"total_score": 20}},
    {"kind": "difficulty_range", "detail": {"range": [0.9, 1.0]}}
  ]
}
```

单卷冲突项的 `kind` 取值：`total_score` / `type` / `coverage` / `difficulty_range` / `mutex`；多卷时另有跨卷项 `shared_questions`（`scope: "cross_paper"`），单卷项带 `scope: "per_paper"`。
