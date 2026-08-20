"""cover-eval Skill：须被 skill_catalog 发现，并写明评测两段流程。"""

from __future__ import annotations

from pathlib import Path

from app.services.skill_catalog import SKILLS_DIR, list_skills

SKILL_NAME = "cover-eval"


def test_cover_eval_skill_file_exists():
    path = SKILLS_DIR / SKILL_NAME / "SKILL.md"
    assert path.is_file(), f"缺少 {path}"


def test_catalog_lists_cover_eval():
    names = {s.name for s in list_skills()}
    assert SKILL_NAME in names
    skill = next(s for s in list_skills() if s.name == SKILL_NAME)
    assert skill.scopes == []
    assert "评测" in skill.description or "eval" in skill.description.lower()
    assert "封面" in skill.description or "多模态" in skill.description


def test_skill_documents_two_phase_eval():
    text = (SKILLS_DIR / SKILL_NAME / "SKILL.md").read_text(encoding="utf-8")
    assert "eval_cover_recognition.py template" in text
    assert "eval_cover_recognition.py compare" in text
    assert "vision_prompt.md" in text
    assert "predictions.json" in text
    assert "book-intake" in text
    assert "不要" in text or "禁止" in text
