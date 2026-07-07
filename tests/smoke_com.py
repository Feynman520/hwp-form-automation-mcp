"""
스모크 테스트 (스펙 §3.4) — 본 구현 전, 이 머신에서 COM 자동화가 실제로 동작하고
보안 팝업이 차단되는지 검증한다. MCP를 거치지 않고 pyhwpx를 직접 호출한다.

실행:
    .venv\\Scripts\\python.exe tests\\smoke_com.py

통과 기준: 예외/팝업 없이 끝까지 진행하고 smoke.hwpx / smoke.pdf 두 파일이 생성된다.
"""
import os
import sys
import traceback

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT_DIR, exist_ok=True)


def log(msg):
    print(f"[smoke] {msg}", flush=True)


def main():
    log("pyhwpx import ...")
    from pyhwpx import Hwp

    log("Hwp(new=True, visible=False) 생성 (보안모듈 자동등록) ...")
    hwp = Hwp(new=True, visible=False)
    try:
        # 1) 한글 버전 확인
        try:
            ver = hwp.Version
            log(f"한글 Version = {ver}")
        except Exception as e:
            log(f"Version 조회 실패(무시): {e}")

        # 2) 누름틀 자리표시자 삽입 후 필드화
        log("insert_text('{{테스트}}') ...")
        hwp.insert_text("{{테스트}}")
        log("set_field_by_bracket() ...")
        hwp.set_field_by_bracket()

        field_list = hwp.get_field_list()
        names = [n for n in (field_list or "").split("\x02") if n]
        log(f"생성된 필드 목록 = {names!r}")
        assert any("테스트" in n for n in names), "필드가 생성되지 않음"

        # 3) 값 채우고 검증
        log("put_field_text({'테스트':'동작확인'}) ...")
        hwp.put_field_text({"테스트": "동작확인"})
        val = hwp.get_field_text("테스트")
        log(f"get_field_text('테스트') = {val!r}")
        assert "동작확인" in (val or ""), "필드 값이 채워지지 않음"

        # 4) 본문 텍스트 추출 (get_text_file)
        try:
            body = hwp.get_text_file()
            log(f"get_text_file() 길이 = {len(body or '')}")
        except Exception as e:
            log(f"get_text_file 실패(보고만): {e}")

        # 5) HWPX 저장 (팝업 없이)
        hwpx_path = os.path.join(OUT_DIR, "smoke.hwpx")
        log(f"save_as HWPX -> {hwpx_path}")
        ok_hwpx = hwp.save_as(hwpx_path)
        log(f"  반환={ok_hwpx}, 존재={os.path.exists(hwpx_path)}")

        # 6) PDF 저장 (내부 HAction 경로, 팝업 없이)
        pdf_path = os.path.join(OUT_DIR, "smoke.pdf")
        log(f"save_as PDF -> {pdf_path}")
        ok_pdf = hwp.save_as(pdf_path)
        log(f"  반환={ok_pdf}, 존재={os.path.exists(pdf_path)}")

        assert os.path.exists(hwpx_path), "HWPX 미생성"
        assert os.path.exists(pdf_path), "PDF 미생성"

        log("=== SMOKE PASS: COM 자동화 + 필드 + HWPX/PDF + 팝업차단 OK ===")
        return 0
    finally:
        try:
            hwp.clear()
        except Exception:
            pass
        log("quit() ...")
        try:
            hwp.quit()
        except Exception as e:
            log(f"quit 실패(무시): {e}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
