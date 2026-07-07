"""
end-to-end 엔진 테스트 — MCP 도구가 호출하는 것과 동일한 engine/session 경로로
①~⑥ 전체 파이프라인을 검증한다.

  A. 합성 원본 .hwp 생성(자리표시자 {{성명}}, {{금액}} 포함)
  ①  convert_hwp_to_hwpx
  ②  extract_hwpx (본문에 자리표시자 텍스트가 보여야 함)
  ③  insert_fields_by_bracket (성명/금액 필드 생성 확인)
  ④  fill_fields (값 채움 + 검증)
  ⑤  save_hwpx
  ⑥  convert_hwpx_to_pdf

실행: .venv\\Scripts\\python.exe tests\\e2e_engine.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import convert, extract, fields, save  # noqa: E402
from engine.session import HwpSession  # noqa: E402

OUT = os.path.join(ROOT, "tests", "_out")
os.makedirs(OUT, exist_ok=True)


def p(name):
    return os.path.join(OUT, name)


def log(m):
    print(f"[e2e] {m}", flush=True)


def main():
    s = HwpSession(visible=False, new=True)
    log("세션 시작(한글 기동 + 보안모듈 선등록) ...")
    s.start()
    try:
        # A. 합성 원본 .hwp 생성
        src_hwp = p("source.hwp")
        log(f"A. 합성 원본 생성 -> {src_hwp}")

        def build(h):
            h.clear()
            h.insert_text("[신청서]")
            h.BreakPara()
            h.insert_text("신청인 성명: {{성명}}")
            h.BreakPara()
            h.insert_text("신청 금액: {{금액}}")
            ok = h.save_as(src_hwp)
            h.clear()
            return ok

        assert s.run(build), "원본 저장 실패"
        assert os.path.exists(src_hwp)

        # ① HWP -> HWPX
        work_hwpx = p("work.hwpx")
        log("① convert_hwp_to_hwpx ...")
        s.run(lambda h: convert.hwp_to_hwpx(h, src_hwp, work_hwpx))
        assert os.path.exists(work_hwpx)

        # ② extract
        log("② extract_hwpx ...")
        data = s.run(lambda h: extract.extract(h, work_hwpx))
        log(f"   text(앞 60)={data['text'][:60]!r} field_names={data['field_names']} tables={data['table_count']}")
        assert "성명" in data["text"], "본문에서 자리표시자 텍스트를 찾지 못함"

        # ③ insert fields
        tmpl = p("template.hwpx")
        log("③ insert_fields_by_bracket ...")
        out3, names = s.run(lambda h: fields.insert_fields_by_bracket(h, work_hwpx, tmpl))
        log(f"   생성된 필드명={names}")
        assert "성명" in names and "금액" in names, f"필드 생성 실패: {names}"

        # ④ fill
        filled = p("filled.hwpx")
        log("④ fill_fields ...")
        res = s.run(lambda h: fields.fill_fields(h, tmpl, {"성명": "홍길동", "금액": "1,000,000"}, filled))
        log(f"   filled={res['filled']} unknown={res['unknown_fields']}")
        assert res["filled"].get("성명") == "홍길동", f"성명 채우기 실패: {res['filled']}"
        assert "1,000,000" in res["filled"].get("금액", ""), f"금액 채우기 실패: {res['filled']}"

        # ⑤ save (정규화)
        final_hwpx = p("final.hwpx")
        log("⑤ save_hwpx ...")
        s.run(lambda h: save.save_hwpx(h, filled, final_hwpx))
        assert os.path.exists(final_hwpx)

        # ⑥ PDF
        final_pdf = p("final.pdf")
        log("⑥ convert_hwpx_to_pdf ...")
        s.run(lambda h: convert.hwpx_to_pdf(h, final_hwpx, final_pdf))
        assert os.path.exists(final_pdf)

        log("=== E2E PASS: ①~⑥ 전체 파이프라인 동작 확인 ===")
        for f in ["source.hwp", "work.hwpx", "template.hwpx", "filled.hwpx", "final.hwpx", "final.pdf"]:
            fp = p(f)
            log(f"   {f}: {os.path.getsize(fp)} bytes" if os.path.exists(fp) else f"   {f}: MISSING")
        return 0
    finally:
        log("세션 종료 ...")
        s.stop()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
