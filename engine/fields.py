"""
engine/fields.py — ③ {{}} 자리표시자 → 필드 변환, ④ 필드 값 채우기.

전용 워커 스레드에서 호출.
- ③ set_field_by_bracket(): 본문의 `{{name}}`을 누름틀로, `[[name]]`을 셀필드로 일괄 변환.
- ④ put_field_text(dict): 필드명=키로 값 채움. ⚠️ 동일 이름 필드는 모두 같은 값으로 채워진다.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


def _clean_field_names(raw: str) -> List[str]:
    names: List[str] = []
    for token in (raw or "").split("\x02"):
        token = token.strip()
        if not token:
            continue
        base = token.split("{{")[0]
        if base and base not in names:
            names.append(base)
    return names


def insert_fields_by_bracket(hwp, path: str, out_path: str) -> Tuple[str, List[str]]:
    """③ `{{필드명}}` 자리표시자를 실제 필드(누름틀)로 일괄 변환 후 템플릿 저장."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    if not hwp.open(path):
        raise RuntimeError(f"열기 실패: {path}")
    try:
        hwp.set_field_by_bracket()
        names = _clean_field_names(hwp.get_field_list())
        ok = hwp.save_as(out_path)
    finally:
        hwp.clear()
    if not ok or not os.path.exists(out_path):
        raise RuntimeError(f"템플릿 저장 실패: {out_path} (반환={ok})")
    return out_path, names


def fill_fields(hwp, path: str, data: Dict[str, Any], out_path: str) -> Dict[str, Any]:
    """④ data(dict, 키=필드명)를 필드에 채우고 저장. 채운 뒤 값 검증 결과를 함께 반환."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    if not isinstance(data, dict) or not data:
        raise ValueError("data는 비어있지 않은 dict(키=필드명)여야 합니다.")
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    if not hwp.open(path):
        raise RuntimeError(f"열기 실패: {path}")
    try:
        existing = _clean_field_names(hwp.get_field_list())
        unknown = [k for k in data if k not in existing]
        # 문자열로 정규화해서 채움
        payload = {str(k): ("" if v is None else str(v)) for k, v in data.items()}
        hwp.put_field_text(payload)
        # 검증: 채운 키별 실제 값
        verified: Dict[str, str] = {}
        for k in payload:
            try:
                verified[k] = hwp.get_field_text(k)
            except Exception:
                verified[k] = ""
        ok = hwp.save_as(out_path)
    finally:
        hwp.clear()
    if not ok or not os.path.exists(out_path):
        raise RuntimeError(f"저장 실패: {out_path} (반환={ok})")
    return {
        "out_path": out_path,
        "filled": verified,
        "unknown_fields": unknown,
        "available_fields": existing,
    }
