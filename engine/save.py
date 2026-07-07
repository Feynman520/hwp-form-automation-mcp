"""
engine/save.py — ⑤ 문서를 깨끗한 HWPX로 저장/정규화.

원안의 ⑤ `save_hwpx`는 ④에서 이미 out_path로 저장하므로 산출물이 겹친다. 본 도구는 각 도구가
독립(atomic)인 설계에 맞춰, **임의 입력 문서를 열어 표준 HWPX로 재저장**하는 정규화/확정
유틸리티로 구현한다(예: 중간 산출물을 최종 HWPX로 확정, 다른 포맷을 HWPX로 정리).
"""
from __future__ import annotations

import os


def save_hwpx(hwp, src_path: str, out_path: str) -> str:
    """⑤ src 문서를 열어 표준 HWPX로 저장."""
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {src_path}")
    if not out_path.lower().endswith(".hwpx"):
        raise ValueError("out_path는 .hwpx 확장자여야 합니다.")
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    if not hwp.open(src_path):
        raise RuntimeError(f"열기 실패: {src_path}")
    try:
        ok = hwp.save_as(out_path)
    finally:
        hwp.clear()
    if not ok or not os.path.exists(out_path):
        raise RuntimeError(f"HWPX 저장 실패: {out_path} (반환={ok})")
    return out_path
