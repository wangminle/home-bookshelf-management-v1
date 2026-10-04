#!/usr/bin/env bash
# 若 python3 不可用（如 Windows 微软商店桩，exit 非零且无输出），回退到 python。
if python3 -c 'import sys' >/dev/null 2>&1; then PY=python3; else PY=python; fi
input="$(cat)"
session_id="$(printf '%s' "$input" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("session_id") or "default")' 2>/dev/null || echo default)"
guard="/tmp/claude-tasklist-sync-${session_id}"
[ -f "$guard" ] && exit 0
touch "$guard"
"$PY" -c 'import json; print(json.dumps({"decision":"block","reason":"会话结束前请按 CLAUDE.md 的「会话结束任务同步」规则：若本次涉及任务完成，把新增条目与状态变更写入根目录 task-list.md 并在回复中告知用户；否则简短说明无需同步。本提醒每会话仅触发一次。"}, ensure_ascii=False))'
