#!/usr/bin/env python3
"""封面识别 eval：金标准对比 + 分档指标（方案 §4）。

vision 识别由 Agent 完成（读封面图填 predictions.json，后端不集成 LLM）；
本脚本只做确定性部分：生成 predictions 骨架、对比 golden.json、算指标。

用法：
    # 0. 生成仓库内合成测试集（封面图 + golden.json）
    python3 scripts/eval_cover_recognition.py generate

    # 1. 生成待填骨架（Agent 逐张看图后填 predicted 字段）
    python3 scripts/eval_cover_recognition.py template

    # 2. Agent 填完后对比打分，结果写 tests/eval/results-{date}.json
    python3 scripts/eval_cover_recognition.py compare

合格线（方案 §4.1）：书名 ≥ 90%，作者 ≥ 80%，书级完全正确率 ≥ 75%（batch
脚本的 --yes 自动入库门控读的就是书级指标）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = PROJECT_ROOT / "tests/eval/golden.json"
DEFAULT_PREDICTIONS = PROJECT_ROOT / "tests/eval/predictions.json"
DEFAULT_EVAL_DIR = PROJECT_ROOT / "tests/eval"

DIFFICULTIES = ("normal", "art_font", "vertical", "foreign", "blurry", "angle")
THRESHOLDS = {"title": 0.90, "author": 0.80, "book_level": 0.75}
AUTHOR_SEPARATORS = re.compile(r"[，,、;；/／&]+")
NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)

# 仓库可分发的合成测试集：公开书目字段 + 程序绘制封面，避免收录受版权保护的扫描件。
SYNTHETIC_CASES: list[dict[str, Any]] = [
    {"id": "vlm-book-001", "file": "normal_01.png", "difficulty": "normal",
     "expected": {"title": "三体", "author": "刘慈欣", "isbn": "9787536692930"}},
    {"id": "vlm-book-002", "file": "normal_02.png", "difficulty": "normal",
     "expected": {"title": "活着", "author": "余华", "isbn": None}},
    {"id": "vlm-book-003", "file": "art_font_01.png", "difficulty": "art_font",
     "expected": {"title": "坚如磐石", "author": "孙颙", "isbn": None}},
    {"id": "vlm-book-004", "file": "art_font_02.png", "difficulty": "art_font",
     "expected": {"title": "百年孤独", "author": "加西亚·马尔克斯", "isbn": None}},
    {"id": "vlm-book-005", "file": "vertical_01.png", "difficulty": "vertical",
     "expected": {"title": "边城", "author": "沈从文", "isbn": None}},
    {"id": "vlm-book-006", "file": "vertical_02.png", "difficulty": "vertical",
     "expected": {"title": "围城", "author": "钱钟书", "isbn": None}},
    {"id": "vlm-book-007", "file": "foreign_01.png", "difficulty": "foreign",
     "expected": {"title": "Sapiens", "author": "Yuval Noah Harari", "isbn": None}},
    {"id": "vlm-book-008", "file": "foreign_02.png", "difficulty": "foreign",
     "expected": {"title": "The Little Prince", "author": "Antoine de Saint-Exupéry", "isbn": None}},
    {"id": "vlm-book-009", "file": "blurry_01.png", "difficulty": "blurry",
     "expected": {"title": "红楼梦", "author": "曹雪芹", "isbn": None}},
    {"id": "vlm-book-010", "file": "blurry_02.png", "difficulty": "blurry",
     "expected": {"title": "平凡的世界", "author": "路遥", "isbn": None}},
    {"id": "vlm-book-011", "file": "angle_01.png", "difficulty": "angle",
     "expected": {"title": "小王子", "author": "圣埃克苏佩里", "isbn": None}},
    {"id": "vlm-book-012", "file": "angle_02.png", "difficulty": "angle",
     "expected": {"title": "1984", "author": "George Orwell", "isbn": "9787532743476"}},
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _die(msg: str) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


# ── 归一化与字段对比 ──

def normalize_text(value: str | None) -> str:
    """NFKC（全角→半角、Ⅲ→III）+ casefold + 去空白与中西文标点。"""
    text = unicodedata.normalize("NFKC", value or "")
    return NON_WORD.sub("", text.casefold())


def title_matches(expected: str | None, predicted: str | None) -> bool:
    return bool(expected) and bool(predicted) and normalize_text(expected) == normalize_text(predicted)


def _author_tokens(value: str | None) -> set[str]:
    """分隔符切分作者；西文再按空白切开，便于姓氏单独命中。"""
    tokens: set[str] = set()
    for part in AUTHOR_SEPARATORS.split(value or ""):
        part = part.strip()
        if not part:
            continue
        tokens.add(normalize_text(part))
        for word in part.split():
            folded = normalize_text(word)
            if folded:
                tokens.add(folded)
    return {t for t in tokens if t}


def author_matches(expected: str | None, predicted: str | None) -> bool:
    """多作者按分隔符切分，任一作者名命中即算对（封面通常只印部分作者）。"""
    exp_tokens = _author_tokens(expected)
    pred_tokens = _author_tokens(predicted)
    return bool(exp_tokens) and bool(exp_tokens & pred_tokens)


def _isbn13_from_10(isbn10: str) -> str | None:
    # X 只能出现在末位校验位；前 9 位含 X 的脏输入（如 "70200022XX"）不换算，
    # 原样返回交给比对记 miss，不让 compare 整体崩溃（CHK-056）
    if len(isbn10) != 10 or not isbn10[:9].isdigit() or isbn10[9] not in "0123456789X":
        return None
    core = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def normalize_isbn(value: str | None) -> str | None:
    digits = re.sub(r"[^0-9Xx]", "", value or "").upper()
    if len(digits) == 10:
        return _isbn13_from_10(digits) or digits
    return digits or None


def isbn_matches(expected: str | None, predicted: str | None) -> bool:
    exp, pred = normalize_isbn(expected), normalize_isbn(predicted)
    return bool(exp) and bool(pred) and exp == pred


# ── 数据读写 ──

def load_json(path: Path, kind: str) -> Any:
    if not path.exists():
        _die(f"{kind}文件不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(f"{kind}文件不是合法 JSON: {path}（{exc}）")


def _load_golden(path: Path) -> list[dict[str, Any]]:
    raw = load_json(path, "金标准")
    if not isinstance(raw, list):
        _die(f"金标准应为顶层数组: {path}")
    for item in raw:
        if not isinstance(item, dict) or "file" not in item:
            _die(f"金标准条目缺 file 字段: {item}")
        item.setdefault("expected", {})
        if item.get("difficulty") not in DIFFICULTIES:
            item["difficulty"] = "normal"
    return raw


def _load_predictions(path: Path) -> dict[str, Any]:
    raw = load_json(path, "预测")
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        _die(f"预测文件应为对象含 entries 数组: {path}")
    return raw


# ── 合成封面绘制 ──

def _pick_font(size: int) -> ImageFont.ImageFont:
    for path in CJK_FONT_CANDIDATES:
        font_path = Path(path)
        if not font_path.exists():
            continue
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: str, width: int) -> None:
    tw, _ = _text_size(draw, text, font)
    draw.text(((width - tw) // 2, y), text, font=font, fill=fill)


def _render_cover(case: dict[str, Any]) -> Image.Image:
    width, height = 420, 600
    palettes = {
        "normal": ("#1f4e79", "#f7f3e8", "#1a1a1a"),
        "art_font": ("#5c1a3a", "#ffd76a", "#5c1a3a"),
        "vertical": ("#2b2b2b", "#f2e6c9", "#2b2b2b"),
        "foreign": ("#243447", "#e8eef5", "#243447"),
        "blurry": ("#3d4f3a", "#e6ead9", "#222"),
        "angle": ("#4a3728", "#f4e4c1", "#4a3728"),
    }
    bg, fg, ink = palettes.get(case["difficulty"], palettes["normal"])
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    title = case["expected"]["title"]
    author = case["expected"]["author"]
    isbn = case["expected"].get("isbn")
    title_font = _pick_font(48 if len(title) <= 8 else 36)
    author_font = _pick_font(26)
    small_font = _pick_font(18)
    draw.rectangle((24, 24, width - 24, height - 24), outline=fg, width=4)

    difficulty = case["difficulty"]
    if difficulty == "vertical":
        chars = list(title)
        glyph_font = _pick_font(52)
        _, ch_h = _text_size(draw, chars[0], glyph_font)
        start_y = (height - len(chars) * (ch_h + 8)) // 2
        for i, ch in enumerate(chars):
            tw, _ = _text_size(draw, ch, glyph_font)
            draw.text(((width - tw) // 2, start_y + i * (ch_h + 8)), ch, font=glyph_font, fill=fg)
        _draw_centered(draw, author, height - 90, author_font, fg, width)
    elif difficulty == "art_font":
        art_font = _pick_font(54)
        x = 50
        y = 180
        for i, ch in enumerate(title):
            piece = Image.new("RGBA", (80, 90), (0, 0, 0, 0))
            ImageDraw.Draw(piece).text((8, 4), ch, font=art_font, fill=fg)
            rotated = piece.rotate((-18 if i % 2 == 0 else 14), expand=True, resample=Image.Resampling.BICUBIC)
            img.paste(rotated, (x, y + (8 if i % 2 else -8)), rotated)
            x += max(42, rotated.size[0] - 12)
        _draw_centered(draw, author, 420, author_font, fg, width)
    else:
        _draw_centered(draw, title, 210, title_font, fg, width)
        _draw_centered(draw, author, 320, author_font, fg, width)

    caption = f"{case['id']} · {difficulty}"
    _draw_centered(draw, caption, height - 48, small_font, fg, width)
    if isbn:
        _draw_centered(draw, f"ISBN {isbn}", height - 120, small_font, fg, width)

    if difficulty == "blurry":
        img = img.filter(ImageFilter.GaussianBlur(radius=2.4))
    elif difficulty == "angle":
        img = img.rotate(16, expand=True, fillcolor=ink, resample=Image.Resampling.BICUBIC)
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    return img.convert("RGB")


def _golden_entry(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": case["id"],
        "file": case["file"],
        "task": "cover_title_author",
        "difficulty": case["difficulty"],
        "expected": dict(case["expected"]),
    }


def cmd_generate(args: argparse.Namespace) -> None:
    eval_dir = Path(args.eval_dir)
    golden_path = eval_dir / "golden.json"
    covers_dir = eval_dir / "covers"
    if golden_path.exists() and not args.force:
        _die(f"金标准已存在: {golden_path}（加 --force 才覆盖；真实书封标注请另存）")
    covers_dir.mkdir(parents=True, exist_ok=True)
    golden = []
    for case in SYNTHETIC_CASES:
        image = _render_cover(case)
        image.save(covers_dir / case["file"], format="PNG")
        golden.append(_golden_entry(case))
    eval_dir.mkdir(parents=True, exist_ok=True)
    golden_path.write_text(json.dumps(golden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"合成测试集已写入: {golden_path}（{len(golden)} 条）")
    print(f"封面图目录: {covers_dir}")
    print("下一步: python3 scripts/eval_cover_recognition.py template")


# ── 子命令 ──

def cmd_template(args: argparse.Namespace) -> None:
    golden = _load_golden(Path(args.golden))
    if not golden:
        _die(f"金标准为空: {args.golden}（先按 tests/eval/README.md 标注）")
    predictions = {
        "model": None,  # Agent 填：如 claude-sonnet-4.5 / glm-4.6v
        "generated_at": None,  # Agent 填：识别完成时间
        "entries": [
            {"file": g["file"], "predicted": {"title": None, "author": None, "isbn": None}, "note": None}
            for g in golden
        ],
    }
    out = Path(args.out)
    out.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"骨架已生成: {out}（{len(golden)} 条）")
    print("Agent 按 tests/eval/vision_prompt.md 逐张读 tests/eval/covers/ 下的图，填 predicted.title/author/isbn 与 model 字段后执行 compare。")


def _metrics_for(rows: list[dict[str, Any]]) -> dict[str, Any]:
    m = {
        "n": len(rows),
        "title_evaluated": sum(1 for r in rows if r["expected"].get("title")),
        "title_correct": sum(1 for r in rows if r["checks"]["title"]),
        "author_evaluated": sum(1 for r in rows if r["expected"].get("author")),
        "author_correct": sum(1 for r in rows if r["checks"]["author"]),
        "isbn_evaluated": sum(1 for r in rows if r["expected"].get("isbn")),
        "isbn_correct": sum(1 for r in rows if r["checks"]["isbn"]),
        "book_level_evaluated": sum(1 for r in rows if r["expected"].get("title")),
        "book_level_correct": sum(1 for r in rows if r["checks"]["book_level"]),
    }
    for base in ("title", "author", "isbn", "book_level"):
        denom = m[f"{base}_evaluated"]
        m[f"{base}_accuracy"] = round(m[f"{base}_correct"] / denom, 4) if denom else None
    return m


def _fmt_pct(value: float | None) -> str:
    return "  -  " if value is None else f"{value:.1%}"


def _print_metrics(m: dict[str, Any], indent: str = "") -> None:
    print(f"{indent}样本 {m['n']}  书名 {_fmt_pct(m['title_accuracy'])}({m['title_correct']}/{m['title_evaluated']})"
          f"  作者 {_fmt_pct(m['author_accuracy'])}({m['author_correct']}/{m['author_evaluated']})"
          f"  ISBN {_fmt_pct(m['isbn_accuracy'])}({m['isbn_correct']}/{m['isbn_evaluated']})"
          f"  书级 {_fmt_pct(m['book_level_accuracy'])}({m['book_level_correct']}/{m['book_level_evaluated']})")


def cmd_compare(args: argparse.Namespace) -> int:
    golden = _load_golden(Path(args.golden))
    raw = _load_predictions(Path(args.predictions))

    pred_entries = raw.get("entries") if isinstance(raw, dict) else raw
    by_file: dict[str, dict[str, Any]] = {}
    for p in pred_entries:
        if not isinstance(p, dict) or "file" not in p:
            _die(f"预测条目缺 file 字段: {p}")
        # 兼容扁平 {title,...} 与嵌套 {predicted:{...}} 两种写法
        flat = p.get("predicted") if isinstance(p.get("predicted"), dict) else p
        by_file[p["file"]] = flat

    rows: list[dict[str, Any]] = []
    warnings = {"golden_without_prediction": [], "prediction_without_golden": []}
    for g in golden:
        exp, pred = g["expected"], by_file.get(g["file"])
        if pred is None:
            warnings["golden_without_prediction"].append(g["file"])
            continue
        checks = {
            "title": title_matches(exp.get("title"), pred.get("title")),
            "author": author_matches(exp.get("author"), pred.get("author")),
            "isbn": isbn_matches(exp.get("isbn"), pred.get("isbn")),
        }
        # 书级 = 书名对，且（有期望作者时作者也对）、（有期望 ISBN 时 ISBN 也对）。
        # ISBN 是最强入库主键：识别错 ISBN 还放行 --yes，会以错误去重键批量入库（CHK-056）
        checks["book_level"] = checks["title"] and (
            (not exp.get("author") or checks["author"])
            and (not exp.get("isbn") or checks["isbn"])
        )
        rows.append({"file": g["file"], "difficulty": g.get("difficulty", "normal"),
                     "expected": exp, "predicted": pred, "checks": checks})
    for f in by_file:
        if f not in {g["file"] for g in golden}:
            warnings["prediction_without_golden"].append(f)

    if not rows:
        _die("没有可对比的条目（file 全部未匹配，检查 golden 与 predictions 的文件名）")

    metrics = _metrics_for(rows)
    by_difficulty: dict[str, dict[str, Any]] = {}
    for diff in DIFFICULTIES + ("other",):
        subset = [r for r in rows if r["difficulty"] == diff]
        if subset:
            by_difficulty[diff] = _metrics_for(subset)

    misses = []
    for r in rows:
        problems = [k for k in ("title", "author", "isbn") if r["expected"].get(k) and not r["checks"][k]]
        if problems:
            misses.append({
                "file": r["file"], "difficulty": r["difficulty"], "problems": problems,
                "expected": r["expected"], "predicted": r["predicted"],
            })

    accuracy = metrics["book_level_accuracy"]
    auto_import_allowed = accuracy is not None and accuracy >= THRESHOLDS["book_level"]
    result = {
        "generated_at": now_iso(),
        "golden": str(Path(args.golden)), "predictions": str(Path(args.predictions)),
        "model": raw.get("model") if isinstance(raw, dict) else None,
        "total": metrics["n"],
        "metrics": metrics,
        "by_difficulty": by_difficulty,
        "thresholds": THRESHOLDS,
        "auto_import_allowed": auto_import_allowed,
        "misses": misses,
        "warnings": warnings,
    }

    out = Path(args.out) if args.out else Path(args.eval_dir) / f"results-{datetime.now():%Y%m%d-%H%M%S}.json"
    if not args.no_write:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"模型: {result['model'] or '(未填)'}  样本: {metrics['n']}  生成: {result['generated_at']}")
    print("总体:")
    _print_metrics(metrics, indent="  ")
    print("分档:")
    for diff, m in by_difficulty.items():
        print(f"  {diff:9}", end="")
        _print_metrics(m)
    if misses:
        print(f"miss 清单（{len(misses)} 条）:")
        for miss in misses:
            exp = miss["expected"]
            pred = miss["predicted"]
            print(f"  [{miss['difficulty']}] {miss['file']} 错项{'/'.join(miss['problems'])}: "
                  f"《{exp.get('title')}》{exp.get('author') or ''} → 预测《{pred.get('title') or '?'}》{pred.get('author') or '?'}")
    for key, files in warnings.items():
        if files:
            print(f"⚠ {key}: {files}")
    verdict = "允许 --yes 自动入库" if auto_import_allowed else "未达标，批量入库须逐本人工确认（confirmed 后再 run）"
    print(f"结论: 书级完全正确率 {_fmt_pct(accuracy)}（门槛 {THRESHOLDS['book_level']:.0%}）→ {verdict}")
    if not args.no_write:
        print(f"结果: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="封面识别 eval：合成测试集、金标准对比与分档指标")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="生成合成封面测试集与 golden.json")
    p_gen.add_argument("--eval-dir", default=str(DEFAULT_EVAL_DIR))
    p_gen.add_argument("--force", action="store_true", help="覆盖已有 golden.json 与合成封面")

    p_tpl = sub.add_parser("template", help="从 golden.json 生成 predictions 骨架")
    p_tpl.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    p_tpl.add_argument("--out", default=str(DEFAULT_PREDICTIONS))

    p_cmp = sub.add_parser("compare", help="对比 golden 与 predictions，输出指标与 miss 清单")
    p_cmp.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    p_cmp.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    p_cmp.add_argument("--eval-dir", default=str(DEFAULT_EVAL_DIR), help="结果输出目录")
    p_cmp.add_argument("--out", default=None, help="结果文件路径（默认 {eval-dir}/results-{时间}.json）")
    p_cmp.add_argument("--no-write", action="store_true", help="只打印不落盘")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "template":
        cmd_template(args)
    elif args.command == "compare":
        return cmd_compare(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
