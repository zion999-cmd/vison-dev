# Development Workflow

## 规范 (Mandatory for every change)

### 1. 代码审查 (Code Review)

每次提交前必须通过代码审查。使用 `code-review` skill：

```
/code-review
```

审查角度：
- 行级 diff 扫描（条件反转、空指针、边界错误、遗漏异常处理）
- 删除行为审计（被删代码的约束是否在新代码中重建）
- 跨文件追踪（调用方是否适配新接口、并行修改是否冲突）

### 2. 测试 (Tests)

```bash
pytest -q  # 必须 100% 通过
```

测试要求：
- 覆盖新模块的所有公开 API
- 覆盖边界条件（空输入、过期数据、并发访问）
- 覆盖状态转换（创建→更新→删除）

### 3. 文档 (Documentation)

更新以下文件：

| 文件 | 内容 |
|------|------|
| `vision-dev-doc.md` | 架构文档、Phase 状态 |
| `README.md` | 项目概览、运行方式、已完成系统 |
| `DEVELOPMENT.md` | 本文件 — 开发规范 |

### 4. 提交 (Commit)

```
<type>: <description>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

Types: feat, fix, refactor, docs, test, chore

## 禁止事项

- ❌ 不先跑测试就提交
- ❌ 跳过代码审查
- ❌ 新模块没有单元测试
- ❌ 新功能不更新文档
