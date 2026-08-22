#!/usr/bin/env bash
# 家庭书架端到端自测脚本（lwa staging 实例）
#
# 用法:
#   bash scripts/e2e-selftest.sh [选项] [phase]
#
#   phase ∈ A|B|C|D|all（默认 all）
#     A — 基础面（版本/SPA/发现面/默认关闭）
#     B — 权限阶段 2（登录/隔离/停用/改密）
#     C — 阶段 1+4（匿名书架/逐书可见性/B 模式）
#     D — Agent Grant/版本绑定/限流/MCP
#
#   选项:
#     --port N        实例端口（默认 18003）
#     --report FILE   输出 JSON 报告到 FILE
#     --lwa-id ID     lwa 实例 ID（默认 staging，用于 env 翻转与清理）
#     --setup-token T SETUP_TOKEN 值（默认 staging-setup-token-2026）
#     --keep          保留实例不删除（默认跑完自动清理）
#     --help          帮助
#
# 清理行为:
#   测试结束后脚本默认自动清理临时实例（lwa stop + 删除 apps/<id> + docker rm）。
#   如需保留实例供人工检查，加 --keep。
#   注意：清理会删除实例的全部数据（data/ 目录），不可恢复。
#
# 前置条件:
#   1. 已用 lwa 部署一个独立实例（参见 docs/deployment.md「staging 自测」节）
#   2. 实例的 docker/.env.local 含 SETUP_TOKEN（值与 --setup-token 一致）
#   3. 实例数据库为空（首次部署或已清空 data/）
#   4. 本机有 curl + jq + python3
# 注意: B/C/D 套件依赖前一阶段的会话 Cookie 和数据，建议 always 用 all 全跑。
#       脚本会自动归一化环境变量（清除模式/MCP 键），结束时恢复默认关闭态。
#       默认测试完成后自动清理实例；加 --keep 保留。
set -u

# ── 参数解析 ──
PORT=18003
LWA_ID=staging
SETUP_TOKEN=staging-setup-token-2026
REPORT_FILE=""
KEEP_INSTANCE=false
PHASE=all

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --report) REPORT_FILE="$2"; shift 2 ;;
    --lwa-id) LWA_ID="$2"; shift 2 ;;
    --setup-token) SETUP_TOKEN="$2"; shift 2 ;;
    --keep) KEEP_INSTANCE=true; shift ;;
    --help|-h)
      grep '^#' "$0" | head -35; exit 0 ;;
    A|B|C|D|all) PHASE="$1"; shift ;;
    *) echo "未知参数: $1（--help 查看用法）"; exit 1 ;;
  esac
done

BASE="http://127.0.0.1:${PORT}"
JA=/tmp/hbs-e2e-owner.jar; JB=/tmp/hbs-e2e-member.jar
rm -f "$JA" "$JB"

# lwa 工作区路径（env 翻转用）
LWA_RUNTIME="${LWA_RUNTIME:-$HOME/Documents/VSCode/1-AI-Coding/6-自制小工具/1-home-server/2-本地简单网页部署基座/local-webpage-access/runtime}"
LWA_DOCKER_DIR="${LWA_RUNTIME}/apps/${LWA_ID}/docker"

PASS=0; FAIL=0; FAILED=()
START_TS=$(date +%s)

t() { echo "▸ $1"; }
ok() { echo "  PASS ✓ $1"; PASS=$((PASS+1)); }
no() { echo "  FAIL ✗ $1"; FAIL=$((FAIL+1)); FAILED+=("$1"); }

jassert() { local desc="$1" body="$2" expr="$3"
  if echo "$body" | jq -e "$expr" >/dev/null 2>&1; then ok "$desc"; else no "$desc — body: $(echo "$body" | head -c 200)"; fi }
has() { local desc="$1" body="$2" expr="$3"
  if echo "$body" | jq -e "$expr" >/dev/null 2>&1; then no "$desc"; else ok "$desc"; fi }
sassert() { local desc="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then ok "$desc"; else no "$desc — got:$got want:$want"; fi }

wait_up() {
  for _ in $(seq 1 40); do
    curl -sf --max-time 3 "${BASE}/api/v1/public-health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

flip_env() {
  if [ ! -d "$LWA_DOCKER_DIR" ]; then
    echo "错误：lwa 实例目录不存在 $LWA_DOCKER_DIR（用 --lwa-id 指定正确 ID）" >&2
    exit 1
  fi
  python3 - "$LWA_DOCKER_DIR/.env" "$@" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
lines = p.read_text().splitlines(); kv = {}
for l in lines:
    if l and not l.startswith("#") and "=" in l:
        k, _, v = l.partition("="); kv[k] = v
for arg in sys.argv[2:]:
    if "=" in arg:
        k, _, v = arg.partition("=")
        if v == "__DEL__":
            kv.pop(k, None)
        else:
            kv[k] = v
header = [l for l in lines if l.startswith("#")]
p.write_text("\n".join(header + [f"{k}={v}" for k, v in kv.items()]) + "\n")
PY
  (cd "$LWA_DOCKER_DIR" && docker compose up -d --force-recreate >/dev/null 2>&1)
  wait_up || { echo "错误：实例 env 翻转后未恢复"; exit 1; }
  echo "  (env 已应用: $*)"
}

# ── 报告生成 ──
write_report() {
  [ -z "$REPORT_FILE" ] && return
  END_TS=$(date +%s); DURATION=$((END_TS - START_TS))
  python3 - "$REPORT_FILE" "$PASS" "$FAIL" "$DURATION" "$PHASE" "${FAILED[*]:-}" <<'PY'
import sys, json, datetime
path, p, f, dur, phase, failed = sys.argv[1:7]
report = {
    "timestamp": datetime.datetime.now().isoformat(),
    "phase": phase,
    "summary": {"pass": int(p), "fail": int(f), "duration_seconds": int(dur),
                "result": "PASS" if int(f) == 0 else "FAIL"},
    "failed_items": failed.split() if failed else [],
}
with open(path, "w") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=2)
print(f"报告已写入 {path}")
PY
}
trap cleanup_and_report EXIT

# ── 实例清理（默认执行；--keep 跳过） ──
cleanup_instance() {
  if [ "$KEEP_INSTANCE" = "true" ]; then
    echo "── 实例已保留（--keep）: ${LWA_ID} @ ${BASE} ──"
    echo "   手动清理: lwa stop ${LWA_ID} && lwa remove ${LWA_ID} --yes"
    echo "             rm -rf ${LWA_RUNTIME}/apps/${LWA_ID}"
    echo "             docker rmi lwa-${LWA_ID}-app:latest 2>/dev/null"
    return
  fi
  echo "── 清理临时实例 ${LWA_ID} ──"
  # 1. 停止容器
  (cd "$LWA_RUNTIME" && lwa stop "$LWA_ID" >/dev/null 2>&1) || \
    (cd "$LWA_DOCKER_DIR" 2>/dev/null && docker compose down >/dev/null 2>&1) || true
  # 2. 移除 lwa registry 条目（lwa remove 只删索引、保留文件）
  (cd "$LWA_RUNTIME" && lwa remove "$LWA_ID" --yes >/dev/null 2>&1) || true
  # 3. 删除残留容器
  docker rm -f "lwa-${LWA_ID}" >/dev/null 2>&1 || true
  # 4. 删除构建镜像（compose 项目名为 lwa-<id>，镜像名 lwa-<id>-app）
  docker rmi "lwa-${LWA_ID}-app:latest" >/dev/null 2>&1 || true
  # 5. 删除实例目录（含数据与日志）
  rm -rf "${LWA_RUNTIME}/apps/${LWA_ID}"
  # 6. 清理本脚本产生的临时 cookie
  rm -f "$JA" "$JB"
  echo "   已清理: registry + 容器 + 镜像 + ${LWA_RUNTIME}/apps/${LWA_ID} + cookie"
}

cleanup_and_report() {
  cleanup_instance
  write_report
}

echo "════════ 家庭书架端到端自测 ════════"
echo "  目标: ${BASE}  实例: ${LWA_ID}  阶段: ${PHASE}"

# ═══════════ A. 基础面 ═══════════
if [ "$PHASE" = "A" ] || [ "$PHASE" = "all" ]; then
echo "── A. 基础面 ──"
flip_env ANONYMOUS_CATALOG_MODE=__DEL__ TRUSTED_LAN_CIDRS=__DEL__ \
  MCP_ENABLED=__DEL__ MCP_CURSOR_SIGNING_SECRET=__DEL__ MCP_TRUSTED_CIDRS=__DEL__ MCP_REQUIRE_HTTPS=__DEL__

t "A1 public-health"
B=$(curl -s "$BASE/api/v1/public-health")
jassert "A1 ok=true" "$B" '.ok==true'
APP_VER=$(echo "$B" | jq -r '.data.app_version')
jassert "A1 app_version=${APP_VER}" "$B" ".data.app_version == \"${APP_VER}\""
jassert "A1 前后端版本一致" "$B" '.data.app_version == .data.frontend_version'

t "A2 SPA 托管"
H=$(curl -s "$BASE/")
echo "$H" | grep -q "家庭书架" && ok "A2 首页 HTML 含应用标题" || no "A2 首页 HTML 缺标题"
ASSET=$(echo "$H" | grep -o 'src="[^"]*\.js"' | head -1 | sed 's/src="//;s/"//')
if [ -n "$ASSET" ]; then
  C=$(curl -s -o /dev/null -w '%{http_code}' "$BASE$ASSET"); sassert "A2 首页 JS 可加载" "$C" "200"
else
  no "A2 未找到 JS 资源引用"
fi

t "A3 Agent 发现面"
B=$(curl -s "$BASE/agent/manifest.json"); jassert "A3 manifest service.id" "$B" '.service.id=="home-bookshelf"'
B=$(curl -s "$BASE/agent/skills/index.json"); jassert "A3 skills 索引非空" "$B" '(.skills|length) > 0'

t "A4 默认关闭面"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/mcp" -H 'Content-Type: application/json' -d '{}')
sassert "A4 /mcp 默认 404" "$C" "404"
B=$(curl -s "$BASE/api/v1/public-catalog/books"); jassert "A4 匿名目录 disabled" "$B" '.error=="ANONYMOUS_CATALOG_DISABLED"'
fi

# ═══════════ B. 权限阶段 2 ═══════════
if [ "$PHASE" = "B" ] || [ "$PHASE" = "all" ]; then
echo "── B. 引导与权限阶段 2 ──"

t "B0 空库引导"
B=$(curl -s -X POST "$BASE/api/v1/members" -H 'Content-Type: application/json' -d '{"name":"E2E主人","role":"owner"}')
jassert "B0 创建 owner" "$B" '.data.role=="owner"'

t "B1 init-password 保护"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/auth/init-password" -H 'Content-Type: application/json' -d '{"password":"e2e-owner-1","confirm":"e2e-owner-1"}')
[ "$C" != "200" ] && ok "B1 无 token 被拒($C)" || no "B1 无 token 通过($C)"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/auth/init-password" -H "X-Setup-Token: $SETUP_TOKEN" -H 'Content-Type: application/json' -d '{"password":"e2e-owner-1","confirm":"e2e-owner-1"}')
sassert "B1 携 token 初始化" "$C" "200"

t "B2 Owner 登录"
B=$(curl -s -c "$JA" -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d '{"password":"e2e-owner-1"}')
jassert "B2 登录 role=owner" "$B" '.authenticated==true and .role=="owner"'
B=$(curl -s -b "$JA" "$BASE/auth/session"); jassert "B2 会话有效" "$B" '.authenticated==true'

t "B3 CI 唯一用户名"
B=$(curl -s -b "$JA" -X POST "$BASE/api/v1/members" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"name":"张三","role":"member","username":"zhang"}')
jassert "B3 创建 zhang" "$B" '.data.username=="zhang"'
MID=$(echo "$B" | jq -r '.data.id')
C=$(curl -s -o /dev/null -w '%{http_code}' -b "$JA" -X POST "$BASE/api/v1/members" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"name":"李四","username":"ZHANG"}')
sassert "B3 CI 变体 409" "$C" "409"

t "B4 成员密码与登录"
B=$(curl -s -b "$JA" -X POST "$BASE/api/v1/members/$MID/password" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"password":"e2e-member-1"}')
jassert "B4 设置密码" "$B" '.data.username=="zhang"'
B=$(curl -s -c "$JB" -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d '{"username":"zhang","password":"e2e-member-1"}')
jassert "B4 成员登录" "$B" '.role=="member"'

t "B4.5 绑定渠道"
OID=$(curl -s -b "$JA" "$BASE/auth/session" | jq -r '.member_id')
B=$(curl -s -b "$JA" -X POST "$BASE/api/v1/members/bind" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d "{\"member_id\":$OID,\"channel\":\"feishu\",\"external_user_id\":\"ou-e2e\"}")
jassert "B4.5 绑定" "$B" '.ok==true'

t "B5 L3 隔离"
B=$(curl -s -b "$JA" -X POST "$BASE/api/v1/books" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"title":"E2E书","category":"测试"}')
BID=$(echo "$B" | jq -r '.data.id')
curl -s -b "$JA" -X POST "$BASE/api/v1/books/$BID/notes" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"content_md":"OWNER_NOTE"}' >/dev/null
curl -s -b "$JB" -X POST "$BASE/api/v1/books/$BID/notes" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"content_md":"MEMBER_NOTE"}' >/dev/null
curl -s -b "$JB" -X POST "$BASE/api/v1/books/$BID/purchases" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"price":5}' >/dev/null
curl -s -b "$JA" -X POST "$BASE/api/v1/books/$BID/purchases" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"price":100}' >/dev/null
B=$(curl -s -b "$JB" "$BASE/api/v1/books/$BID")
has "B5 成员看不到 Owner 笔记" "$B" '.. | strings | select(.=="OWNER_NOTE")'
jassert "B5 成员看到自己笔记" "$B" '.. | strings | select(.=="MEMBER_NOTE")'
jassert "B5 详情只含本人购买" "$B" '([.data.purchase_records[]? | select(.price==100)] | length) == 0'
B=$(curl -s -b "$JB" "$BASE/api/v1/stats")
jassert "B5 统计仅本人" "$B" '.data.total_spent==5'

t "B6 管理端点 owner-only"
C=$(curl -s -o /dev/null -w '%{http_code}' -b "$JB" -X PATCH "$BASE/api/v1/books/$BID/visibility" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"visibility":"public"}')
[ "$C" = "403" ] || [ "$C" = "401" ]; sassert "B6 改可见性被拒($C)" "$?" "0"
C=$(curl -s -o /dev/null -w '%{http_code}' -b "$JB" -X POST "$BASE/api/v1/members" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"name":"x","role":"member"}')
[ "$C" = "403" ]; sassert "B6 建成员被拒($C)" "$?" "0"

t "B7 停用→恢复"
curl -s -b "$JA" -X PATCH "$BASE/api/v1/members/$MID" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"disabled":true}' >/dev/null
B=$(curl -s -b "$JB" "$BASE/auth/session"); jassert "B7 旧会话失效" "$B" '.authenticated==false'
B=$(curl -s -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d '{"username":"zhang","password":"e2e-member-1"}')
jassert "B7 登录被拒" "$B" '.detail | tostring | contains("停用")'
curl -s -b "$JA" -X PATCH "$BASE/api/v1/members/$MID" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"disabled":false}' >/dev/null
B=$(curl -s -c "$JB" -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d '{"username":"zhang","password":"e2e-member-1"}')
jassert "B7 恢复后可登录" "$B" '.authenticated==true'

t "B8 重置密码撤会话"
curl -s -b "$JA" -X POST "$BASE/api/v1/members/$MID/password" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"password":"e2e-member-2"}' >/dev/null
B=$(curl -s -b "$JB" "$BASE/auth/session"); jassert "B8 旧会话失效" "$B" '.authenticated==false'

t "B9 自助改密"
B=$(curl -s -b "$JA" -X POST "$BASE/auth/change-password" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"old_password":"e2e-owner-1","new_password":"e2e-owner-2","confirm":"e2e-owner-2"}')
jassert "B9 改密成功" "$B" '.ok==true'
B=$(curl -s -b "$JA" "$BASE/auth/session"); jassert "B9 当前会话有效" "$B" '.authenticated==true'
fi

# ═══════════ C. 阶段 1+4 ═══════════
if [ "$PHASE" = "C" ] || [ "$PHASE" = "all" ]; then
echo "── C. 匿名书架与逐书可见性 ──"
flip_env ANONYMOUS_CATALOG_MODE=lan_shared TRUSTED_LAN_CIDRS="192.168.0.0/16,172.16.0.0/12"

t "C1 C 模式"
B=$(curl -s "$BASE/api/v1/public-catalog/books")
jassert "C1 匿名列表 200" "$B" '.ok==true'
jassert "C1 字段白名单" "$B" '(.data.items[0] | keys | all(. as $k | ["id","title","subtitle","authors","translators","publisher","publish_date","edition","language","page_count","category","summary","cover_thumbnail_url","public_tags","availability_status"] | index($k)))'
B=$(curl -s -H 'X-Forwarded-For: 8.8.8.8' "$BASE/api/v1/public-catalog/books")
jassert "C1 伪造 XFF 被拒" "$B" '.error=="LAN_REQUIRED"'

t "C2 逐书可见性"
curl -s -b "$JA" -X POST "$BASE/api/v1/books" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"title":"C2-legacy","category":"历史"}' >/dev/null
curl -s -b "$JA" -X POST "$BASE/api/v1/books" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"title":"C2-mo","category":"童话"}' >/dev/null
curl -s -b "$JA" -X POST "$BASE/api/v1/books" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"title":"C2-priv","category":"其他"}' >/dev/null
IDS=$(curl -s -b "$JA" "$BASE/api/v1/books?limit=100" | jq -r '.data.items[] | "\(.id) \(.title)"')
ID_PUB=$(echo "$IDS" | awk '/E2E书/{print $1}')
ID_MO=$(echo "$IDS" | awk '/C2-mo/{print $1}')
ID_PRIV=$(echo "$IDS" | awk '/C2-priv/{print $1}')
ID_LEG=$(echo "$IDS" | awk '/C2-legacy/{print $1}')
curl -s -b "$JA" -X PATCH "$BASE/api/v1/books/$ID_PUB/visibility" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"visibility":"public"}' >/dev/null
curl -s -b "$JA" -X PATCH "$BASE/api/v1/books/$ID_MO/visibility" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"visibility":"members_only"}' >/dev/null
curl -s -b "$JA" -X PATCH "$BASE/api/v1/books/$ID_PRIV/visibility" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"visibility":"private"}' >/dev/null
B=$(curl -s "$BASE/api/v1/public-catalog/books?limit=100")
T=$(echo "$B" | jq -r '[.data.items[].title] | join(",")')
echo "$T" | grep -q "E2E书" && echo "$T" | grep -q "C2-legacy" && ok "C2 lan_shared 见 public+legacy" || no "C2 可见集: $T"
echo "$T" | grep -q "C2-mo" && no "C2 members_only 泄露" || ok "C2 members_only 不可见"
echo "$T" | grep -q "C2-priv" && no "C2 private 泄露" || ok "C2 private 不可见"

t "C3 C→B 预览"
B=$(curl -s -b "$JA" "$BASE/api/v1/catalog-visibility/preview")
jassert "C3 remain" "$B" '.data.summary.remain_public==1'
jassert "C3 disappear" "$B" '.data.summary.disappear_from_anonymous==1'
jassert "C3 never" "$B" '.data.summary.never_anonymous==2'

t "C4 B 模式"
flip_env ANONYMOUS_CATALOG_MODE=explicit_public
B=$(curl -s "$BASE/api/v1/public-catalog/books?limit=100")
T=$(echo "$B" | jq -r '[.data.items[].title] | join(",")')
echo "$T" | grep -q "E2E书" && ! echo "$T" | grep -q "C2-legacy" && ok "C4 仅 public 可见" || no "C4 可见集: $T"
C=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/public-catalog/books/$ID_LEG"); sassert "C4 不可见 404" "$C" "404"
C=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/public-catalog/covers/$ID_LEG"); sassert "C4 封面 404" "$C" "404"

t "C5 回滚"
flip_env ANONYMOUS_CATALOG_MODE=lan_shared
B=$(curl -s "$BASE/api/v1/public-catalog/books?limit=100")
T=$(echo "$B" | jq -r '[.data.items[].title] | join(",")')
echo "$T" | grep -q "C2-legacy" && ok "C5 回滚后 legacy 恢复" || no "C5 回滚后: $T"
B=$(curl -s -b "$JA" "$BASE/api/v1/books/$ID_LEG")
jassert "C5 数据未改写" "$B" '.data.catalog_visibility==null'
fi

# ═══════════ D. Agent / MCP ═══════════
if [ "$PHASE" = "D" ] || [ "$PHASE" = "all" ]; then
echo "── D. Agent Grant / 版本绑定 / 限流 / MCP ──"

t "D1 试点 Grant"
B=$(curl -s -b "$JA" -X POST "$BASE/agent-access/clients" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"display_name":"E2E-Agent"}')
CID=$(echo "$B" | jq -r '.id')
OID=$(curl -s -b "$JA" "$BASE/auth/session" | jq -r '.member_id')
B=$(curl -s -b "$JA" -X POST "$BASE/agent-access/grants" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d "{\"agent_client_id\":$CID,\"member_id\":$OID,\"scopes\":[\"books:read\"],\"data_scope\":\"household_shared\"}")
GID=$(echo "$B" | jq -r '.id')
B=$(curl -s -b "$JA" -X POST "$BASE/agent-access/tokens" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d "{\"grant_id\":$GID}")
TOK=$(echo "$B" | jq -r '.token')
C=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/books" -H "Authorization: Bearer $TOK")
sassert "D1 Bearer 读书目" "$C" "200"

t "D2 Grant 版本绑定"
curl -s -b "$JA" -X PATCH "$BASE/agent-access/grants/$GID" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"scopes":["books:read","notes:read"]}' >/dev/null
C=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/books" -H "Authorization: Bearer $TOK")
sassert "D2 缩权后旧 Token 401" "$C" "401"

t "D3 登录防爆破"
CODES=""
for i in $(seq 1 12); do
  C=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d '{"username":"zhang","password":"wrong"}')
  CODES="${CODES}${C} "; sleep 0.3
done
echo "${CODES}" | grep -q "429" && ok "D3 触发 429" || no "D3 未触发: ${CODES}"

t "D4 MCP 试点"
flip_env MCP_ENABLED=true \
  MCP_CURSOR_SIGNING_SECRET=e2e-cursor-secret-0123456789abcdef0123456789 \
  MCP_TRUSTED_CIDRS="192.168.0.0/16,172.16.0.0/12" MCP_REQUIRE_HTTPS=false
curl -s -b "$JA" -X PATCH "$BASE/agent-access/grants/$GID" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d '{"scopes":["books:read"]}' >/dev/null
B=$(curl -s -b "$JA" -X POST "$BASE/agent-access/tokens" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json' -d "{\"grant_id\":$GID}")
TOK=$(echo "$B" | jq -r '.token')
jassert "D4 重签 Token" "$B" '.token | startswith("hbs_at_")'
R=$(curl -s -X POST "$BASE/mcp" -H "Authorization: Bearer $TOK" -H 'MCP-Protocol-Version: 2026-07-28' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{}}}')
echo "$R" | jq -e '.result.supportedVersions' >/dev/null 2>&1 && ok "D4 discover" || no "D4 discover: $(echo "$R" | head -c 120)"
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/mcp" -H 'MCP-Protocol-Version: 2026-07-28' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{}}}')
sassert "D4 无 Token 401" "$C" "401"
R=$(curl -s -X POST "$BASE/mcp" -H "Authorization: Bearer $TOK" -H 'Mcp-Method: tools/call' -H 'Mcp-Name: bookshelf_search_books' -H 'MCP-Protocol-Version: 2026-07-28' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"_meta":{},"name":"bookshelf_search_books","arguments":{"query":"E2E","limit":5}}}')
echo "$R" | jq -e '.result.structuredContent.count >= 1' >/dev/null 2>&1 && ok "D4 MCP 搜索" || no "D4 MCP 搜索: $(echo "$R" | head -c 120)"

t "D5 MCP 关闭"
flip_env MCP_ENABLED=__DEL__ MCP_CURSOR_SIGNING_SECRET=__DEL__ MCP_TRUSTED_CIDRS=__DEL__ MCP_REQUIRE_HTTPS=__DEL__
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/mcp" -H 'Content-Type: application/json' -d '{}')
sassert "D5 /mcp 404" "$C" "404"
fi

echo "════════ 结果：PASS=$PASS FAIL=$FAIL ════════"
if [ "${#FAILED[@]:-0}" -gt 0 ] 2>/dev/null; then printf '失败项: %s\n' "${FAILED[*]:-}"; fi
# trap cleanup_and_report EXIT 会自动清理实例并写报告
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
