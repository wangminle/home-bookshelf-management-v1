# 批量导入图书封面并建档

把一批封面图片批量导入系统，有两条路径：

- **Web 工作台（推荐，PLN-012 M3）**：前端「批量入库」页（`/batch-intake`，Owner 专属）——
  上传多图 →（模型识别/手工填写）→ 候选核对（编辑/拆分/照片关联）→ 匹配已有书预览 →
  确认选中 → 执行 → 回执/重试。全部状态（照片、候选、确认、回执）持久化在服务端，
  **刷新/重启后重新打开页面即可恢复**；重复执行不重复建书；确认字段不被外部元数据覆盖。
  书目/封面数据库修改与命令回执同事务提交；提交前再次检查确认快照、有效 Owner 与租约代次，
  检查失败一起回滚。过期恢复同样检查参数摘要和 ISBN 归属冲突。
  详见 `design/checkpoints/PLN-012-M3-批量核对工作台-阶段记录-20261003.md`。
- **Agent + CLI 脚本路径（既有）**：Agent 视觉识别书名/作者 → 用户核对清单 → 脚本批量入库 → 汇总报告，
  适合交给 Agent 会话自动化执行。已实施方案全文见 `design/achievements/批量导入图书封面并建档方案.md`；
  可靠性增强契约（manifest v2、警告/错误码、字段策略、报告口径）见
  `design/plans/拍照入库契约基线v2-BI01-20261002.md`。

```
封面目录（如 ./covers）
  → python3 scripts/batch_import_covers.py scan --dir ./covers   生成 batch_manifest.json（v2）
  → Agent 逐张看图填 title/author（status: pending → recognized）
  → 用户核对清单（recognized → confirmed / skip）
  → python3 scripts/batch_import_covers.py run                   逐本调 POST /books/intake
  → batch_report-<run时间戳>.json（入库/已存在/失败/结果未知、警告与错误码）
  → python3 scripts/batch_import_covers.py reconcile             只读核对最终关联（悬空 ID 处理）
```

CLI 路径（元数据补全、封面落盘、查重去重）由既有 `POST /books/intake` 完成。可靠性增强（2026-10，
PLN-012 M1）之后：条码依赖故障**可降级**（有书名即入库并带警告）、已确认条目字段**不被外部元数据覆盖**、
逐条落盘**中断可恢复**、写请求回执丢失标记**结果未知**不自动重试。

## 前置条件

1. **后端在跑**：`http://127.0.0.1:8000`（可用 `bookshelf health` 验证）；不在本机时 `export BOOKSHELF_API_URL=http://<服务器>`
2. **Agent Token**：在前端「Agent 授权」页（`/agent-authorization`）注册 client → 建 `books:write` 授权 → 签发 token，
   然后 `export BOOKSHELF_TOKEN=hbs_at_...`（写接口必需，见 `docs/agent-setup.md`；reconcile 只需读权限）
3. **图片**：放进一个目录。支持 jpg / jpeg / png / webp / bmp / heic / tif / tiff；建议 jpg/png。
   文件名随意（Agent 靠看图识别，不依赖文件名）；若恰好在文件名里放了 ISBN，可在清单里手工填 `isbn` 字段。
4. **（可选）条码解码依赖**：macOS `brew install zbar`。缺 zbar 时批量流程**仍可用**——
   有书名/作者的条目降级入库并带 `barcode_dependency_unavailable` 警告；纯图片且无手工 ISBN 的
   条目会被拒绝（需要补充信息）。`run` 启动预检会读取 `/health` 的 `barcode_scan_available`
   提前提示（无 `members:read` 权限时显示"能力未知"，由后端最终判断）。

### macOS zbar 加载排障（TST-016 实录）

Homebrew 安装 zbar 后，Python `import pyzbar.pyzbar` 仍可能因 dyld 找不到 `libzbar.dylib` 失败
（Python 子进程会剥离 `DYLD_LIBRARY_PATH` 等变量）。可选的环境处置：

```bash
brew install zbar
mkdir -p ~/lib && ln -sf $(brew --prefix zbar)/lib/libzbar.dylib ~/lib/libzbar.dylib
```

`~/lib` 是 dyld 的默认回退路径，无需环境变量。**这属于用户自行决定的环境处置**——业务代码不会
自动改写用户目录；是否在代码里探测 `/opt/homebrew/lib` 属后续优化（OPT-014），当前以文档指引为准。
用 `bookshelf doctor` 验证：条码识别应显示"可用"。

## 第一步：scan 生成清单

```bash
python3 scripts/batch_import_covers.py scan --dir ./covers
```

生成 `batch_manifest.json`（`--out` 可改路径）。重复执行会**合并**：已有条目的
识别结果和状态原样保留，只追加新图片；目录里已删除的图片条目保留并提示。

### 清单 v2 字段与 v1 兼容

scan 生成的清单为 **version 2**。相对旧版，每个条目新增：

| 字段 | 说明 |
|---|---|
| `photo_id` | 稳定照片 ID（`p0001` 递增，scan 分配，合并沿用）。**照片不是图书计数单位** |
| `sha256` | 照片内容哈希，scan 时固化。只证明照片相同，不能据此判定两张照片是同一本书 |
| `role` | 图片角色：`cover`（默认）/ `barcode` / `other` / `unknown` |
| `confirmed_fields` | 用户核对过的字段名列表（run 时作为 `prefer_confirmed` 输入）；旧 confirmed 条目缺失时，在请求前推断并持久保存，失败/未知后的重试仍保留确认资格 |
| `warnings` | 条目级警告（`{code, message, …}`） |
| `candidate_id` | 候选书目预留（M3 工作台使用） |

兼容规则：

- **v1 清单仍可直接使用**：读取时自动在内存升级（补缺省字段、分配 photo_id），下次保存写为 v2；
  首次升级保存前，原 v1 文件自动备份为 `<清单名>.v1.bak`（不覆盖已有备份），历史 `result` 原样保留。
- 工具遇到**高于自身支持的清单版本**会明确报错并**拒绝写回**，不会静默丢弃未知字段。

## 第二步：Agent 识别（对 Agent 的指引）

Agent 会话中，识别这样执行：

1. 读清单，取出全部 `status: pending` 的条目；
2. 逐张用视觉读取封面图（`covers/` 下的文件），提取 **title / author**（封面正面通常没有
   条码，ISBN 一般读不到，留空即可；竖排、艺术字看不清时如实留空并在 `note` 写明原因）；
3. 填入清单并把对应条目 `status` 改为 `recognized`；条码照片可在 `role` 标 `barcode`；
4. 向用户汇报识别清单（书名 | 作者 | 置信度/备注），等待核对。

依据 `skills/book-intake/SKILL.md` 的约束：**识别结果存疑时先确认再入库**——
拿不准的条目保持 `recognized` 并在汇报中明示，不要替用户确认。

## 第三步：用户核对

打开 `batch_manifest.json`（或按 Agent 汇报逐条答复）：

- 识别正确 → `status: "confirmed"`
- 这本不想导 → `status: "skip"`
- 识别有误 → 直接改 `title`/`author` 后置 `confirmed`；也可顺手补 `isbn`/`price`/`location` 等字段；
  核对过哪些字段可记入 `confirmed_fields`（如 `["title", "authors"]`），run 时这些字段
  将以 `prefer_confirmed` 策略提交——**外部元数据不得覆盖你核对过的值**，不一致时后端保留
  确认值并在响应 `warnings` 里给出 `metadata_field_conflict`（含双方值与来源）。
  **清空字段的入口区别**：Web 工作台和 JSON 入库 API 支持确认 `authors: []`
  （确认无作者，不从元数据回填）及 `subtitle: ""`（清空副题）。JSON API 的
  `confirmed_fields: []` 表示「无确认字段」，省略则按已提供字段推断。
  批量脚本当前不传递副题，空作者数组会回退到 `author`；`confirmed` 清单条目的
  空确认列表也会按非空书名、作者、ISBN 推断。因此不要用上述清单空值表达清空，
  需要清空时使用 Web 工作台或 JSON 入库 API。

状态机全表（谁可以改、什么时候改）：

| status | 含义 | 下一步 |
|---|---|---|
| `pending` | 扫描到，未识别 | Agent 识别后 → `recognized` |
| `recognized` | Agent 已识别，待核对 | 用户核对 → `confirmed` / `skip` |
| `confirmed` | 已核对，待入库 | `run` 后 → `imported` / `failed` / `outcome_unknown` |
| `skip` | 跳过不导 | 终态 |
| `imported` | 已成功入库（`result` 里有 book_id 与 run_id） | 终态，重跑自动跳过 |
| `failed` | 上次入库**确定**失败（`result.error_code` 有码） | 修正后改回 `confirmed`，或 `run --retry-failed` |
| `outcome_unknown` | 写请求的回执丢失/解码失败或 HTTP 5xx，**可能已入库** | 不得自动重试：先核对书目及副本/购买，确认未提交后 `run --retry-unknown` |

## 第四步：run 入库

```bash
# 先演练：只校验并展示将提交的条目，不调 API、不改清单
python3 scripts/batch_import_covers.py run --dry-run

# 正式入库
python3 scripts/batch_import_covers.py run
python3 scripts/batch_import_covers.py run --location "客厅书架A" --channel 当当   # 批量默认值
```

- 只处理 `confirmed` 条目；`--price/--channel/--location/--member-id` 是批量默认值，条目自身字段优先；
- `confirmed` 条目按 `prefer_confirmed` 策略提交（见上节）；`--yes` 的 `recognized` 条目未经
  人工核对，仍用默认策略（元数据可补全）；
- **逐条落盘**：每处理完一条，清单（原子替换）与报告快照立即写盘——进程中断/断网后，
  已处理条目的回执不丢，重跑从未处理条目继续；
- 每次运行有独立 `run_id`（写入报告与每个条目 `result.run_id`）；报告默认写
  `batch_report-<run时间戳>.json`，**重跑不覆写历史报告**（`--report` 可指定路径）；
- 报告 `summary` 口径：`photos_total`（批次照片总数，含跳过）/ `total`（本次提交数）/
  `created` / `exists` / `failed` / `outcome_unknown` / `skipped`；每行带 `error_code`
  （稳定错误码，与 `warnings[].code` 同一套）与后端 `warnings`。

### 结果未知（outcome_unknown）

写请求发生读超时、连接建立后的读写/协议错误、压缩回执损坏、成功回执无法解析，或返回 HTTP 5xx 时，
**请求可能已被服务端提交**。5xx 也可能来自网关，不能证明上游没有写入。
明确未建立连接的连接错误/连接超时，以及确定的 4xx 业务拒绝，仍进入普通失败。
处理约定：

1. 该条目标 `outcome_unknown`，`--retry-failed` **不会**自动重试它；
2. 先核对：`reconcile` 或按书名/ISBN 查询书目；若带价格/位置，还需核对购买记录和副本；
3. 确认写入没有提交后，`run --retry-unknown` 显式重发。CLI 的直接入库端点没有命令幂等键，
   仅书目去重不能保证副本/购买恰好一次；Web 工作台以命令回执与业务同事务保证已完成命令重放。

### `--yes` 自动模式与 75% 门控

`run --yes` 把 `recognized` 条目当作已确认直接入库，**前提是最近一次 eval 的
已核验书级完全正确率 ≥ 75%**（`tests/eval/results-*.json`）、至少 20 本不同已核验书、
非冒烟且完成全部计划样本，并且任务/提示词/模型/数据集四项身份与本次匹配，否则拒绝执行。这是把
「识别存疑先确认」变成基于指标的自动开关。注意门控只认可 `run_cover_model_eval.py`
产出的报告格式（含 `identity` + `progress` 块，见下节「eval」）；`eval_cover_recognition.py`
的离线评分输出不满足门控。

```bash
python3 scripts/batch_import_covers.py run --yes \
  --gate-task-type '<任务类型>' --gate-prompt-version '<提示词版本>' \
  --gate-model-fingerprint '<模型指纹>' --gate-golden-sha '<数据集hash>'
python3 scripts/batch_import_covers.py run --yes --force    # 人工越过门控（慎用）
```

首批导入务必走人工核对——核对结果顺手就是 eval 金标准（见下节），一次劳动两个用途。

## 第五步：reconcile 最终关联核对

入库后（尤其是经历过重跑、人工清理、删除合并的批次），核对"每张照片的最终去向"：

```bash
# 演练（默认只读）：逐条验证 book_id 是否存在、ISBN 是否冲突、封面引用是否有效
python3 scripts/batch_import_covers.py reconcile --manifest batch_manifest.json

# 悬空 ID 重定向：先核对目标 ISBN，通过才应用（如 TST-016 批次已核验的映射 10→18、11→30、12→56）
python3 scripts/batch_import_covers.py reconcile --apply \
    --map 10=18:9787553820217 --map 11=30:<目标ISBN> --map 12=56:<目标ISBN>
```

- **默认只读**：只调读接口（`GET /books/{id}`），不改任何数据；
- `--apply` 只修改**清单/报告**（最终关联写 `final_book_id`、保留原 `result.book_id` 并写
  `reconciliations` 追溯记录），**不写生产数据库**；
- 映射必须先核对目标存在且 ISBN 与核对值一致，否则拒绝重定向、条目保持待核对——
  **其他批次不得仅按书名自动猜测替代 ID**；已删除且无可靠目标的条目留在未解决清单；
  ISBN 比对前两侧统一换算为规范 ISBN-13：清单里的 ISBN-10 会与后端同一本书的
  ISBN-13 判为一致（如 `702000220X` ≡ `9787020002207`），不会误报不符；
- **退出码**：任一 `unresolved` 分桶非零（悬空 `missing_book` / 未知 `unknown` /
  ISBN 不符 `isbn_mismatch`）时 `reconcile` 退出 `1`；其中 `outcome_unknown` 且回执
  无 `book_id` 的条目（请求可能已被受理，无从核对关联）计入 `unresolved.unknown`，
  标 `pending_review` 保持待核对。`run` 的退出码同理：`failed` / `outcome_unknown`
  任一 > 0 即退出 `1`。
- 分口径汇总（`batch_reconcile-<时间戳>.json`）：
  - `photos_total` 照片总数；`photos_linked` 成功关联照片数；`distinct_book_ids` 不同有效书数；
  - `unresolved` 未解决关联（悬空/未知/ISBN 不符）；`net_created` **批次净新增**
    （独立查询原创建 ID，仅计本批创建且仍存活的书；最终关联改到既有书不会抹掉存活的原创建对象，
    原对象已删或状态未知不计；命中已有书不算新增）；
  - `corrections` 清理/修正（重映射条数、被拒映射），不归为新入库成功。

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

`eval_cover_recognition.py` 是**离线评分变体**：产出总体/分档指标供人工看质量，
输出没有 `identity`/`progress` 块，`--yes` 门控（`check_auto_confirm_gate`）**拒绝**
这种结果文件。

**`--yes` 门控认可的 eval 路径是 `scripts/run_cover_model_eval.py`**：它用「模型」页
配置的识图模型（llm_settings）对金标准逐张跑识别，产出含 `identity` + `progress` 块和
`metrics.book_level_accuracy_verified` 的报告，是唯一满足门控格式要求的评测。前置条件：
先在「模型」页（`/llm-settings`）启用并配好模型（或 `PUT /api/v1/settings/llm`），然后：

```bash
backend/.venv/bin/python scripts/run_cover_model_eval.py \
    --golden tests/eval/user-set/golden.json \
    --database-url sqlite:///backend/data/bookshelf.db \
    --data-dir backend/data
```

报告默认写 `<金标准目录>/results-<时间戳>.json`（`--out` 可指定路径），`--yes` 门控按其中
已核验书级完全正确率判读；同配置重跑命中缓存不再重复调用。

指标与合格线：

| 指标 | 合格线 | 说明 |
|---|---|---|
| 书名准确率 | ≥ 90% | 无 ISBN 时的主匹配键 |
| 作者准确率 | ≥ 80% | 消歧用；多作者任一命中即算对 |
| 书级完全正确率 | ≥ 75% | 书名对、（有期望作者时）作者对、（有期望 ISBN 时）ISBN 也对；**`--yes` 门控读这个** |
| ISBN 识别率 | 不强制 | 正面封面通常无条码，能读到才评 |

**何时重跑 eval**：换 vision 模型、改识别 prompt、模型版本升级、每批大批量入库前。

## 附：警告码 / 错误码速查

`warnings[].code`（非阻断，如实可见）：

| 码 | 含义 |
|---|---|
| `barcode_dependency_unavailable` | 后端缺 pyzbar/zbar；有书名时已降级入库 |
| `barcode_decode_timeout` | 条码解码超时（15s）已放弃 |
| `barcode_not_found` | 图片中未发现 ISBN 条码 |
| `barcode_invalid_checksum` | 解码结果校验位无效已丢弃 |
| `metadata_missing` | 外部元数据未命中（含外部依赖故障），按手工输入入库 |
| `metadata_field_conflict` | 外部元数据与确认值不一致，已保留确认值（detail 含双方值与来源） |
| `isbn_ownership_conflict` | ISBN 命中已有书但书名/作者明显冲突（阻断，转人工） |

`result.error_code`（失败/结果未知时）：`missing_input` / `file_missing` / `invalid_isbn` /
`image_corrupted` / `barcode_dependency_unavailable` / `isbn_ownership_conflict` / `bad_request` /
`conflict` / `unauthorized` / `forbidden` / `service_unavailable` / `timeout`（=结果未知）/
`receipt_lost`（=结果未知：连接建立后读写失败/协议错误/2xx 不可解析，须核对后决定是否重试）/
`network_error` / `unknown`。

## 常见问题

- **run 报 401/403**：`BOOKSHELF_TOKEN` 未设置或过期，去前端「Agent 授权」页（`/agent-authorization`）重新签发。
- **后端不可用**：`run` 启动时会先做健康检查并直接退出，不会入库一半。
- **某本识别不出来**：留空 + `note` 注明，人工补书名/ISBN 后再 `confirmed`；
  有 ISBN 时（如封底照）后端会自动拉元数据与封面。
- **同一本书导了两次**：不会重复建档，第二次返回 `already_exists`（报告记 `exists`）。
- **ISBN 归属冲突（400）**：该 ISBN 已绑定另一本明显不同的书——不要改 ISBN 硬绕，
  先在前端核对两本书目，必要时合并/删除走既有授权操作。
- **run 中断了怎么办**：直接重跑。已 `imported` 的自动跳过；`outcome_unknown` 的先核对
  （见上文），不要盲目 `--retry-failed`。
- **外部元数据给了错的书名/作者**：核对过的字段不会被覆盖（`prefer_confirmed`）；
  未核对字段如被明显带偏（如英文错题），改清单字段并置 `confirmed` 后重跑，或在前端修正。
- **不想用命令行**：把封面目录告诉 Agent（Claude Code 会话），说「按 docs/batch-import.md 批量导入」即可，识别与执行由 Agent 完成。
