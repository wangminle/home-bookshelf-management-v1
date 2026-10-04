# 自备识书测试集

把你自己的封面图和人工标注放在这里，用来检验多模态模型能不能读出书名和作者。

这个目录和仓库自带的合成集 `tests/eval/covers/` + `tests/eval/golden.json` 分开。`eval_cover_recognition.py generate --force` 只覆盖合成集，不会动这里。

## 现在里面有什么

六张程序绘制的模拟封面（虚构书名，不是扫描件）和对应标注（v2 格式），用来看格式：

| 文件 | 难度 | 书名 | 作者 | 特点 |
|---|---|---|---|---|
| `covers/sim_01_normal.png` | 清晰横排 | 星港手记 | 陈予安 | |
| `covers/sim_02_vertical.png` | 竖排 | 南风辞 | 林晚 | |
| `covers/sim_03_blurry.png` | 轻度模糊 | 纸上星图 | 周衡 | |
| `covers/sim_04_art_font.png` | 艺术字 | 雾中灯塔 | 陈予安、林晚 | 多作者样例 |
| `covers/sim_05_angle_isbn.png` | 倾斜 | 拾光的人 | 周衡 | 封面印有 ISBN（9787553820217） |
| `covers/sim_06_normal_g2.png` | 清晰横排 | 雾中灯塔 | 陈予安、林晚 | 与 sim_04 **同一本书**（同组 `g-sim-004`） |

标注在 `golden.json`。`sim_` 开头的图片会进 git；其余图片默认不进 git。

## 放入你的测试数据

1. 封面图放进 `covers/`。用 jpg 或 png。文件名不要以 `sim_` 开头。
2. 在 `golden.json` 里为每张图写一条标注。可以删掉模拟样例，也可以留着一起评。
3. 书名、作者按封面所见填写。画面上看不到 ISBN 就写 `null`，不要编造。
4. `difficulty` 用：`normal`、`art_font`、`vertical`、`foreign`、`blurry`、`angle`。拿不准写 `normal`。

一条 v2 标注长这样（后四个字段可省略，默认值见说明）：

```json
{
  "id": "user-001",
  "file": "我的封面.jpg",
  "task": "cover_title_author",
  "difficulty": "normal",
  "group": "user-001",
  "verified": false,
  "isbn_visible": false,
  "authors_on_cover": true,
  "expected": {"title": "书名", "author": "作者", "isbn": null}
}
```

`file` 必须和 `covers/` 里的文件名一致。多作者用顿号分隔。

### v2 字段说明（BI-08）

| 字段 | 默认 | 语义 |
|---|---|---|
| `group` | 等于 `id` | **同一本书**的照片分组：同一本书拍了多张（封面/封底/不同角度）必须写同一个 group。切分校准/验证集按组分，同书照片不得跨集泄漏。注意：内容哈希相同只证明照片相同，不能凭相似封面判定两本书相同 |
| `verified` | `false` | 该标注是否**人工逐项核对**过。模型识别结果只能作标注草稿；`--yes` 自动门控只认 `verified` 样本（≥20 本不同书） |
| `isbn_visible` | `false` | 封面是否实际印有 ISBN/条码——区分"模型漏读 ISBN"与"封面本来没有" |
| `authors_on_cover` | `true` | 封面是否署作者——区分"封面未署作者"与"模型漏作者" |

真实金标准的建议流程：批量入库/识别后，把用户核对过的结论转写进来，逐张对照封面把 `verified` 置 `true`（核对耗时如实记录，用于阶段结论）；未核对的条目保持 `false`，不会被门控采信。

## 跑评测（BI-09 运行器，读后端 Owner 模型配置）

```bash
backend/.venv/bin/python scripts/run_cover_model_eval.py \
    --golden tests/eval/user-set/golden.json \
    --database-url sqlite:///backend/data/bookshelf.db \
    --data-dir backend/data
```

运行器只读后端 `llm_settings`（API Key 不导出、不写报告）、逐条落检查点、按图片哈希+模型指纹+提示词版本缓存（重跑不重复付费）、不修改图书库。结果写 `results-<时间戳>.json`，含身份三元组（模型指纹/提示词版本/任务类型）、测试集指纹、逐条 usage 与耗时、费用按"未知"如实标记。

## 打分（Agent 手工预测路径，模型看图之后）

识图提示词仍用 `tests/eval/vision_prompt.md`。预测结果写到本目录的 `predictions.json` 后：

```bash
python3 scripts/eval_cover_recognition.py compare \
  --golden tests/eval/user-set/golden.json \
  --predictions tests/eval/user-set/predictions.json \
  --eval-dir tests/eval/user-set
```

合格线与合成集相同：书名 ≥ 90%，作者 ≥ 80%，书级完全正确率 ≥ 75%（`--yes` 门控读这个）。
