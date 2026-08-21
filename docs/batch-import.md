# 批量导入图书封面并建档

把一批封面图片批量导入系统：**Agent 视觉识别书名/作者 → 用户核对清单 → 脚本批量入库 → 汇总报告**。
方案全文见 `design/plans/批量导入图书封面并建档方案.md`。

```
封面目录（如 ./covers）
  → python3 scripts/batch_import_covers.py scan --dir ./covers   生成 batch_manifest.json
  → Agent 逐张看图填 title/author（status: pending → recognized）
  → 用户核对清单（recognized → confirmed / skip）
  → python3 scripts/batch_import_covers.py run                   逐本调 POST /books/intake
  → batch_report.json（已入库 / 已存在 / 失败及原因）
```

后端零改动：元数据补全、封面落盘、查重去重全部由既有 `POST /books/intake` 完成
（`backend/app/services/intake.py`），入库可安全重跑（重复书返回 `already_exists`）。

## 前置条件

1. **后端在跑**：`http://127.0.0.1:8000`（可用 `bookshelf health` 验证）；不在本机时 `export BOOKSHELF_API_URL=http://<服务器>`
2. **Agent Token**：在前端「Agent 授权」页（`/agent-authorization`）注册 client → 建 `books:write` 授权 → 签发 token，
   然后 `export BOOKSHELF_TOKEN=hbs_at_...`（写接口必需，见 `docs/agent-setup.md`）
3. **图片**：放进一个目录。支持 jpg / jpeg / png / webp / bmp / heic / tif / tiff；建议 jpg/png。
   文件名随意（Agent 靠看图识别，不依赖文件名）；若恰好在文件名里放了 ISBN，可在清单里手工填 `isbn` 字段。

## 第一步：scan 生成清单

```bash
python3 scripts/batch_import_covers.py scan --dir ./covers
```

生成 `batch_manifest.json`（`--out` 可改路径）。重复执行会**合并**：已有条目的
识别结果和状态原样保留，只追加新图片；目录里已删除的图片条目保留并提示。

## 第二步：Agent 识别（对 Agent 的指引）

Agent 会话中，识别这样执行：

1. 读清单，取出全部 `status: pending` 的条目；
2. 逐张用视觉读取封面图（`covers/` 下的文件），提取 **title / author**（封面正面通常没有
   条码，ISBN 一般读不到，留空即可；竖排、艺术字看不清时如实留空并在 `note` 写明原因）；
3. 填入清单并把对应条目 `status` 改为 `recognized`；
4. 向用户汇报识别清单（书名 | 作者 | 置信度/备注），等待核对。

依据 `skills/book-intake/SKILL.md` 的约束：**识别结果存疑时先确认再入库**——
拿不准的条目保持 `recognized` 并在汇报中明示，不要替用户确认。

## 第三步：用户核对

打开 `batch_manifest.json`（或按 Agent 汇报逐条答复）：

- 识别正确 → `status: "confirmed"`
- 这本不想导 → `status: "skip"`
- 识别有误 → 直接改 `title`/`author` 后置 `confirmed`；也可顺手补 `isbn`/`price`/`location` 等字段

状态机全表（谁可以改、什么时候改）：

| status | 含义 | 下一步 |
|---|---|---|
| `pending` | 扫描到，未识别 | Agent 识别后 → `recognized` |
| `recognized` | Agent 已识别，待核对 | 用户核对 → `confirmed` / `skip` |
| `confirmed` | 已核对，待入库 | `run` 后 → `imported` / `failed` |
| `skip` | 跳过不导 | 终态 |
| `imported` | 已成功入库（`result` 里有 book_id） | 终态，重跑自动跳过 |
| `failed` | 上次入库失败（`result.error` 有原因） | 修正后改回 `confirmed`，或 `run --retry-failed` |

## 第四步：run 入库

```bash
# 先演练：只校验并展示将提交的条目，不调 API、不改清单
python3 scripts/batch_import_covers.py run --dry-run

# 正式入库
python3 scripts/batch_import_covers.py run
python3 scripts/batch_import_covers.py run --location "客厅书架A" --channel 当当   # 批量默认值
```

- 只处理 `confirmed` 条目；`--price/--channel/--location/--member-id` 是批量默认值，条目自身字段优先；
- 每本的结果实时打印，结束后清单回写（`imported`/`failed` + `book_id`/`error`），
  报告写 `batch_report.json`：`created`（新入库）/ `exists`（书架已有，跳过建档）/ `failed`（原因）；
- 中断重跑安全：`imported` 不重复提交，API 层还有二次查重兜底。

### `--yes` 自动模式与 75% 门控

`run --yes` 把 `recognized` 条目当作已确认直接入库，**前提是最近一次 eval 的
书级完全正确率 ≥ 75%**（`tests/eval/results-*.json`），否则拒绝执行。这是把
「识别存疑先确认」变成基于指标的自动开关：

```bash
python3 scripts/batch_import_covers.py run --yes            # 未达标时会被拒绝
python3 scripts/batch_import_covers.py run --yes --force    # 人工越过门控（慎用）
```

首批导入务必走人工核对——核对结果顺手就是 eval 金标准（见下节），一次劳动两个用途。

## eval：识别质量评估（tests/eval/）

详细规范见 `tests/eval/README.md`。核心流程：

Agent 编排时加载 `skills/cover-eval/SKILL.md`（触发语：评测视觉模型 / 封面 eval）。

```bash
# 0. （可选）生成/刷新仓库内合成测试集
python3 scripts/eval_cover_recognition.py generate --force
# 1. 首批核对完成后，也可把真实封面图放进 tests/eval/covers/，
#    核对结论（用户修正后的书名/作者）写进 tests/eval/golden.json
# 2. 生成待填骨架，Agent 按 tests/eval/vision_prompt.md 逐张识别填 predicted 字段
python3 scripts/eval_cover_recognition.py template
# 3. 对比打分：总体 + 分档指标 + miss 清单，写 tests/eval/results-{时间}.json
python3 scripts/eval_cover_recognition.py compare
```

指标与合格线：

| 指标 | 合格线 | 说明 |
|---|---|---|
| 书名准确率 | ≥ 90% | 无 ISBN 时的主匹配键 |
| 作者准确率 | ≥ 80% | 消歧用；多作者任一命中即算对 |
| 书级完全正确率 | ≥ 75% | 书名对、（有期望作者时）作者对、（有期望 ISBN 时）ISBN 也对；**`--yes` 门控读这个** |
| ISBN 识别率 | 不强制 | 正面封面通常无条码，能读到才评 |

**何时重跑 eval**：换 vision 模型、改识别 prompt、模型版本升级、每批大批量入库前。

## 常见问题

- **run 报 401/403**：`BOOKSHELF_TOKEN` 未设置或过期，去前端「Agent 授权」页（`/agent-authorization`）重新签发。
- **后端不可用**：`run` 启动时会先做健康检查并直接退出，不会入库一半。
- **某本识别不出来**：留空 + `note` 注明，人工补书名/ISBN 后再 `confirmed`；
  有 ISBN 时（如封底照）后端会自动拉元数据与封面。
- **同一本书导了两次**：不会重复建档，第二次返回 `already_exists`（报告记 `exists`）。
- **不想用命令行**：把封面目录告诉 Agent（Claude Code 会话），说「按 docs/batch-import.md 批量导入」即可，识别与执行由 Agent 完成。
