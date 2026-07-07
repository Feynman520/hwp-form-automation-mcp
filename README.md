# hwp-form-automation-mcp

한컴오피스 한/글 데스크톱을 COM으로 자동화해, **정부·학교 HWP/HWPX 양식의 변환 → 추출 →
필드화 → 값 채우기 → PDF 출력** 전 과정을 8개 도구로 노출하는 로컬 MCP 서버.

> **English**: A local MCP server that automates the Hancom Office (HWP) word processor via COM
> to fill Korean government/school forms end-to-end: convert `.hwp` → `.hwpx`, extract
> text/fields, turn `{{placeholder}}` marks into real form fields, fill them with data, and
> export the final PDF — with original formatting perfectly preserved. Requires Windows +
> a licensed Hancom Office installation. Documentation below is in Korean, as is its audience.

## 왜 이 도구인가

정부·학교의 HWP 양식은 서식이 복잡해서, 파서(파일을 직접 읽는 라이브러리)로 채우면 서식이
깨지기 일쑤다. 이 서버는 **진짜 한/글 엔진**으로 문서를 열고 채우므로 원본 서식이 완벽하게
보존되고, 최종 제출용 PDF까지 한 흐름으로 나온다. LLM(Claude)이 양식의 의미를 분석해
`{{필드명}}` 자리표시자를 삽입하면, 엔진이 기계적 변환·채움·출력을 담당하는 분업 구조다.

## 요구사항

- Windows 10+ / **로그인된 인터랙티브 데스크톱 세션** (한/글은 진짜 headless가 없음)
- 한컴오피스(한/글) 설치 + 활성 라이선스 — 검증 환경: **한컴오피스 2024**
- Python 3.10+ — 검증: **3.12**
- [Claude Code](https://claude.com/claude-code) 또는 임의의 MCP 클라이언트

## 설치

```powershell
git clone https://github.com/Feynman520/hwp-form-automation-mcp.git
cd hwp-form-automation-mcp
py -3.12 -m venv .venv          # 또는: python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Claude Code 등록

클론한 폴더 안에서 아래 한 줄 실행 (절대경로가 박히므로 이후 어디서든 동작):

```powershell
claude mcp add hwp-automation --scope user -- "$PWD\.venv\Scripts\python.exe" "$PWD\server.py"
```

`--scope user`는 모든 프로젝트에서 사용 가능. 한 프로젝트에서만 쓰려면 `--scope project`.

### 동작 검증 (권장 순서)

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
& $py tests\smoke_com.py      # COM·필드·HWPX/PDF·보안팝업차단 1차 검증
& $py tests\e2e_engine.py     # ①~⑥ 전체 파이프라인 검증
& $py tests\server_tools.py   # MCP 8개 도구 등록 검증(한/글 미기동)
& $py tests\mcp_client.py     # 실제 stdio 전송으로 서버 통합 검증
```

## 도구 (6 + 진단 2)

| # | 도구 | 입력 → 출력 |
|---|---|---|
| ① | `convert_hwp_to_hwpx` | `src_path(.hwp), out_path(.hwpx)` → `{out_path}` |
| ② | `extract_hwpx` | `path(.hwpx)` → `{text, fields, field_names, tables, table_count}` |
| ③ | `insert_fields_by_bracket` | `path(.hwpx, {{}}자리표시자), out_path` → `{out_path, field_names}` |
| ④ | `fill_fields` | `path, data{필드명:값}, out_path` → `{out_path, filled, unknown_fields, available_fields}` |
| ⑤ | `save_hwpx` | `src_path, out_path(.hwpx)` → `{out_path}` (정규화/확정) |
| ⑥ | `convert_hwpx_to_pdf` | `src_path(.hwpx), out_path(.pdf)` → `{out_path}` |
| — | `hwp_health` | → `{alive, hangul_version}` |
| — | `restart_session` | → `{alive}` (COM 오류 복구) |

## 파이프라인

```
정부 HWP ─①→ HWPX ─②→ (LLM 분석: 칸에 {{필드명}} 자리표시자 삽입)
        ─③→ 필드화 템플릿(.hwpx)  ── 양식당 1회 제작·재사용
        ─④→ 값 채움 ─⑤→ 완성본(.hwpx) ─⑥→ 제출용 PDF
```

- **①~③**: 양식 종류당 1회(템플릿 자산). **④~⑥**: 신청 건마다 반복.
- 역할 분담: 기계적 변환·추출·저장 = 엔진(pyhwpx), 의미 분석·필드 매핑 = LLM.

## 아키텍처 핵심

- **단일 STA 워커 스레드**(`engine/session.py`): win32com COM은 STA에 묶이고 FastMCP는 async
  스레드를 옮겨다니므로, 모든 한/글 호출을 전용 스레드 1개에 직렬화한다.
- **세션 지연 초기화**: 첫 도구 호출 시 한/글 기동, 서버 종료 시 닫음. 1개 인스턴스 재사용.
- **보안 팝업 차단**: `ensure_security_module()`이 번들 `FilePathCheckerModule.dll`을
  레지스트리(`HKCU\Software\HNC\HwpAutomation\Modules`)에 선등록해, 자동화 시 뜨는
  파일 접근 승인 팝업을 차단한다. (pyhwpx 1.7.2의 `register_regedit` 버그 우회 구현.)

> ⚠️ `security/FilePathCheckerModule.dll`은 **한컴이 제작·배포하는 보안모듈**입니다
> (pyhwpx PyPI 패키지에도 동일 파일이 포함되어 배포됨). 설치 편의를 위해 함께 담았으며
> 본 리포의 MIT 라이선스 대상이 아닙니다 — 해당 파일의 권리는 한컴에 있습니다.

## 한계

- 무인/서비스 세션 부적합(데스크톱 세션 필요). 한/글 라이선스 만료 시 자동화도 중단.
- 동일 이름 필드는 `fill_fields`가 모두 같은 값으로 채움 → 값이 달라야 하면 ③ 단계에서
  유일한 이름을 부여할 것.
- 표 추출(`tables`)은 best-effort. 복잡한 양식은 `extract`의 `text`/`fields`를 우선 활용.

## 라이선스

[MIT](LICENSE) (단, 위 한컴 보안모듈 DLL 제외)
