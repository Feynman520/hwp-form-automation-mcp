"""
engine/extract.py — ② HWPX 요소(본문 텍스트·필드·표) 추출.

전용 워커 스레드에서 호출. 본문은 get_text_file(option="")로 문서 전체를 받는다
(기본 option="saveblock:true"는 선택영역만 반환하므로 비워야 전체가 나온다).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List


def _clean_field_names(raw: str) -> List[str]:
    """get_field_list 결과(\x02 구분, name{{idx}} 표기)를 고유 기본 필드명 목록으로 정리."""
    names: List[str] = []
    for token in (raw or "").split("\x02"):
        token = token.strip()
        if not token:
            continue
        base = token.split("{{")[0]  # 'name{{0}}' → 'name'
        if base and base not in names:
            names.append(base)
    return names


def _extract_tables(hwp, max_tables: int = 200) -> List[List[List[str]]]:
    """문서 내 표들을 best-effort로 2차원 리스트 목록으로 추출. 실패는 무시."""
    tables: List[List[List[str]]] = []
    n = 0
    while n < max_tables:
        try:
            moved = hwp.get_into_nth_table(n)
        except Exception:
            break
        if moved is False:
            break
        try:
            df = hwp.table_to_df()
            tables.append([[("" if v is None else str(v)) for v in row] for row in df.values.tolist()])
        except Exception:
            break
        n += 1
    return tables


def extract(hwp, path: str) -> Dict[str, Any]:
    """② 본문 텍스트 + 필드(이름→값) + 표를 구조적으로 추출."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    if not hwp.open(path):
        raise RuntimeError(f"열기 실패: {path}")
    try:
        try:
            text = hwp.get_text_file("UNICODE", option="")  # 전체 본문
        except Exception:
            text = ""
        try:
            # 빈 키 항목({'': ['']})은 필드 없는 문서의 잡음이므로 제거
            fields = {k: v for k, v in (hwp.fields_to_dict() or {}).items() if k}
        except Exception:
            fields = {}
        try:
            field_names = _clean_field_names(hwp.get_field_list())
        except Exception:
            field_names = list(fields.keys())
        tables = _extract_tables(hwp)
    finally:
        hwp.clear()
    return {
        "text": text,
        "fields": fields,
        "field_names": field_names,
        "tables": tables,
        "table_count": len(tables),
    }
