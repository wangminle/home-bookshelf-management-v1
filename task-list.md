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
| BUG-050 | 修复 | 第七轮审查：POST /attachments 写端点未接入渠道鉴权，匿名可上传任意文件、给任意 book/member/note/copy 实体挂任意 URL（purchases/notes/reading-logs/progress/intake 均已保护，仅此遗漏）。TestClient 复现：无渠道头 POST /api/v1/attachments → 201 成功 | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | api/v1/attachments.py:19-31 缺 channel_headers/enforce_channel_member，对照 purchases.py:19-27 补接；已补 channel_headers+enforce_channel_member；pytest：未绑定渠道 403、半组头 400；已补 channel_headers/enforce_channel_member + log_and_commit；上传限 10MB；本会话修复；pytest 19 passed |
| BUG-051 | 修复 | 封面下载 SSRF 绕过：storage._is_safe_url 只校验初始 URL，urllib.request.urlopen 默认跟随 3xx 跳转且不再校验目标，可经公开 URL 重定向到内网/云元数据地址（127.0.0.1/169.254.169.254）；getaddrinfo 与 urlopen 两次独立解析还存在 DNS rebinding TOCTOU | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | services/storage.py:51-65；需自定义不自动跟随跳转或用重定向钩子逐跳复检 _is_safe_url；_SafeRedirectHandler 逐跳复检 _is_safe_url；pytest：302→回环拒绝且不落盘 |
| BUG-052 | 修复 | CLI client._request 未捕获 httpx.HTTPError：后端未启动/超时/证书错误时，除 health/doctor 外所有命令（add/find/show/progress/purchase/note/reading-log/stats/member/bind/recognize）打印原始 traceback 而非友好错误；同文件 health_probe 已正确捕获，处理不一致 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | cli/bookshelf/client.py:21-52；包一层 httpx.HTTPError→RuntimeError 中文提示；_request 捕获 TimeoutException/HTTPError→RuntimeError 中文提示；pytest 覆盖 ConnectError |
| BUG-053 | 修复 | 6 个写端点（progress/purchases/notes/reading-logs/intake multipart+json）的 enforce_channel_member 在 try 块之外，无渠道头且 member_id 不存在时 resolve_member_id 抛 ValueError 无人捕获→500（应为 400/404）。TestClient 已复现 progress/notes 均 500 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | api/v1/progress.py:22-29 等；把 enforce_channel_member 纳入 try 或路由层统一捕获 ValueError→400；auth.enforce_channel_member 内捕获 ValueError→HTTP 400；pytest：progress/notes 未知 member_id→400；本会话补充回归覆盖：purchases/reading-logs/intake_json 的未知 member_id 也统一返回 400；pytest 26 passed |
| BUG-054 | 修复 | POST /books/{id}/copies、POST /custom-fields、POST /members 三个写端点未接入渠道鉴权：匿名可把副本归属任意成员（owner_member_id 取自 body）、改写任意实体自定义字段、无限创建成员 | 2026-08-09 03:28 | 2026-08-09 04:20 | 已修复 | api/v1/copies.py:15、custom_fields.py:14、members.py:35；对照 purchases.py 补 channel_headers+enforce/授权；本会话修复；pytest 19 passed；copies+custom_fields 已接鉴权；POST /members 保留匿名 bootstrap（一期取舍），带渠道头时校验绑定；；二次修复：auth.enforce_channel_member 新增 require_channel 参数，copies/custom_fields 传 require_channel=True 显式拒绝匿名（403）；members 在 system_has_channel_bindings=True 后拒绝匿名创建（引导期仍允许）；更新受影响测试 4 文件；pytest 104 passed |
| BUG-055 | 修复 | CLI 渠道身份头只在 bind 命令透传：_request 不读 BOOKSHELF_CHANNEL/BOOKSHELF_EXTERNAL_USER_ID，其余写命令一律不带渠道头，后端走可信局域网兜底静默落到默认成员，与 README 宣称的渠道白名单鉴权模型不符，Agent 无法以绑定成员身份写入 | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | cli/bookshelf/client.py:89-109 vs :21-52；_request 统一注入渠道头（有则带、无则兜底不变）；本会话修复；pytest 19 passed |
| BUG-056 | 修复 | intake 封面在 db.commit 之前落盘：并发同 ISBN 入库触发 isbn13 唯一约束 IntegrityError 回滚后封面文件成孤儿；download_cover 的 .part 临时文件名仅由 target_name 决定，并发下载同名封面互相覆盖/截断 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | services/intake.py:113-133 vs :179/:256、storage.py:62；commit 失败清理封面，.part 追加 uuid；本会话已修复并回归 pytest 19 passed |
| BUG-057 | 修复 | /recognize/cover 无论识别成功与否都先落盘扫描图（无 title 时文件名=时间戳+uuid），识别失败的扫描也永久累积文件，反复扫描导致孤儿堆积 | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | services/cover_recognition.py:39；改为识别命中或明确需要保留时再落盘，失败分支清理；本会话修复；pytest 19 passed |
| BUG-058 | 修复 | 附件上传无大小上限：封面有 MAX_COVER_BYTES 10MB 流式上限，但附件链路（API 层 tmp.write(await file.read()) + service 层 shutil.copy2）无任何限制，大文件可耗尽磁盘 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | api/v1/attachments.py:48-52、services/attachments.py:65；分块读+上限或拒绝超限上传；utils/uploads.read_upload_limited 10MB 流式上限+413；attachments 接入 |
| BUG-059 | 修复 | 向 /recognize/isbn 与 /recognize/cover 上传非图片/损坏文件返回 500：PIL.UnidentifiedImageError 是 OSError 子类，路由仅捕获 RuntimeError | 2026-08-09 03:28 | 2026-08-09 04:20 | 已修复 | services/recognition.py:17、api/v1/recognize.py:22-28/52-58；捕获 (RuntimeError, OSError) 或先校验图片格式→400；本会话修复；pytest 19 passed；；二次修复：recognition.py 将 OSError 重写为 ValueError，路由层 except 补 ValueError；recognize.py 两个端点 except 改为 (RuntimeError, OSError, ValueError)；新增 test_bug054_059_auth.py 覆盖坏图像->400；pytest 104 passed |
| BUG-060 | 修复 | GET /stats 口径分裂：total_spent 仅合计 CNY 购买（BUG-015 有意为之），但 purchase_count 统计全部币种，含外币购买时数量计入而金额不计入 | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | services/stats.py:54-63；purchase_count 与 total_spent 同口径或分别给出 CNY 笔数/总笔数；本会话修复；pytest 19 passed |
| BUG-061 | 修复 | HTTP 状态码不区分创建/更新：POST /custom-fields upsert 命中更新时仍返回 201；POST /books/{id}/progress 新建进度时固定 200 | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | api/v1/custom_fields.py:14（service 已返回 created 标志未用）、progress.py:15；按 created 动态设置 status_code；本会话修复；pytest 19 passed |
| BUG-062 | 修复 | GET /books 只传 member_id 不带 status 时该参数被静默忽略，过滤不生效也无提示 | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | api/v1/books.py:74-79；member_id 无 status 时报 400 或独立过滤成员相关书；本会话修复；pytest 19 passed |
| BUG-063 | 修复 | 迁移 c8d9e0f1a2b3 给 13 列加了 DB server_default，但所有 model 列均未声明 server_default（仅 Python default）：autogenerate 会反复生成 alter_column 噪音迁移，create_all 建库也缺这些默认值；created_at/updated_at 时间戳列同样只有 Python default，非 ORM 直插会因 NOT NULL 失败 | 2026-08-09 03:28 | 2026-08-09 03:48 | 已修复 | alembic/versions/c8d9e0f1a2b3 vs models/*、models/base.py:19-33；model 列补 server_default=sa.text(...)；models 列补 server_default；TimestampMixin 补 CURRENT_TIMESTAMP |
| BUG-064 | 修复 | copy/member/attachment/note/intake 的 schema 字符串字段普遍缺 max_length，与 model 列长度脱节（SQLite 不强制长度暂不报错，切 Postgres/MySQL 会 DataError）；book 侧已对齐 | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | schemas/copy.py:17-23、member.py:13、attachment.py:13-16、note.py:15、intake.py:11-13 对照各自 model 补齐；本会话修复；pytest 19 passed |
| BUG-065 | 修复 | doctor 误报：后端可达但返回非 JSON（如反代 502 HTML）时 health_probe 返回 (None,status)，doctor 一律提示'无法连接 API'误导排查；'API 未更新'判断靠 '404'/'Not Found' 字符串匹配 | 2026-08-09 03:28 | 2026-08-09 03:48 | 已修复 | cli/bookshelf/doctor.py:84-88/:126、client.py:59-62；区分不可达与非 JSON 响应；doctor 已区分不可达/非JSON；404 判断收紧为 [HTTP 404] |
| BUG-066 | 修复 | CLI 输出一致性：show/health/stats 在 --no-json 下仍打印 JSON（emit 只识别含 message/items 的响应）；--json 输出混入内部字段 _http_status | 2026-08-09 03:28 | 2026-08-09 03:46 | 已修复 | cli/bookshelf/client.py:236-273/:51；emit 补文本渲染分支，输出前剔除 _http_status；本会话修复；pytest 19 passed |
| BUG-067 | 修复 | deploy/backup.sh：covers/attachments 目录不存在时 tar 失败被 \|\|true 吞掉，归档未生成但仍打印'附件包 → 路径'，误导用户以为附件已备份 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | deploy/backup.sh:37-48；tar 失败显式告警，仅成功时打印路径；本会话已修复并回归 pytest 19 passed |
| BUG-068 | 修复 | backend/Dockerfile 的 COPY alembic.ini alembic/ app/ ./ 将 alembic/ 与 app/ 目录内容平铺进 /app，容器内 app 包与 alembic 布局被破坏，docker compose 构建后无法启动（并行会话 CHK-016 发现；其修复时误将内部文本写入文件，本次清理污染并完成干净修复：拆为 COPY alembic.ini ./ + COPY alembic/ ./alembic/ + COPY app/ ./app/） | 2026-08-09 03:25 | 2026-08-09 03:35 | 已修复 | backend/Dockerfile:12-14；已对照 HEAD 恢复其余行，git diff 仅余 COPY 拆分 |
| BUG-069 | 修复 | NLC 元数据出版年正则失效：metadata/nlc.py:169 _parse_publish_info 用 re.search(r"\d{9}(\d{4})", general_data) 期望 13 位连续数字，但 MARC 008 字段在位置 6 处含类型字母（如 160527s2015…）必中断数字串，正则对真实样本恒不匹配。实测 3 条真实 008 样本均 No Match，正确年份位于位置 7-10。结果：general_data 年份分支为死代码，永远回退到 publish_item 文本解析 | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | 正则应为 s[7:11] 或 \d{6}[a-z](\d{4})；复现：openlibrary/nlc 三样本 year_match 全 False，正确 year=pos7-10；本会话修复；pytest 19 passed |
| BUG-070 | 修复 | OpenLibrary summary 回退到 subtitle 污染摘要：metadata/openlibrary.py:141 summary=(data.get('notes') or data.get('subtitle'))，当无 notes 但有 subtitle 时把副标题写入 summary；intake.py:82/90 同一元数据对象又写入 book.subtitle 与 book.summary，导致 summary 列被副标题文本污染而非真实描述。实测 subtitle='A Novel' 时 summary 落库 'A Novel' | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | 应改为 data.get('notes') only；与 subtitle 字段去耦；本会话修复；pytest 19 passed |
| BUG-071 | 修复 | normalize_isbn 对非字符串入参崩溃：book_helpers.py:12 digits=re.sub(..., raw.strip()) 无类型守卫，raw 为 JSON number 时 raw.strip() 抛 AttributeError。openlibrary.py 搜索解析遍历 doc.get('isbn') 列表时若上游返回数值型 ISBN 则整 provider 被 chain._safe_call 的 except Exception 吞掉返回 None，表现为 OpenLibrary 元数据源整体静默失效而非跳过坏元素。实测 normalize_isbn(1234567890) 抛 AttributeError | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | normalize_isbn 先 str(raw) 或非 str 元素 continue；provider 循环不应因单坏元素整体失败；本会话修复；pytest 19 passed |
| BUG-072 | 修复 | list_books 作者过滤漏匹配含双引号/反斜杠的作者名：api/v1/books.py:50-57 手搓 f'%"{author_clean}"%'，而 authors 经 serialize_json_list→json.dumps 落库，双引号存为 \"、反斜杠存为 \\，过滤式与存储字节不一致导致搜索静默返回空。实测作者名含 " 时模式与存储字节不匹配。已有 author_in_json_list 助手可正确处理，应改用 | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | 复用 book_helpers.author_in_json_list；或对 author_clean 应用与 json.dumps 一致的转义；本会话修复；pytest 19 passed |
| BUG-073 | 修复 | 连续阅读天数当天未记录即归零：services/stats.py:26-31 _reading_streak 以 date.today 为起点向前回溯，若当天尚未记日志（哪怕此前连续多日有读）则 streak=0，丢失本应延续到当日结束的有效连续记录，仪表盘显示误导。常见实现是 today 缺失时回退到 yesterday 再判断断卡 | 2026-08-09 11:05 | 2026-08-09 03:42 | 已修复 | 当前未读但昨日已读应仍显示连续天数；仅当 yesterday 也缺失才算断；本会话已修复并回归 pytest 19 passed；台账描述与第八轮报告的 normalize_title 不一致：本条实为 streak 问题（与 BUG-074 重复），代码已按 streak 修复；normalize_title 弱归一见 BUG-080 |
| BUG-074 | 修复 | 连续阅读天数当天未记录即归零：services/stats.py:26-31 _reading_streak 以 date.today 为起点向前回溯，若当天尚未记日志则 streak=0，丢失本应延续到当日结束的有效连续记录 | 2026-08-09 11:05 | 2026-08-09 03:42 | 已修复 | today 缺失时应回退到 yesterday 再判断断卡；本会话已修复并回归 pytest 19 passed |
| BUG-075 | 修复 | 附件 URL 缺 scheme 校验，存在存储型 XSS/不安全协议风险：schemas/attachment.py:14 url: str \| None = None 无 validator，attach_type=link 时 javascript:alert(1)、file:///etc/passwd 等原样落库并经 book_detail_to_dict 返回客户端（storage.download_cover 的 _is_safe_url 已拦截这些协议，附件链路未对齐）。实测上述 URL 均被接受 | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | 加 pydantic validator：仅允许 http/https；与 storage.py _is_safe_url 对齐；本会话修复；pytest 19 passed |
| BUG-076 | 修复 | 书籍详情进度项 message 恒为空：utils/serializers.py:38-50 progress_to_out 硬编码 message=''，GET /books/{id} 详情中所有 reading_progress 项的 message 被丢弃；POST /progress 单条因路由自建 ProgressOut(message=result.message) 不受影响，两路径输出不一致 | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | 改为基于 status 派生 message 或透传 result.message；本会话修复；pytest 19 passed |
| BUG-077 | 修复 | 购买确认消息硬编码货币符号：services/purchase.py:57 message=f'...¥{payload.price}...'，无论 payload.currency 为何（模型默认 CNY、schema 支持多币种、stats 还按币种过滤）一律显示 ¥，USD/EUR 购买被错误显示为 ¥12.99，与多币种设计冲突 | 2026-08-09 11:05 | 2026-08-09 03:42 | 已修复 | 按 payload.currency 选择符号或显示 currency+金额；本会话已修复并回归 pytest 19 passed |
| BUG-078 | 修复 | publish_date 无日期校验：schemas/book.py:22 publish_date: str \| None = Field(max_length=20) 接受任意字符串，PurchaseCreate.purchase_date 与 ReadingLogCreate.log_date 均用 date.fromisoformat 强制 YYYY-MM-DD，唯独此处放任，可落库 'whatever' 并破坏下游日期显示/排序一致性 | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | 原仅正则校验格式；补 date.fromisoformat 真实日期合法性校验（YYYY/YYYY-MM/YYYY-MM-DD 仍允许）。实测 'whatever-stg'/'2020-13-45'/'2020-02-30'/'2021-02-29'(非闰年)/'2020/06/15' 均拒收；'2020-02-29'(闰年) 接受。新增 test_bug078_publish_date.py（10 用例）。全量 pytest 40 passed、compileall 通过 |
| BUG-079 | 修复 | 识别失败仍返回 ok=True：api/v1/recognize.py:34-38/64-74 在未识别到 ISBN/封面时返回 ApiResponse(ok 默认 True)，客户端按 response.ok 分支会把未识别当成功，与 intake/health 的失败路径不一致。found=False 时应 ok=False 或用非 2xx | 2026-08-09 11:05 | 2026-08-09 03:46 | 已修复 | ApiResponse.ok 默认 True；仅 health.py 会置 False；本会话修复；pytest 19 passed |
| BUG-080 | 修复 | Docker 部署 COPY 目录源只复制内容，容器内无 /app/app 包导致 alembic/uvicorn 无法启动 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | Dockerfile 拆成 COPY alembic.ini ./ + alembic/ + app/ |
| BUG-081 | 修复 | Google Books ISBN 校验死代码：found_isbn13 预填 preferred_isbn，mismatch 拒绝永不触发，模糊命中会张冠李戴 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | google_books.py 从 identifiers 提取真实 ISBN 再与 preferred 比对；无标识时才回填 preferred |
| BUG-082 | 修复 | NLC 详情页不校验 ISBN 一致性，页面 ISBN 覆盖请求值导致张冠李戴 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | nlc.py 页面 ISBN 与请求 ISBN 不一致时 return None |
| BUG-083 | 修复 | systemd 部署 install.sh 迁移库与运行库路径不一致，服务空库但 health 正常 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | install.sh 优先 DATABASE_URL/DATA_DIR；缺 Python≥3.10 前置检查 |
| BUG-084 | 修复 | streak 今天没记录就显示 0；连续阅读白天看报表被清零 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | stats.py 今天无日志从昨天起算 |
| BUG-085 | 修复 | reading start_date 从不写入；finished 不联动 percent=100 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | reading.py unread→reading 写 start_date；finished 置 percent/page |
| BUG-086 | 修复 | 鉴权局域网兜底路径 member_id 不存在时 ValueError 未捕获返回 500 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | auth.enforce_channel_member 捕获 ValueError→400 |
| BUG-087 | 修复 | 上传端点无大小限制且全量读内存，超大 POST 可耗尽内存 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | utils/uploads.read_upload_limited 10MB；attachments/intake/recognize 接入 |
| BUG-088 | 修复 | CLI 网络异常未捕获，API 宕机时除 health 外抛完整 traceback | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | client._request 捕获 httpx.Timeout/HTTPError→友好 RuntimeError |
| BUG-089 | 修复 | backup.sh tar 失败被静默吞掉，归档缺失仍报备份完成 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | 目录缺失跳过并警告；tar 失败 exit 1 |
| BUG-090 | 修复 | ISBN-10 单独 PATCH 不派生 ISBN-13；手工位数错误 ISBN 静默丢弃；intake 优先元数据 ISBN | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | books.update 派生 isbn13；intake 位数错误报错；_resolve_isbn_fields 优先扫描 ISBN |
| BUG-091 | 修复 | 元数据链无总超时；BookOut 漏字段；health 503 时 ok 仍 true；笔记空白/未来 log_date/session 反序；by_status 漏 unread；购买消息硬编码货币；封面可覆盖；intake 回滚孤儿封面；CORS 未配置 | 2026-08-09 03:28 | 2026-08-09 03:42 | 已修复 | chain 12s；BookOut+serializers；health ok=false；note/reading_log 校验；stats unread；purchase/CLI currency；storage 不覆盖；intake 清封面；main CORS |
| BUG-092 | 修复 | async 写端点 /books/intake 与 /attachments 把请求作用域 SQLAlchemy Session 直接传入 run_in_threadpool，同一 Session 跨线程复用；在线上并发下可能出现不稳定事务/连接行为。 | 2026-08-09 03:49 | 2026-08-09 03:49 | 已修复 | api/v1/intake.py、api/v1/attachments.py；改为线程内用 app.db.SessionLocal 自建会话，成员解析/业务写入/operation log 全在线程内完成，不再跨线程传递请求 db；新增 pytest：test_bug068_intake_threadpool_opens_own_session、test_bug068_attachment_threadpool_opens_own_session |
| BUG-093 | 修复 | PATCH /books/{id} 仅更新 isbn10 时，等价 isbn13 不会统一校验，可能留下逻辑重复书目或 isbn10/isbn13 脏组合。 | 2026-08-09 03:49 | 2026-08-09 03:49 | 已修复 | services/books.py；更新流程统一规范化 isbn10/isbn13，校验二者一致性，并对其他书目的等价 ISBN 冲突做预检查；新增 pytest：test_bug069_patch_isbn10_backfills_isbn13、test_bug069_patch_isbn10_conflict_returns_409 |
| BUG-094 | 修复 | normalize_title 归一化过弱：book_helpers.py 仅 strip+lower，多空格/标点/全角不归一，同书易重复入库（第八轮报告原拟 BUG-073，台账 BUG-073/074/084 均记成 streak 且已修） | 2026-08-09 03:51 | 2026-08-09 11:40 | 已修复 | normalize_title 改为 NFKC 全/半角统一 + 去标点(Unicode category P) + 折叠空白 + 小写；实测 'Harry Potter'/'Harry  Potter'/'Harry Potter.'/'Harry, Potter!'/'Ｈａｒｒｙ Ｐｏｔｔｅｒ' 全部归一为 'harry potter'。新增 test_bug094_normalize_title.py（4 用例）。全量 pytest 40 passed、compileall 通过 |

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
| CHK-016 | 检查 | 全项目审查（业务流程+bug）：后端服务层/API层/模型迁移一致性/CLI契约/skills/deploy 四路并行核查 + pytest 13 项全过；新发现 1 高（Dockerfile COPY 目录平铺导致 compose 无法启动）、9 中（Google Books ISBN 校验死代码、NLC 不校验 ISBN、streak 今日边界、start_date 从不写入、鉴权 ValueError 500、上传无大小限制、CLI 网络异常未捕获、backup.sh tar 失败静默、systemd 迁移库路径不一致等）、若干低；详见会话结论 | 2026-08-09 03:25 | 2026-08-09 03:25 | 已完成 | 审查报告见会话；待修复项建议按高→中优先级开 BUG 条目 |
| CHK-017 | 检查 | 第二独立验证器复核 3 个候选问题（线程池 Session 传递、PATCH ISBN 等价冲突、CLI 渠道头契约） | 2026-08-09 03:29 | 2026-08-09 03:29 | 已完成 | 结论：候选 1/2 确认存在且为中高优先级问题；候选 3 与既有 CLI/Agent 设计契约一致，判定为误报。未修改业务代码。 |
| CHK-018 | 检查 | 第七轮全项目完整性与 bug 检查：基线验证（compileall/pip check/空库 alembic upgrade+check/pytest 13 项全过）+ 5 维度并行审查（API 与鉴权/services 与 utils/models 与迁移/CLI/docs 与 deploy），关键发现均代码核实或 TestClient 复现 | 2026-08-09 03:28 | 2026-08-09 03:30 | 已完成 | 发现 BUG-050~067（18 项：高危 3=附件无鉴权/SSRF 重定向绕过/CLI 网络异常裸 traceback，中危 6，低危 9）+OPR-002（脚本不装 pytest）+DOC-017（README/cli-reference/env.example 三处不一致）；统计摘要旧数据随写入自动重算修正。潜在项备忘：attachments/custom_fields 多态引用无删除清理路径（现无 DELETE 端点）、channel_bindings 唯一性仅应用层、无 CORS 中间件（无浏览器前端前无影响） |
| CHK-019 | 检查 | 第二独立验证器复核候选问题4：受保护写接口在 try/except 前调用 enforce_channel_member，非法 member_id 触发 500 | 2026-08-09 03:29 | 2026-08-09 03:29 | 已完成 | 确认真实存在：enforce_channel_member 无渠道头时调用 resolve_member_id；不存在成员抛 ValueError，而 progress/notes/purchases/reading_logs/intake/intake_json 均在 try 外调用，导致未处理 500。主代理 TestClient 复现与静态代码一致。未修改业务代码。 |
| CHK-020 | 检查 | 第八轮 bug 修复验证：逐一核查 BUG-050~094 是否存在/已修复。首次修复 9 项（BUG-054/056/057/059/061/062/065/072/079）+OPR-002+DOC-017；经独立验证器复核发现 BUG-054（匿名仍可写 copies/custom-fields/members）和 BUG-059（ValueError 未捕获致 500）未真正收口，二次修复：auth.py 加 require_channel 参数、members 白名单建立后拒匿名、recognize.py 补 ValueError 捕获。最终 compileall 通过、pytest 104 passed | 2026-08-09 03:28 | 2026-08-09 04:20 | 已完成 | 首次结论失真已修正：BUG-054/059 经二次修复确认收口；pytest 实际 104 passed（非首次报告的 26） |
| CHK-021 | 检查 | 复核第八轮 BUG-069~079 是否仍存在/已修复（代码实跑） | 2026-08-09 03:51 | 2026-08-09 03:51 | 已完成 | 结论：069-072、074-079 已修复；报告中的 BUG-073(normalize_title) 台账错位为 streak，弱归一化见 BUG-080 或后续条目；BUG-073 备注已注明错位 |
| CHK-022 | 检查 | 复核设计方案合理性评估（v1.3 + 审查结论）并尝试检索同类开源项目 | 2026-08-09 04:12 | 2026-08-09 04:12 | 已完成 | 评估总体同意；补充 nuance：CLI 文档漂移、进度 copy 维度、CHK-001 未闭环属实；Docker/元数据超时部分已有后续修复需核对。开源检索因本机未装 parallel-cli 未完成，待 /parallel-setup 后补 |

## 测试数据

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| TST-001 | 检查 | 补齐后端/CLI 自动化回归测试，覆盖空库初始化、入库去重、购买详情、附件/自定义字段完整性、日期/ISBN 边界和鉴权 | 2026-07-11 19:35 | 2026-08-09 12:20 | 已完成 | 新增 6 测试文件 64 测试函数：test_purchase_detail.py(10 购买详情+original_price 往返+日期默认+货币消息)、test_custom_fields.py(7 upsert 插入/更新/实体校验)、test_intake_dedup.py(6 无 ISBN 归一化去重+ISBN-10↔13 互查)、test_attachment_entity_validation.py(7 book/member/note/copy 实体校验+link/markdown 内容校验)、test_isbn_unit.py(15 normalize/isbn10→13/is_valid/lookup_keys 单元)、test_reading_log_date.py(13 log_date 必填/合法/未来拒绝/pages/session 校验)。新增 pytest.ini 配置(testpaths+filterwarnings)。8 覆盖区域从 2 空白+5 部分+1 全覆盖 提升到全覆盖。全量 pytest 104 passed(原40+新64)、compileall 无错误。OPR-002 install --dev 已覆盖 pytest 安装路径 |

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
| DOC-017 | 文档 | 文档三处不一致：README 中/英项目结构图把 alembic/ 画在 app/ 下（实际在 backend/ 顶层）；docs/cli-reference.md 的 purchase 命令漏列 --notes 选项；deploy/.env.example 缺 SETUP_TOKEN（backend/.env.example 有） | 2026-08-09 03:28 | 2026-08-09 04:20 | 已完成 | README.md:34/:129、docs/cli-reference.md:59-65、deploy/.env.example；；本会话修复：README 中英项目结构图 alembic/ 移至 backend/ 顶层；cli-reference.md purchase 补 --notes；deploy/.env.example 补 SETUP_TOKEN |
| DOC-018 | 文档 | 核对并同步 docs/ 与 skills/ 中已过期的 CLI/Agent/鉴权说明：CLI 现在会自动透传 BOOKSHELF_CHANNEL/BOOKSHELF_EXTERNAL_USER_ID/BOOKSHELF_SETUP_TOKEN，doctor/health 错误语义与受保护写接口范围也需对齐当前实现。 | 2026-08-09 03:56 | 2026-08-09 03:56 | 已完成 | 已更新 docs/cli-reference.md、agent-setup.md、get-started.md、user-guide.md、faq.md、deployment.md；skills/README.md、bookshelf-setup/SKILL.md、reading-tracker/SKILL.md。重点同步：CLI 自动注入鉴权头、Agent+CLI 环境变量配置、doctor 失败退出码/中文错误、受保护写接口覆盖范围、backup.sh 跳过附件包的行为说明；task-list check 通过 |

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
| OPR-002 | 运维 | backend/install.sh 与 install.bat 只安装 requirements.txt，requirements-dev.txt（pytest）不被任何安装脚本覆盖，按脚本装出的 venv 无法运行回归测试（本轮检查时 venv 缺 pytest，补装后 13 项全过） | 2026-08-09 03:28 | 2026-08-09 04:20 | 已完成 | backend/install.sh:33、install.bat:43；加 --dev 分支或追加安装 requirements-dev.txt，关联 TST-001；；本会话修复：install.sh 加 --dev/DEV_DEPS 分支安装 requirements-dev.txt，install.bat 对应对齐 |
| OPS-001 | 优化 | 优化 .gitignore：新增 .zcode/、.claude/、tsconfig.tsbuildinfo、tests/_tmp/、*.log/*.tmp/*.bak/*.swp/*.swo/*~/Thumbs.db、*.sqlite/*.sqlite3、.coverage.*/*.cover 等忽略规则，防止缓存和临时文件上传 GitHub | 2026-08-09 04:25 | 2026-08-09 04:25 | 已完成 | - |

## 规划事项

| ID | 动作 | 事项 | 发现时间 | 完成时间 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| PLN-001 | 规划 | 二期：局域网数字书架 Web UI（封面墙/筛选/详情页）——Vue 3 SPA | 2026-06-26 16:45 | 2026-08-09 13:10 | 已完成 | 架构决策：Vue 3 + Vite + TypeScript SPA + Pinia + Vue Router；后端新增专用 FileResponse 路由(GET /api/v1/files/covers/{path}、/attachments/{path})含路径穿越防护。5 工作包全部完成：WP1 files.py+5测试、WP2 脚手架(types/stores/router/CSS)、WP3 封面墙(BookshelfView 网格+筛选栏+无限滚动+封面占位符)、WP4 详情页(BookDetailView 6 Tab+改进度+加笔记)、WP5 统计页(StatsView 数字卡片+分类条形图+成员统计)+成员选择器+docs/web-ui.md。前端 npm run build 通过(TS 严格模式无错误)，后端 pytest 109 passed。写操作走 body.member_id+可信局域网兜底。范围不含入库/删除/在线阅读/概览图导出 |
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
| 代码 Bug | 94 | 94 | 0 | 100% |
| 调整事项 | 3 | 3 | 0 | 100% |
| 检查事项 | 22 | 21 | 1 | 95% |
| 测试数据 | 1 | 1 | 0 | 100% |
| 文档维护 | 18 | 18 | 0 | 100% |
| 功能开发 | 12 | 12 | 0 | 100% |
| 配置运维 | 3 | 3 | 0 | 100% |
| 规划事项 | 4 | 1 | 3 | 25% |
| 优化事项 | 4 | 4 | 0 | 100% |
| 调研事项 | 3 | 3 | 0 | 100% |
| **总计** | 164 | 160 | 4 | 98% |
