---
name: cover-eval
description: 家庭书架封面识书评测技能。当用户说「评测视觉模型」「测多模态能不能识书」「跑封面 eval」「这个模型达标吗」「compare golden」「vision 准确率」时使用。指导 Agent 看测试集封面填 predictions，再用脚本打分，不入库。
scopes: []
version: "0.2.6"
---

# 封面识书评测（cover-eval）

评测**多模态/视觉模型**能否达到本项目入库所需的书名/作者识别质量。

命名与 `book-intake`、`book-query` 同构：`{领域}-{动作}`，短横线、无产品前缀。不要使用 `home-book-shelf-multimodal-eval` 这类长名。

本技能**不调用** `bookshelf` CLI，也**不入库**。打分走仓库脚本；看图由当前 Agent 的视觉能力完成。

## 适用场景

- 「评测一下这个视觉模型能不能识书封」
- 「跑 tests/eval / 封面金标准 / cover eval」
- 「换模型后准不准、能不能 `--yes` 自动入库」
- 「书名准确率 / 书级完全正确率 / auto_import_allowed」

用户要**把书加入书架**时不要用本技能，转 **book-intake**。

## 前置

1. 工作目录是本仓库根（存在 `scripts/eval_cover_recognition.py`、`scripts/run_cover_model_eval.py` 与 `tests/eval/`）。
2. 本机有 Python 3 与 Pillow（生成合成封面时需要）。
3. 当前会话能读本地图片（视觉模型）。不必连后端，不必 `BOOKSHELF_TOKEN`。

## 合格线（方案 §4）

| 指标 | 合格线 |
|------|--------|
| 书名准确率 | ≥ 90% |
| 作者准确率 | ≥ 80% |
| 书级完全正确率 | ≥ 75%（`batch_import_covers.py run --yes` 门控读 `metrics.book_level_accuracy_verified`，仅已核验样本口径） |

ISBN 只在画面上有数字时才评，不强制。

## 执行步骤

始终在**仓库根**执行。

### 1. 确认测试集

合成集：`tests/eval/golden.json` 与 `tests/eval/covers/` 应有条目。若合成金标准为空或需要刷新合成集：

```bash
python3 scripts/eval_cover_recognition.py generate --force
```

真实家藏书封：放入 `tests/eval/user-set/covers/`，把核对后的书名/作者写入 `tests/eval/user-set/golden.json`（`--yes` 门控默认金标准即此文件）。该目录与合成集分开，`--force` 不会覆盖它；有真实标注时不要乱刷新合成集。

### 2. 生成预测骨架

```bash
python3 scripts/eval_cover_recognition.py template
```

得到 `tests/eval/predictions.json`。

### 3. 看图填写（Agent / 视觉模型，不走脚本）

按 `tests/eval/vision_prompt.md`：

1. **逐张读取**封面图（合成集 `tests/eval/covers/`，真实集 `tests/eval/user-set/covers/`）下 `golden.json` 列出的文件，**不要用文件名猜书名**。
2. 只根据画面文字填写对应条目的 `predicted.title` / `predicted.author` / `predicted.isbn`。
3. 看不清则填 `null`，不要编造 ISBN。
4. 顶层填写 `model`（如 `claude-sonnet-4.6`）和 `generated_at`（ISO 时间）。

可一次读多张，但每张都必须单独对着图填，禁止批量用文件名或金标准抄答案。

### 4. 打分

```bash
python3 scripts/eval_cover_recognition.py compare
```

结果写入 `tests/eval/results-{时间}.json`，并打印总体指标、分档、miss 清单、是否 `允许 --yes 自动入库`。

> 这是**手工预测路径**，适合快速评估 Agent 自身视觉能力。注意：`batch_import_covers.py run --yes` 的门控**只认** `scripts/run_cover_model_eval.py`（自动评测运行器）产出的报告——手工 `compare` 报告缺 verified 口径与 identity，不会被门控接受。要给批量导入开 `--yes`，必须用运行器出报告（见下节）。

## 回复规范

向用户报告：

> 模型：{model}  
> 样本：{n}  
> 书名 {title%} · 作者 {author%} · 书级 {book_level%}  
> 结论：{达标 / 未达标}（书级门槛 75%）  
> 主要失分档：{art_font / vertical / …}  
> miss：列出错的书名（期望 → 预测）

未达标时：说明批量导入须逐本确认；可建议改 prompt 或换模型后重跑本技能。

要给 `batch_import_covers.py run --yes` 放行，最近一次 eval 报告必须**同时**满足（BI-10 门控）：

1. `metrics.book_level_accuracy_verified`（仅已核验样本口径）≥ 0.75；
2. 报告携带完整 identity（任务类型 / 提示词版本 / 模型配置指纹 / 测试集指纹），运行时以 `--gate-task-type / --gate-prompt-version / --gate-model-fingerprint / --gate-golden-sha` 逐一比对，**缺一即拒**；
3. 报告携带 progress 完成度（完成度不足即拒）。

门控报告由自动评测运行器产出（读后端 Owner 模型配置，含缓存与检查点）：

```bash
backend/.venv/bin/python scripts/run_cover_model_eval.py \
    --golden tests/eval/user-set/golden.json \
    --database-url sqlite:///backend/data/bookshelf.db \
    --data-dir backend/data            # 可选：--out / --limit / --no-cache / --only-verified
```

换模型 / 提示词 / 任务类型 / 测试集后旧证据不通用，必须重跑运行器再申请 `--yes`。

## 异常处理

| 情况 | 处理 |
|------|------|
| 金标准为空 | `generate` 刷新合成集，或请用户把真实封面放入 `tests/eval/user-set/covers/` 并标注 |
| 金标准已存在且 `generate` 拒绝覆盖 | 不要加 `--force`，除非用户确认可丢弃现有标注 |
| `compare` 报 file 未匹配 | 检查 `predictions.json` 的 `file` 是否与 `golden.json` 一致 |
| 无法读图 | 说明当前 Agent 无视觉或路径不对，不要瞎填 |
| 用户把评测当成入库 | 转 **book-intake**，评测结果不能代替 `bookshelf add` |

## 禁止事项

- 不要跳过看图、不要把 `expected` 抄进 `predicted`
- 不要为了达标改 `golden.json`
- 不要在本技能里执行 `bookshelf add` 或 `batch_import_covers.py run`
- 不要把评测图当作业务附件上传到后端
