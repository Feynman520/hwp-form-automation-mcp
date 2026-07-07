"""
server.py — 정부 HWP/HWPX 양식 자동화 MCP 서버 (stdio).

한컴오피스(한/글) 데스크톱을 COM으로 자동화해, 정부 양식의 변환·추출·필드작성·채우기·
PDF 출력을 6개 도구로 노출한다. 모든 한글 호출은 단일 STA 워커 스레드(engine.session)에서
직렬화되며, 도구는 async로 그 블로킹 작업을 워커 스레드에 위임해 이벤트 루프를 막지 않는다.

세션은 **지연 초기화**: 첫 도구 호출 시 한글을 띄우고, 서버 종료 시 닫는다.

실행:  python server.py        (Claude Code가 stdio로 기동)
"""
from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Optional

import anyio
from mcp.server.fastmcp import FastMCP

from engine import convert, extract, fields, save
from engine.session import HwpSession, preimport_pyhwpx

# ---- 단일 세션 관리(지연 초기화) -------------------------------------------
_session: Optional[HwpSession] = None
_lock = threading.Lock()


def _get_session() -> HwpSession:
    global _session
    with _lock:
        if _session is None or not _session.alive:
            s = HwpSession(visible=False, new=True)
            s.start()
            _session = s
        return _session


def _stop_session() -> None:
    global _session
    with _lock:
        if _session is not None:
            _session.stop()
            _session = None


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict]:
    try:
        yield {}
    finally:
        await anyio.to_thread.run_sync(_stop_session)


mcp = FastMCP(
    "hwp-form-automation",
    lifespan=_lifespan,
    instructions=(
        "한컴 한/글(HWP/HWPX) 정부양식 자동화. 파이프라인: "
        "convert_hwp_to_hwpx → extract_hwpx →(LLM이 {{필드명}} 자리표시자 삽입)→ "
        "insert_fields_by_bracket → fill_fields → convert_hwpx_to_pdf. "
        "경로는 절대경로를 권장. 동일 이름 필드는 fill_fields가 모두 같은 값으로 채운다."
    ),
)


# ---- 동기 작업 헬퍼(워커 스레드에서 실행) ----------------------------------
def _run(fn) -> Any:
    return _get_session().run(fn)


# ============================================================================
# 6개 도구 (+ 진단/복구 유틸)
# ============================================================================
@mcp.tool()
async def convert_hwp_to_hwpx(src_path: str, out_path: str) -> dict:
    """① 바이너리 HWP를 개방형 HWPX로 변환한다(한컴 엔진, 서식 보존).

    Args:
        src_path: 원본 .hwp 절대경로
        out_path: 출력 .hwpx 절대경로
    Returns: {"out_path": 변환된 HWPX 경로}
    """
    def work():
        return {"out_path": _run(lambda h: convert.hwp_to_hwpx(h, src_path, out_path))}
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def extract_hwpx(path: str) -> dict:
    """② HWPX의 본문 텍스트·필드·표를 구조적으로 추출한다(어떤 칸에 무엇을 넣을지 분석용).

    Args:
        path: 분석할 .hwpx 절대경로
    Returns: {"text", "fields"(이름→값), "field_names", "tables", "table_count"}
    """
    def work():
        return _run(lambda h: extract.extract(h, path))
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def insert_fields_by_bracket(path: str, out_path: str) -> dict:
    """③ 본문에 넣어둔 `{{필드명}}` 자리표시자를 실제 필드(누름틀)로 일괄 변환해 템플릿을 만든다.

    먼저 extract_hwpx로 분석한 뒤, 입력 자리에 `{{대표자성명}}`처럼 자리표시자를 넣어 저장한
    .hwpx를 입력으로 준다. (`[[이름]]`은 표 셀필드로 변환됨)

    Args:
        path: `{{}}` 자리표시자가 들어간 .hwpx 절대경로
        out_path: 필드화된 템플릿 .hwpx 출력 경로
    Returns: {"out_path", "field_names": 생성된 필드명 목록}
    """
    def work():
        out, names = _run(lambda h: fields.insert_fields_by_bracket(h, path, out_path))
        return {"out_path": out, "field_names": names}
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def fill_fields(path: str, data: dict[str, str], out_path: str) -> dict:
    """④ 필드화된 템플릿의 필드에 값을 채운다(키=필드명). 채운 뒤 실제 값으로 검증한다.

    ⚠️ 동일 이름 필드는 모두 같은 값으로 채워진다. 다른 값이 필요하면 ③에서 필드명을 유일하게
    부여하라(예: 금액_1, 금액_2). 값은 문자열로 전달한다(숫자는 호출 측에서 문자열화).

    Args:
        path: 필드화된 템플릿 .hwpx 절대경로
        data: {필드명: 값} 매핑
        out_path: 값이 채워진 .hwpx 출력 경로
    Returns: {"out_path", "filled"(검증된 값), "unknown_fields"(템플릿에 없던 키), "available_fields"}
    """
    def work():
        return _run(lambda h: fields.fill_fields(h, path, data, out_path))
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def save_hwpx(src_path: str, out_path: str) -> dict:
    """⑤ 문서를 깨끗한 표준 HWPX로 저장/정규화한다(중간 산출물 확정, 포맷 정리용).

    Args:
        src_path: 입력 문서 절대경로(.hwpx 등)
        out_path: 출력 .hwpx 절대경로
    Returns: {"out_path"}
    """
    def work():
        return {"out_path": _run(lambda h: save.save_hwpx(h, src_path, out_path))}
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def convert_hwpx_to_pdf(src_path: str, out_path: str) -> dict:
    """⑥ 완성된 HWPX(또는 HWP)를 제출용 PDF로 출력한다(한컴 엔진 렌더링, 정확도 우선).

    Args:
        src_path: 입력 .hwpx 절대경로
        out_path: 출력 .pdf 절대경로
    Returns: {"out_path": PDF 경로}
    """
    def work():
        return {"out_path": _run(lambda h: convert.hwpx_to_pdf(h, src_path, out_path))}
    return await anyio.to_thread.run_sync(work)


# ---- 진단/복구 -------------------------------------------------------------
@mcp.tool()
async def hwp_health() -> dict:
    """엔진 상태를 점검한다(세션 기동/한글 버전). 첫 호출 시 한글을 띄운다."""
    def work():
        s = _get_session()
        try:
            ver = s.run(lambda h: list(h.Version))
        except Exception as e:  # noqa: BLE001
            ver = f"버전 조회 실패: {e}"
        return {"alive": s.alive, "hangul_version": ver}
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def restart_session() -> dict:
    """한글 세션을 종료 후 재기동한다(COM 오류 복구용)."""
    def work():
        _stop_session()
        s = _get_session()
        return {"alive": s.alive}
    return await anyio.to_thread.run_sync(work)


if __name__ == "__main__":
    # protocol(stdout) 바인딩 전에 pyhwpx import-time makepy 재생성을 끝내둔다
    # (그 출력은 fd 우회로 stderr로 보냄). 이렇게 하면 첫 도구 호출 시 한글 초기화가
    # JSON-RPC stdout 파이프를 오염/블록하지 않는다.
    try:
        preimport_pyhwpx()
    except Exception:
        pass
    mcp.run(transport="stdio")
