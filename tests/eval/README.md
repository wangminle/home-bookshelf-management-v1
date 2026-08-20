# tests/eval — 多模态识书测试集

用来回答：**某个视觉大模型能不能达到本项目入库所需的书名/作者识别质量。**

方案见 `design/plans/批量导入图书封面并建档方案.md` §4，操作手册见 `docs/batch-import.md`。

仓库默认带一套 **合成封面**（程序绘制，不含扫描件版权问题）。真实家藏书封可另放到 `covers/` 并改 `golden.json`。

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
    "expected": {"title": "三体", "author": "刘慈欣", "isbn": "9787536692930"}
  }
]
```

- `author` / `isbn` 拿不准可填 `null`（isbn 仅画面可见时标注）。
- 多作者用顿号等分隔；打分时 **任一作者名命中** 即算对；西文姓氏单独写出也算对。

## 使用

Agent 侧走 `skills/cover-eval`（「评测视觉模型」「封面 eval」）。脚本仍在仓库根执行：

```bash
# （可选）重新生成合成测试集，会覆盖 golden.json 与合成封面
python3 scripts/eval_cover_recognition.py generate --force

# 生成 predictions.json 骨架
python3 scripts/eval_cover_recognition.py template

# 按 vision_prompt.md 逐张识图，填 predicted 与 model
python3 scripts/eval_cover_recognition.py compare
```

合格线：书名 ≥ 90%，作者 ≥ 80%，书级完全正确率 ≥ 75%。
书级 ≥ 75% 时 `batch_import_covers.py run --yes` 的自动入库门控才会放行。

## 如何判定一个模型「满足要求」

1. 对该模型跑完整 12 条（或你的真实金标准）。
2. `compare` 输出 `auto_import_allowed: true`。
3. 看分档：若 `art_font` / `vertical` 明显低于总体，说明该模型不能在对应拍照条件下免人工确认。
