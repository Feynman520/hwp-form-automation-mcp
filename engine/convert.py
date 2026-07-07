"""
engine/convert.py — ① HWP→HWPX, ⑥ HWPX→PDF 변환.

모든 함수는 `hwp`(pyhwpx Hwp)를 첫 인자로 받고, 전용 워커 스레드에서 호출된다(session.run).
각 함수는 open → 작업 → clear로 문서 상태를 비워 다음 호출 오염을 막는다.
"""
from __future__ import annotations

import os


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)


def hwp_to_hwpx(hwp, src_path: str, out_path: str) -> str:
    """① 바이너리 HWP → 개방형 HWPX. 한컴 엔진으로 변환(서식 보존)."""
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"원본을 찾을 수 없습니다: {src_path}")
    _ensure_parent(out_path)
    if not hwp.open(src_path):
        raise RuntimeError(f"열기 실패: {src_path}")
    try:
        ok = hwp.save_as(out_path)  # 확장자 .hwpx → SaveAs(Format='HWPX')
    finally:
        hwp.clear()
    if not ok or not os.path.exists(out_path):
        raise RuntimeError(f"HWPX 저장 실패: {out_path} (반환={ok})")
    return out_path


def hwpx_to_pdf(hwp, src_path: str, out_path: str) -> str:
    """⑥ HWPX(또는 HWP) → PDF. 엔진 렌더링(HAction FileSaveAs_S/FileSaveAsPdf)."""
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"원본을 찾을 수 없습니다: {src_path}")
    _ensure_parent(out_path)
    if not hwp.open(src_path):
        raise RuntimeError(f"열기 실패: {src_path}")
    try:
        ok = hwp.save_as(out_path)  # 확장자 .pdf → 내부 PDF 액션
    finally:
        hwp.clear()
    if not ok or not os.path.exists(out_path):
        raise RuntimeError(f"PDF 저장 실패: {out_path} (반환={ok})")
    return out_path
