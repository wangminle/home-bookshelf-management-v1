# 任务跟踪列表

记录本项目所有任务：代码 bug、bug 转需求、新增需求、需求调整、功能开发、代码审查、测试数据、文档维护、配置运维等。

> 说明：本文件是当前项目的任务清单。所有新增事项、状态变更和完成记录都应同步写入本文件。
> 字段说明：动作字段只允许以下 8 个固定枚举：修复、开发、优化、调整、规划、检查、文档、运维。
> 时间说明：发现时间和完成时间分开记录，格式为 YYYY-MM-DD HH:MM，使用机器本地时区的 24 小时制时间；未完成事项的完成时间填 -。
> 归并规则：审计、复核、核查、审查、验证、评估统一记为"检查"；重构、清理统一记为"优化"；方案、梳理统一记为"规划"；记录类文档事项统一记为"文档"。

## 代码 Bug

| ID | 动作 | 问题描述 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| BUG-001 | 修复 | ISBN-10→13 校验位算法错误（权重应为 1/3 交替） | 2026-06-26 20:00 | 2026-06-26 20:30 | 已修复 | book_helpers.py；0306406152→9780306406157 |
| BUG-002 | 修复 | intake 已存在仍返回 201；/intake/json 缺 RuntimeError 处理 | 2026-06-26 20:00 | 2026-06-26 20:30 | 已修复 | api/v1/intake.py Response.status_code |
| BUG-003 | 修复 | ReadingProgress.updated_at 用本地时间；OperationLog.created_at 无默认值 | 2026-06-26 20:00 | 2026-06-26 20:30 | 已修复 | models/book.py、models/extension.py |
| BUG-004 | 修复 | 入库去重对 JSON authors 使用 ilike 子串误匹配 | 2026-06-26 20:00 | 2026-06-26 20:30 | 已修复 | intake.py _find_existing/_authors_match |
| BUG-005 | 修复 | 入库未统一 canonical ISBN-13；去重不查 isbn10/格式换算 | 2026-06-26 21:00 | 2026-06-26 22:00 | 已修复 | canonical_isbn13、isbn_lookup_keys |
| BUG-006 | 修复 | 无作者时书名去重误命中第一本；库中无作者时任意作者均匹配 | 2026-06-26 21:00 | 2026-06-26 22:00 | 已修复 | intake.py _find_existing/_authors_match |
| BUG-007 | 修复 | IntegrityError 未捕获导致 500；member/copy 未校验 | 2026-06-26 21:00 | 2026-06-26 22:00 | 已修复 | utils/db_errors.py ConflictError→409 |
| BUG-008 | 修复 | 列表搜索多条件 OR 并集；intake 允许 price≤0；进度 page/percent 覆盖冲突 | 2026-06-26 21:00 | 2026-06-26 22:00 | 已修复 | books.py AND；schemas/intake；reading.py |
| BUG-009 | 修复 | 重复入库不创建 BookCopy；OpenLibrary 搜索 ISBN 未 normalize | 2026-06-26 21:00 | 2026-06-26 22:00 | 已修复 | intake.py location 副本；openlibrary.py |
| BUG-010 | 修复 | /health 不探测 DB；last_read_at 未更新；status 无枚举校验 | 2026-06-26 21:00 | 2026-06-26 22:00 | 已修复 | health.py；reading.py；schemas/reading.py |
| BUG-011 | 修复 | OpenLibrary language 字段写入 /languages/eng（14字符）超过 String(10)，SQLite 存脏值、Postgres 报 DataError | 2026-06-26 17:39 | 2026-06-26 17:44 | 已修复 | openlibrary.py _parse_language 取 key 末段 eng，截断至 10 字符 |
| BUG-012 | 修复 | POST /books 重复 ISBN13 未捕获 IntegrityError，并发或漏判时返回 500 | 2026-06-26 17:39 | 2026-06-26 17:44 | 已修复 | books.py commit try/except IntegrityError→409 |
| BUG-013 | 修复 | /health 数据库断开仍返回 HTTP 200，Docker healthcheck 无法识别不健康 | 2026-06-26 17:39 | 2026-06-26 17:44 | 已修复 | health.py DB 异常时 response.status_code=503 |
| BUG-014 | 修复 | 书名入库（无 ISBN）即便元数据有 cover_url 也不下载封面 | 2026-06-26 17:39 | 2026-06-26 17:44 | 已修复 | intake.py 封面 target_name 回退 normalize_title(title) |
| BUG-015 | 修复 | GET /stats 的 total_spent 跨币种直接 SUM(price)，多币种购买统计金额错误 | 2026-06-26 18:12 | 2026-06-26 18:19 | 已修复 | stats.py 仅合计 currency=CNY（缺省视为 CNY） |
| BUG-016 | 修复 | ReadingLogCreate.log_date 无日期格式校验，任意字符串可入库，破坏 streak 与统计 | 2026-06-26 18:12 | 2026-06-26 18:19 | 已修复 | reading_log.py date.fromisoformat validator |
| BUG-017 | 修复 | /recognize/cover 无 title 时封面保存为 cover_scan.jpg，多次扫描互相覆盖 | 2026-06-26 18:12 | 2026-06-26 18:19 | 已修复 | cover_recognition _cover_target_name 时间戳+uuid |
| BUG-018 | 修复 | CLI progress --status help 列了 abandoned/dropped，但 schema 仅允许 unread/reading/finished，传值被 422 拒绝 | 2026-06-26 18:12 | 2026-06-26 18:19 | 已修复 | 审查时 schema 已扩 5 态；复核 ProgressUpdate+CLI help 一致 |
| BUG-019 | 修复 | stats streak 用 UTC 今日比对本地 log_date，东八区凌晨差一天致当日 streak 漏算 | 2026-06-26 18:12 | 2026-06-26 18:19 | 已修复 | time_helpers.local_today_iso + stats streak 改用本地日 |
| BUG-020 | 修复 | PATCH /books/{id} 传重复标签时写入重复 book_tags，触发唯一约束返回 409 | 2026-06-28 01:03 | 2026-06-28 01:03 | 已修复 | services/books.py 标签清洗后按顺序去重 |
| BUG-021 | 修复 | POST/PATCH /books 未规范化手工 ISBN，带连字符 ISBN 可绕过重复检测生成重复书籍 | 2026-06-28 01:03 | 2026-06-28 01:03 | 已修复 | books.py + services/books.py 使用 canonical_isbn13/normalize_isbn |
| BUG-022 | 修复 | 附件 entity_type 未做白名单/清洗，可构造 `../../x` 实现任意文件写入（路径穿越） | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | service 层已有 ALLOWED_ENTITY_TYPES 白名单+entity_id 校验+relative_to 路径穿越拦截；本次补 schemas/attachment.py entity_type/attach_type Literal 枚举 + api/v1/attachments.py 捕获 ValidationError→422 |
| BUG-023 | 修复 | NLC 出版社正则 `r":\s*(.+),\s"` 要求逗号后空白，与国图真实格式 `北京:出版社,2024` 不符，出版社几乎恒为 None | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | nlc.py _parse_publish_info 改 split+年份定位，复核通过 |
| BUG-024 | 修复 | OpenLibrary search `doc.get("key","").replace(...)` 当 key 显式为 null 时 AttributeError 中断 fallback | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | openlibrary.py:91-92 isinstance(key,str) 守卫，key 非 str 时不再 .replace |
| BUG-025 | 修复 | OpenLibrary _parse_data `data.get("cover",{}).get(...)` 当 cover 为 int/None 时 AttributeError | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | _get_cover_url 用 isinstance(cover,dict) 守卫 |
| BUG-026 | 修复 | PurchaseCreate/PurchaseOut 缺 original_price 字段，service 硬编码 original_price=price，原价永远等于实付价 | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | schemas/purchase.py + services/purchase.py:41 均已接入 original_price |
| BUG-027 | 修复 | storage.download_cover/save_uploaded_image `except Exception` 吞掉所有异常返回 None，下载失败/权限错误被静默掩盖 | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | storage.py:86-108 改为 logger.warning/exception 记录，不再静默 |
| BUG-028 | 修复 | download_cover 无大小上限、无 scheme/内网校验，恶意或异常封面可致磁盘耗尽/SSRF | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | storage.py _is_safe_url(scheme+DNS+私网/回环 IP 拦截)+MAX_COVER_BYTES 10MB 流式上限 |
| BUG-029 | 修复 | intake 新建路径无条件创建 BookCopy，与 existing 路径（仅 location 时创建）策略不一致，致副本膨胀 | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | intake.py 新建(133)/existing(204) 两路径统一为 if payload.location 才建副本 |
| BUG-030 | 修复 | 附件 commit 失败时已落盘文件不清理，产生孤儿文件堆积 | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | attachments.py:79-83 IntegrityError 时 dest.unlink(missing_ok) |
| BUG-031 | 修复 | CLI client._request 当 4xx body 为非 dict 列表（首元素非 dict）时 AttributeError，掩盖真实 HTTP 错误 | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | client.py:35-43 列表首元素非 dict 时 str(first) 兜底 |
| BUG-032 | 修复 | async 端点（intake/recognize/attachments）内同步调用 urllib/元数据链，阻塞事件循环拖垮并发 | 2026-06-28 01:20 | 2026-06-28 13:45 | 已修复 | intake/recognize/attachments 端点均用 run_in_threadpool 包装同步调用 |
| BUG-033 | 修复 | intake 在 _find_existing 之前就 save_uploaded_image 落盘封面；命中已有书走 _handle_existing_book 时 image_saved_path 被丢弃，封面文件成孤儿堆积，且已有书缺封面也不会补 | 2026-06-29 10:56 | 2026-06-29 12:09 | 已修复 | services/intake.py:62 先存图→:99 查重→:101 existing 分支未用 image_saved_path（grep 确认仅:103 新建路径用）。修法：查重后再存图，或 existing 分支在缺封面时回填 existing.cover_path |
| BUG-034 | 修复 | 用户可见日期仍用 utc_today_iso：reading.py:65 finish_date、intake.py:303 purchase_date、purchase.py:45 purchase_date，东八区 0-8 点记录成前一天（与已修 BUG-019 同类） | 2026-06-29 10:56 | 2026-06-29 12:09 | 已修复 | time_helpers 已有 local_today_iso（stats streak 已用）；将这 3 处统一改 local_today_iso。用户显式传 --date 不受影响 |
| BUG-035 | 修复 | 全新空库没有成员创建/初始化入口，doctor 提示绑定 member_id=1 但 POST /members/bind 必然返回 400 | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | 新增 POST /members 创建成员端点 + MemberCreate schema + CLI `member` 命令 + client.add_member；bind_member_channel 在空库 member_id=1 时自动创建默认 owner；doctor 提示补充 member 命令。TestClient 冒烟：空库 bind member_id=1 返回 200 |
| BUG-036 | 修复 | 书籍详情序列化购买记录时遗漏 original_price，新增购买接口返回 80，随后 GET /books/{id} 变成 null | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | utils/serializers.py purchase_to_out 补 original_price=purchase.original_price；TestClient GET /books/{id} purchase_records 含 original_price=20.0 |
| BUG-037 | 修复 | 附件只校验 book 实体，member/note 不校验存在性；file 类型无上传文件也能创建空附件 | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | services/attachments.py _validate_entity 扩展 member/note/copy 存在性校验；attach_type="file" 无 upload_path 时 raise ValueError。TestClient：未知 member/file 无上传 均 400/422 |
| BUG-038 | 修复 | 同一实体、同一标题的附件落盘文件名固定，后上传文件会静默覆盖先前附件内容 | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | services/attachments.py 文件名追加 uuid.uuid4().hex[:8] 唯一后缀；实测两同标题上传生成不同文件名，内容互不覆盖 |
| BUG-039 | 修复 | 自定义字段允许任意 entity_type、负 entity_id，且数据库缺少(entity_type,entity_id,field_key)唯一约束，并发 upsert 可产生重复记录 | 2026-07-11 19:35 | 2026-07-11 20:25 | 已修复 | 迁移 d4f1a2b3c5e7 升级前 DELETE 保留 MAX(id) 去重后再建 uq_custom_fields_entity_key；pytest 预置两条重复记录后 upgrade head 成功且仅留较新一行 |
| BUG-040 | 修复 | 核心输入校验不足：空白书名、负页数、非法购买日期、任意副本类型/状态等可写入数据库 | 2026-07-11 19:35 | 2026-07-11 20:25 | 已修复 | BookCreate/Update 对齐 ORM：title/subtitle≤500、language≤10、publisher≤200 等；PurchaseCreate.currency≤10；intake 元数据字段落库前截断。pytest：501 标题/11 字符 language·currency 均 ValidationError |
| BUG-041 | 修复 | ISBN 只清洗长度、不验证 ISBN-10/13 校验位，错误 ISBN 会被规范化、查元数据并持久化 | 2026-07-11 19:35 | 2026-07-11 20:25 | 已修复 | canonical_isbn13 先 is_valid_isbn；intake._resolve_isbn_fields 与 recognition 条码结果均丢弃错误校验位；脏元数据 ISBN 不再入库。pytest 覆盖 intake+recognize |
| BUG-042 | 修复 | 外部元数据响应结构异常时解析器可抛 AttributeError，chain 未隔离 provider 异常，导致后续数据源不再回退并使入库失败 | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | services/metadata/chain.py 新增 _safe_call 逐 provider 异常隔离+logger.warning；openlibrary.py _parse_data 对 authors/publishers/subjects/identifiers 加 isinstance 守卫；google_books.py identifiers 循环加 isinstance(dict) 守卫。单元测试：BoomProvider 异常被隔离返回 None 继续回退 |
| BUG-043 | 修复 | NLC 搜索结果相对链接被拼成 http://opac.nlc.cn，回退到明文 HTTP，与已完成的 HTTPS 加固目标不一致 | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | services/metadata/nlc.py _abs_url 相对链接改 https://opac.nlc.cn；绝对 http://opac.nlc.cn 链接也升级为 https |
| BUG-044 | 修复 | channel_bindings 仅可写入和展示，所有业务 API 均不校验外部渠道身份且可任意指定 member_id，“渠道白名单鉴权”需求实际未生效 | 2026-07-11 19:35 | 2026-07-11 20:25 | 已修复 | 半组渠道头→400；非空库匿名 /members/bind→403（空库引导仍可用；可用 X-Setup-Token 或已绑定 owner 代绑）；CLI 透传 BOOKSHELF_SETUP_TOKEN/渠道头。pytest：匿名自绑+半组头均被拒，空库 bind 仍 200 |
| BUG-045 | 修复 | operation_logs 未覆盖 intake、progress、purchase、member bind 等关键写操作，且现有日志在业务提交后另行提交，日志失败会返回 500 但业务已生效 | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | utils/operation_log.py 新增 log_and_commit：日志失败 logger.warning+rollback 不破坏已成功业务结果；books/copies/attachments/notes/reading-logs/progress/purchases/members-bind/members-create/custom-fields/intake 全部接入 log_and_commit 覆盖关键写操作。TestClient：operation_logs 含 book.create/member.create/member.bind/progress.update/purchase.create |
| BUG-046 | 修复 | 当前工作区 deploy/backup.sh 丢失可执行位，Linux 上直接运行备份脚本会 Permission denied | 2026-07-11 19:35 | 2026-07-11 20:30 | 已修复 | git update-index --chmod=+x deploy/backup.sh + backend/install.sh（顺手补）；git ls-files -s 确认三者均 100755 |
| BUG-047 | 修复 | 提交 65596f7 的新增成员流程使空库先创建成员后无法匿名绑定渠道 | 2026-07-11 20:44 | 2026-07-11 20:50 | 已修复 | authorize_member_bind：系统尚无任何渠道绑定时允许首次初始化 bind（兼容 README 先 member 后 bind）；白名单建立后匿名仍 403。pytest：create→bind=200 |
| BUG-048 | 修复 | 渠道绑定缺少全局唯一性，重复外部身份会被解析为不确定成员 | 2026-07-11 20:44 | 2026-07-11 20:50 | 已修复 | bind_member_channel 拒绝同一 (channel,external_user_id) 绑到其他成员→409；resolve 对历史脏数据按 member.id 升序取确定性首个。pytest：重复绑定 409 |
| BUG-049 | 修复 | 附件服务新增 copy 实体支持但请求 schema 仍拒绝 copy | 2026-07-11 20:44 | 2026-07-11 20:50 | 已修复 | AttachmentCreate.entity_type Literal 增加 copy，与 attachments 服务 ALLOWED_ENTITY_TYPES 对齐。pytest：copy markdown 附件 201 |

## 调整事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| ADJ-001 | 调整 | M5 飞书 Channel Adapter 不单独开发，改由 OpenClaw/Hermes 加载 Skills | 2026-06-26 19:00 | 2026-06-26 19:00 | 已完成 | 对应 DEV-008 已关闭 |
| ADJ-002 | 调整 | CLI 版本号 0.1.0 → 0.1.1（cli/pyproject.toml，全仓唯一版本定义点；后端无版本字段） | 2026-06-29 14:13 | 2026-06-29 14:13 | 已完成 | 全仓仅 cli/pyproject.toml:3 一处项目版本；requirements.txt 的 pyzbar>=0.1.9 为依赖版本无关；后端无 __version__，本次未加 |
| ADJ-003 | 调整 | CLI 版本号 0.1.1 → 0.1.8（cli/pyproject.toml 唯一版本点；后端无版本字段） | 2026-07-11 20:40 | 2026-07-11 20:40 | 已完成 | cli/pyproject.toml:3 0.1.1→0.1.8；home-bookshelf/1.0 等为元数据 User-Agent 与项目版本无关，不动；后端无 __version__ 未加 |

## 检查事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| CHK-001 | 检查 | 一期验收：拍照/ISBN/文字入库→落库→查询链路端到端 | 2026-06-26 16:45 | - | 待开发 | Agent（OpenClaw/Hermes）+ CLI 联调后执行 |
| CHK-002 | 检查 | 代码审查（两轮）：后端/CLI/模型系统性检查 | 2026-06-26 20:00 | 2026-06-26 22:00 | 已完成 | 共 25 项，均已修复并记入 BUG-001~010 |
| CHK-003 | 检查 | 全项目代码+文档 bug 审查（backend/cli/deploy/docs/skills） | 2026-06-26 17:39 | 2026-06-26 17:39 | 已完成 | 发现 4 代码 bug(BUG-011~014)+文档不一致(DOC-005)+加固项(OPT-004) |
| CHK-004 | 检查 | 第二轮 bug 审查：新增 8 端点/6 表写入路径/CLI doctor/3 skills/2 迁移 | 2026-06-26 18:12 | 2026-06-26 18:12 | 已完成 | 导入+迁移链+alembic check 全通过；发现 BUG-015~019 |
| CHK-005 | 检查 | 第三轮 bug 检查：编译、应用导入、Alembic 空库迁移、API 冒烟与边界端点 | 2026-06-28 01:03 | 2026-06-28 01:03 | 已完成 | 发现并修复 BUG-020~021；compileall/SMOKE/EXTRA/ISBN 用例通过 |
| CHK-006 | 检查 | 第四轮 bug 审查：services/api/metadata/cli/schema/migration 全量逐文件 | 2026-06-28 01:20 | 2026-06-28 13:45 | 已完成 | 发现 BUG-022~032 共 11 项；本次全部复核确认修复（BUG-023~032 前序会话已修，BUG-022 本次补 schema 枚举+api 422）；compileall 全量通过 |
| CHK-007 | 检查 | 验证 backend/.venv 可用性 | 2026-06-28 13:30 | 2026-06-28 13:30 | 已完成 | pyvenv.cfg 显示为 macOS 创建（home=/Library/Frameworks/Python.framework，用户 fenix-macmini），布局 bin/+lib/ 无 Windows Scripts/，本机不可用；依赖版本清单完整（fastapi0.138.1/sqlalchemy2.0.51/alembic1.18.5 等），需用 install.bat 重建 |
| CHK-008 | 检查 | 第五轮 bug 复查：复核 BUG-015~019 修复+扫最近改动文件(attachments/intake/purchase/recognize/storage/reading) | 2026-06-29 10:56 | 2026-06-29 10:56 | 已完成 | BUG-015~019 全部确认已修复（BUG-018 经 ReadingStatus 扩 5 态解决）；app.main 导入通过；发现新 BUG-033(重入库封面孤儿文件)/BUG-034(3 处用户可见日期仍用 UTC) |
| CHK-009 | 检查 | 修复 BUG-033/034 并补双语 README | 2026-06-29 12:09 | 2026-06-29 12:09 | 已完成 | BUG-033:intake 改为查重后存图+已有书缺封面时回填(临时DB冒烟验证:新建存1次/重入库有封面0次无孤儿/缺封面回填1次落库);BUG-034:reading.finish_date+intake/purchase.purchase_date 3 处 utc→local_today_iso(冒烟验证=local_today);README 补中英双语+切换标签 |
| CHK-010 | 检查 | 第六轮全项目逻辑/需求审查：模型、迁移、API、服务、CLI、Skills、部署与文档 | 2026-07-11 19:35 | 2026-07-11 19:35 | 已完成 | compileall、空库 alembic upgrade/check、pip check 通过；临时库 API 边界冒烟完成；发现 BUG-035~046、TST-001、DOC-014；未修改业务代码 |
| CHK-011 | 检查 | 复验 BUG-035~046 修复及 TST-001/DOC-014 完成情况 | 2026-07-11 20:10 | 2026-07-11 20:10 | 已完成 | BUG-035/036/037/038/042/043/045/046 通过；BUG-039/040/041/044 边界复验未通过并恢复待修复；TST-001、DOC-014 仍待开发。compileall/pip check/空库迁移+alembic check/task-list check 通过；旧重复数据迁移、鉴权绕过、元数据脏 ISBN 均稳定复现 |
| CHK-012 | 检查 | 复验 BUG-039/040/041/044 二次修复 | 2026-07-11 20:25 | 2026-07-11 20:25 | 已完成 | pytest 10 项全过：迁移去重、schema 长度、ISBN 全入口、半组头 400、匿名自绑 403、空库 bind 200；compileall/pip check/空库 alembic upgrade+check 通过 |
| CHK-013 | 检查 | 审查提交 65596f75a4a05dd4b9f3d684dee489b09a2d17f0 | 2026-07-11 20:44 | 2026-07-11 20:44 | 已完成 | 发现 BUG-047~049；pytest 本机执行 4 项通过、6 项因沙箱 Temp 目录 PermissionError 未运行 |
| CHK-014 | 检查 | 修复并复验 BUG-047/048/049 | 2026-07-11 20:50 | 2026-07-11 20:50 | 已完成 | 三项均确认存在并已修复；backend/tests 13 passed（含初始化 bind、重复身份 409、copy 附件） |
| CHK-015 | 检查 | 审计 design/ 目录文档完成度与一期实现对照 | 2026-07-11 20:52 | 2026-07-11 20:52 | 已完成 | design 主方案 v1.2 约 90% 写完、一期实现约 85-90%；docs/ 镜像滞后；缺口 CHK-001/TST-001/DOC-014；二期 PLN 未做属预期 |

## 测试数据

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| TST-001 | 检查 | 补齐后端/CLI 自动化回归测试，覆盖空库初始化、入库去重、购买详情、附件/自定义字段完整性、日期/ISBN 边界和鉴权 | 2026-07-11 19:35 | - | 待开发 | 已落地 backend/tests/ 覆盖 BUG-039/040/041/044 回归（pytest 10 项）；仍缺空库初始化/入库去重/购买详情/附件等更广覆盖，pytest 未写入 requirements |

## 文档维护

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| DOC-001 | 文档 | 输出家庭图书管理系统总体设计方案（V2） | 2026-06-26 14:56 | 2026-06-26 15:30 | 已完成 | docs/家庭图书管理系统-设计方案.md |
| DOC-002 | 文档 | GitHub 同类项目调研（>300 star） | 2026-06-26 15:28 | 2026-06-26 15:35 | 已完成 | docs/参考项目调研.md |
| DOC-003 | 文档 | mybibliotheca + jelu Schema 对照与一期表设计细化 | 2026-06-26 15:40 | 2026-06-26 16:10 | 已完成 | docs/数据库Schema对照与一期细化.md |
| DOC-004 | 文档 | ISBN 元数据 API 调研（OpenLibrary/Google/国图） | 2026-06-26 17:00 | 2026-06-26 18:00 | 已完成 | docs/ISBN元数据API调研.md |
| DOC-005 | 文档 | task-list 标准化诊断与 extended profile 校验 | 2026-06-26 17:25 | 2026-06-26 17:25 | 已完成 | docs/task-list-standardize-report.md；check 通过 |
| DOC-006 | 文档 | CLAUDE.md 协作约定与会话结束 task-list 同步规则 | 2026-06-26 17:25 | 2026-06-26 17:25 | 已完成 | CLAUDE.md + .claude/settings.json Stop hook |
| DOC-007 | 文档 | 设计/Schema 文档承诺的端点代码未实现：PATCH /books/{id}、copies、notes、attachments、custom-fields、stats、recognize/cover、members 共 8 个 | 2026-06-26 17:39 | 2026-06-26 17:56 | 已完成 | 已实现全部端点 + README API 表 |
| DOC-008 | 文档 | reading_logs/reading_notes/attachments/custom_fields/tags/operation_logs 6 张表在 api/services 中无任何写入路径（reading_logs 为文档重点特色却不可用） | 2026-06-26 17:39 | 2026-06-26 17:56 | 已完成 | reading-logs/notes/attachments/custom-fields/tags/operation_logs 均已接入 |
| DOC-009 | 文档 | task-list DEV-002 13 张表应为 12；Schema §6 端点路径(reading-logs/PATCH/stats)与代码不符；ProgressUpdate status 枚举(仅 unread/reading/finished)与文档(含 abandoned/dropped)不一致 | 2026-06-26 17:39 | 2026-06-26 18:12 | 已完成 | 设计方案§6+Schema§6 加实现状态列；12表清单；POST progress |
| DOC-010 | 文档 | 设计方案/Schema 文档与代码对齐（表数量、端点路径、进度字段名、交付物清单） | 2026-06-26 18:12 | 2026-06-26 18:12 | 已完成 | 设计方案.md + 数据库Schema对照与一期细化.md |
| DOC-011 | 文档 | 内部业务流转 SVG 流程图（IM→Agent→Skills→CLI→API→DB） | 2026-06-26 18:13 | 2026-06-26 18:25 | 已完成 | docs/业务流转流程图.svg；修复编码损坏与 XML 非法字符 |
| DOC-012 | 文档 | README「启动后端」章节补充 Windows CMD 启动命令 + 跨平台一键安装脚本说明 + pyzbar 平台运行时依赖提示 | 2026-06-28 13:30 | 2026-06-28 13:30 | 已完成 | README.md §快速启动；对应 OPR-001 |
| DOC-013 | 文档 | README 扩为中英双语（中文在前，标题下切换标签），覆盖核心功能/项目结构/CLI/Skills/Agent指南/后端安装 | 2026-06-29 12:09 | 2026-06-29 12:09 | 已完成 | README.md;核对 install.sh/docker-compose/.env.example/zbar 依赖均准确;含安全提示(仅可信局域网) |
| DOC-014 | 文档 | 设计方案 CLI 示例与实际一期范围不一致：list/attach/field、find --status、stats --by/--spending/--year、--member 姓名均未实现；入库“默认创建副本”也与当前仅 location 时创建不符 | 2026-07-11 19:35 | 2026-07-11 21:00 | 已完成 | design/ 主方案升至 v1.3：§5.1 二期命令已注释、§6.1 仅 location 创建副本、§7.2/§9 去掉独立 channels/、补 POST /members；Schema §3.3/role 对齐代码（用户向说明改写至 docs/，见 DOC-016） |
| DOC-015 | 文档 | 误将 design 设计稿镜像到 docs/ | 2026-07-11 21:00 | 2026-07-11 21:05 | 已完成 | 已撤销「docs=design 镜像」做法；docs/ 改为用户向说明（DOC-016） |
| DOC-016 | 文档 | 按 Cursor 风格重建 docs/：快速开始、使用指南、CLI 参考、部署、Agent 接入、FAQ | 2026-07-11 21:05 | 2026-07-11 21:05 | 已完成 | docs/ 与 design/ 职责分离；根 README 作唯一入口索引；docs/ 不设 README，避免与项目 README 定位冲突 |

## 功能开发

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| DEV-001 | 开发 | M1：后端骨架（FastAPI + SQLAlchemy + Alembic） | 2026-06-26 16:10 | 2026-06-26 16:45 | 已完成 | backend/app/ 目录结构、config/db/main |
| DEV-002 | 开发 | M1：12 张表 ORM 模型与初始迁移 | 2026-06-26 16:10 | 2026-06-26 16:45 | 已完成 | alembic/versions/a5bfb4c64f04_initial_schema.py |
| DEV-003 | 开发 | M1：基础 API（health / books 列表·创建·详情） | 2026-06-26 16:10 | 2026-06-26 16:45 | 已完成 | GET/POST /api/v1/books |
| DEV-004 | 开发 | M2：bookshelf CLI（add / find / show） | 2026-06-26 16:45 | 2026-06-26 17:00 | 已完成 | cli/bookshelf/ Typer + JSON 输出 |
| DEV-005 | 开发 | M3：ISBN 条码识别 + 元数据补全（初版 OpenLibrary） | 2026-06-26 16:45 | 2026-06-26 17:00 | 已完成 | pyzbar + MetadataProvider |
| DEV-006 | 开发 | M3：POST /api/v1/books/intake 入库编排接口 | 2026-06-26 16:45 | 2026-06-26 17:00 | 已完成 | multipart + JSON 双入口 |
| DEV-007 | 开发 | M3+：多源元数据链（国图/Google Books/Open Library + 搜索兜底） | 2026-06-26 18:00 | 2026-06-26 19:00 | 已完成 | metadata/chain.py、nlc.py、google_books.py |
| DEV-008 | 开发 | M4：Skills 编写（book-intake / book-query / reading-tracker / purchase-logger） | 2026-06-26 16:45 | 2026-06-26 18:30 | 已完成 | skills/ + progress/purchase API/CLI |
| DEV-009 | 开发 | M5：飞书 Channel Adapter（消息→Agent→CLI→回复） | 2026-06-26 16:45 | 2026-06-26 19:00 | 已关闭 | 见 ADJ-001；不单独实现 channels/ |
| DEV-010 | 开发 | M6：家庭服务器部署（docker-compose / systemd + 数据备份） | 2026-06-26 16:45 | 2026-06-26 19:00 | 已完成 | deploy/ 目录 |
| DEV-011 | 开发 | 文档承诺端点全量补全：8 API + 6 表写入 + stats/members/cover + CLI note/reading-log/stats + Skills | 2026-06-26 17:56 | 2026-06-26 17:56 | 已完成 | api/v1/* + services/* + skills/note-taker + shelf-report |
| DEV-012 | 开发 | 书架初始化：bookshelf-setup Skill + bookshelf doctor/bind CLI + health 诊断字段 | 2026-06-26 18:30 | 2026-06-26 18:30 | 已完成 | skills/bookshelf-setup/；cli/bookshelf/doctor.py；health google_books/barcode 标志 |

## 配置运维

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| OPR-001 | 运维 | 新增跨平台后端安装脚本 backend/install.sh + install.bat，封装 venv 创建+pip 安装+alembic 迁移，自动检测并重建异平台（如 macOS 同步来）的 .venv | 2026-06-28 13:30 | 2026-06-28 13:30 | 已完成 | bash -n 校验通过；README 已挂接一键命令 |

## 规划事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| PLN-001 | 规划 | 二期：局域网数字书架 Web UI（封面墙/筛选/详情页） | 2026-06-26 16:45 | - | 待开发 | 参考 calibre-web / komga / kavita |
| PLN-002 | 规划 | 二期：藏书概览图生成与阅读统计 | 2026-06-26 16:45 | - | 待开发 | virtual-bookshelf 视觉参考 |
| PLN-003 | 规划 | 二期：电子书上传与浏览器在线阅读（EPUB/PDF） | 2026-06-26 16:45 | - | 待开发 | epub.js / pdf.js + book_copies.file_path |
| PLN-004 | 规划 | 三期预留：家庭间图书交换与信息发布（仅架构预留） | 2026-06-26 16:45 | - | 待开发 | 本期不实现，book_copies.status 预留 lent_out |

## 优化事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| OPT-001 | 优化 | CLI 入库超时 90s；SQLite busy_timeout；封面文件名 sanitize | 2026-06-26 20:30 | 2026-06-26 21:00 | 已完成 | client.py INTAKE_TIMEOUT；db.py；storage.py |
| OPT-002 | 优化 | OpenLibrary fetch_by_isbn 递归改循环 | 2026-06-26 20:00 | 2026-06-26 20:30 | 已完成 | openlibrary.py |
| OPT-003 | 优化 | 业务日期统一 UTC；CLI 响应附带 _http_status | 2026-06-26 21:00 | 2026-06-26 22:00 | 已完成 | time_helpers.py；cli/bookshelf/client.py |
| OPT-004 | 优化 | 加固项：迁移补 server_default、isbn10 加索引、backup.sh WAL 警告、LIKE 通配符转义、finish_date 回清与 current_page 上限、google_books 删未用 import re、NLC 改 HTTPS | 2026-06-26 17:39 | 2026-06-26 18:12 | 已完成 | b7e2a1c904f3 isbn10 索引；c8d9e0f1a2b3 server_default |

## 调研事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| RES-001 | 规划 | 深挖 mybibliotheca 数据库 Schema（v1 SQLite + v2 KuzuDB） | 2026-06-26 15:40 | 2026-06-26 16:05 | 已完成 | master_schema.json + 官方文档 |
| RES-002 | 规划 | 深挖 jelu 数据库 Schema（book/user_book/reading_event） | 2026-06-26 15:40 | 2026-06-26 16:05 | 已完成 | BookTable.kt / UserBookTable.kt / ReadingEventTable.kt |
| RES-003 | 规划 | ISBN 元数据 API 源对比（OpenLibrary/Google/国图/NLC 插件） | 2026-06-26 17:00 | 2026-06-26 18:00 | 已完成 | 结论见 DOC-004；实现见 DEV-007 |

## 统计摘要

| 分类 | 总数 | 已完成 | 待开发/待修复 | 完成率 |
| --- | --- | --- | --- | --- |
| 代码 Bug | 49 | 46 | 3 | 94% |
| 调整事项 | 3 | 3 | 0 | 100% |
| 检查事项 | 13 | 12 | 1 | 92% |
| 测试数据 | 1 | 0 | 1 | 0% |
| 文档维护 | 14 | 13 | 1 | 93% |
| 功能开发 | 12 | 12 | 0 | 100% |
| 配置运维 | 1 | 1 | 0 | 100% |
| 规划事项 | 4 | 0 | 4 | 0% |
| 优化事项 | 4 | 4 | 0 | 100% |
| 调研事项 | 3 | 3 | 0 | 100% |
| **总计** | 104 | 94 | 10 | 90% |
