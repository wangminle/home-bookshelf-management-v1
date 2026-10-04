# tests/eval — 多模态识书测试集

用来回答：**某个视觉大模型能不能达到本项目入库所需的书名/作者识别质量。**

已实施方案见 `design/achievements/批量导入图书封面并建档方案.md` §4，操作手册见 `docs/batch-import.md`。

仓库默认带一套 **合成封面**（程序绘制，不含扫描件版权问题）。

你自己的封面和标注请放到 [`user-set/`](user-set/README.md)（`tests/eval/user-set/`）。该目录与合成集分开，`generate --force` 不会覆盖它。目录里已有 3 张虚构书名的模拟封面，可按 `golden.json` 的格式替换成你的图片和标注。真实封面默认不进 git。

## 目录内容

| 路径 | 说明 |
|---|---|
| `covers/` | 测试封面图（默认合成 12 张，覆盖全部难度档） |
| `golden.json` | 金标准：`id / file / task / difficulty / expected{title, author, isbn}` |
| `vision_prompt.md` | 评测时给模型的识图提示词 |
| `predictions.json` | 模型输出（`template` 生成骨架后填写，不入库 git） |
| `results-*.json` | `compare` 产出的评估结果（不入库 git） |

## 任务定义

当前任务类型只有 `cover_title_author`：看单张封面，抽出书名、作者、可选 ISBN。

这对应入库主键：ISBN > 书名 + 作者。没有 ISBN 时，书名错就会建脏数据。

## 难度分档

| 档位 | 特征 | 预期 |
|---|---|---|
| `normal` | 清晰中文横排 | 主要得分来源 |
| `art_font` | 艺术字/错落旋转 | 最大失分点 |
| `vertical` | 竖排书名 | 中等失分 |
| `foreign` | 外文书名作者 | 中等失分 |
| `blurry` | 高斯模糊 | 真实拍照失分 |
| `angle` | 倾斜 | 真实拍照失分 |

## golden.json 条目格式

```json
[
  {
    "id": "vlm-book-001",
    "file": "normal_01.png",
    "task": "cover_title_author",
    "difficulty": "normal",
    "group": "vlm-book-001",
    "verified": true,
    "isbn_visible": true,
    "authors_on_cover": true,
    "expected": {"title": "三体", "author": "刘慈欣", "isbn": "9787536692930"}
  }
]
```

- `author` / `isbn` 拿不准可填 `null`（isbn 仅画面可见时标注）。
- 多作者用顿号等分隔；打分时 **任一作者名命中** 即算对（兼容口径）；西文姓氏单独写出也算对。

### v2 增量字段（BI-08，`user-set` 已启用；合成集沿用 v1 字段亦可）

| 字段 | 默认 | 语义 |
|---|---|---|
| `group` | 等于 `id` | 同一本书的照片分组；校准/验证集按组切分，同书照片不得跨集泄漏 |
| `verified` | `false` | 人工逐项核对该标注。**`--yes` 自动门控只采信 verified 样本（≥20 本不同书）**；识别结果只能作标注草稿 |
| `isbn_visible` | `false` | 封面是否印有 ISBN——区分"模型漏读"与"封面本来没有"（ISBN 召回的分母） |
| `authors_on_cover` | `true` | 封面是否署作者——区分"封面未署作者"与"模型漏作者"（多作者完整性的适用条件） |

相关工具：`scripts/eval_golden.py`（校验/统计/按组切分）、`tests/scripts/test_eval_golden.py`。

## 使用

两条评测路径（BI-09 后推荐 A）：

**A. 后端模型直评（读 Owner 配置的模型，含 usage/缓存/检查点）**——`scripts/run_cover_model_eval.py`：

```bash
backend/.venv/bin/python scripts/run_cover_model_eval.py \
    --golden tests/eval/user-set/golden.json \
    --database-url sqlite:///backend/data/bookshelf.db \
    --data-dir backend/data
```

- API Key 由服务内部读取，不导出、不写报告；评测不修改图书库；
- 按图片哈希 + 模型配置指纹 + 提示词版本缓存，同配置重跑不重复付费；
- 报告含身份三元组（模型指纹/提示词版本/任务类型）、测试集指纹、人工核验样本数、
  逐条 usage 与耗时；费用未知时如实标 null（不填 0）。

**B. Agent 手工预测路径**——`skills/cover-eval`（「评测视觉模型」「封面 eval」）：

```bash
# （可选）重新生成合成测试集，会覆盖 golden.json 与合成封面
python3 scripts/eval_cover_recognition.py generate --force

# 生成 predictions.json 骨架
python3 scripts/eval_cover_recognition.py template

# 按 vision_prompt.md 逐张识图，填 predicted 与 model
python3 scripts/eval_cover_recognition.py compare
```

## 合格线与门控

合格线：书名 ≥ 90%，作者 ≥ 80%，书级完全正确率 ≥ 75%。

`batch_import_covers.py run --yes` 的自动入库门控（BI-10 增强）在准确率之外还要求：

1. eval 结果携带 `identity`（任务类型 = `cover_title_author`、提示词版本、模型配置指纹、
   测试集指纹）——换模型/提示词/任务类型后旧证据不通用；
2. `sample.verified_groups ≥ 20`（人工核验的不同书数；71 张照片不是 71 个独立书目样本）；
3. 模型重配后可用 `--gate-model-fingerprint`/`--gate-prompt-version` 显式比对。

## 如何判定一个模型「满足要求」

1. 对该模型跑完整 12 条（或你的真实金标准，需 ≥20 本不同书且人工核验）。
2. `compare`/运行器输出 `book_level_accuracy ≥ 0.75` 且门控证据核验通过。
3. 看分档：若 `art_font` / `vertical` 明显低于总体，说明该模型不能在对应拍照条件下免人工确认。
4. 补充口径（运行器已产出）：多作者完整性、ISBN 准确率（幻觉度量）与
   ISBN 召回（仅封面印有 ISBN 的条目）；任一作者命中口径不得用于宣称多作者全部正确。
