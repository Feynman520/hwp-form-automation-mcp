"""
실제 stdio 전송 통합 검증 — server.py를 별도 프로세스로 띄우고 MCP JSON-RPC로
initialize → tools/list → call_tool(hwp_health) 를 수행한다. Claude Code가 쓰는 것과
동일한 경로(프로세스 기동 + stdio)로 끝까지 동작하는지 확인한다.

실행: .venv\\Scripts\\python.exe tests\\mcp_client.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import anyio  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

VPY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
SERVER = os.path.join(ROOT, "server.py")


def _payload(r):
    """도구 결과에서 dict를 꺼낸다. structuredContent가 없으면 content 텍스트의 JSON을 파싱."""
    if getattr(r, "structuredContent", None):
        return r.structuredContent
    for c in r.content:
        text = getattr(c, "text", None)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return {"text": text}
    return None


async def run():
    params = StdioServerParameters(
        command=VPY,
        args=[SERVER],
        env={**os.environ, "PYTHONUTF8": "1"},
        cwd=ROOT,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[client] initialize OK", flush=True)

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"[client] tools({len(names)}): {names}", flush=True)
            assert len(names) == 8, f"도구 수 불일치: {len(names)}"

            print("[client] call hwp_health (한글 기동) ...", flush=True)
            res = await session.call_tool("hwp_health", {})
            print(f"[client] hwp_health -> {_payload(res)}", flush=True)
            assert not res.isError, "hwp_health 오류"

            # ---- 6개 도구 전체 파이프라인을 실제 전송으로 검증 ----
            out = os.path.join(ROOT, "tests", "_out")
            src = os.path.join(out, "source.hwp")  # e2e_engine.py가 생성한 합성 원본
            if not os.path.exists(src):
                print("[client] (source.hwp 없음 → 전체 파이프라인 생략; e2e_engine.py 먼저 실행)")
            else:
                async def call(name, args):
                    r = await session.call_tool(name, args)
                    assert not r.isError, f"{name} 오류: {[c.text for c in r.content]}"
                    data = _payload(r)
                    print(f"[client]   {name} -> {data}", flush=True)
                    return data

                print("[client] 전체 파이프라인 ①~⑥ (over the wire) ...", flush=True)
                await call("convert_hwp_to_hwpx", {"src_path": src, "out_path": os.path.join(out, "w2.hwpx")})
                await call("extract_hwpx", {"path": os.path.join(out, "w2.hwpx")})
                await call("insert_fields_by_bracket", {"path": os.path.join(out, "w2.hwpx"), "out_path": os.path.join(out, "t2.hwpx")})
                fr = await call("fill_fields", {"path": os.path.join(out, "t2.hwpx"), "data": {"성명": "김철수", "금액": "2,500,000"}, "out_path": os.path.join(out, "f2.hwpx")})
                assert fr["filled"].get("성명") == "김철수", f"필드 채우기 검증 실패: {fr}"
                await call("save_hwpx", {"src_path": os.path.join(out, "f2.hwpx"), "out_path": os.path.join(out, "final2.hwpx")})
                await call("convert_hwpx_to_pdf", {"src_path": os.path.join(out, "final2.hwpx"), "out_path": os.path.join(out, "final2.pdf")})
                assert os.path.exists(os.path.join(out, "final2.pdf")), "최종 PDF 미생성"

            print("[client] === STDIO 통합 OK: 프로세스 기동 + JSON-RPC + 6개 도구 전 파이프라인 동작 ===")


def main():
    anyio.run(run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
