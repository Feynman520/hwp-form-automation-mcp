"""
engine/session.py — 한글 COM 세션을 전용 STA 워커 스레드 1개에 고정한다. (스펙 §3.2-bis)

win32com COM 객체는 STA(단일 스레드 아파트)에 묶여, 생성한 그 스레드에서만 안전하게
호출된다. FastMCP는 asyncio 이벤트 루프에서 도구를 실행하며 스레드를 옮겨다닐 수 있으므로,
모든 한글 호출을 여기 정의한 전용 스레드의 작업 큐로 직렬화한다.

사용:
    session = HwpSession()
    session.start()                      # 한글 띄우고 보안모듈 등록 (블로킹)
    result = session.run(lambda hwp: hwp.get_field_list())
    session.stop()                       # 한글 종료
"""
from __future__ import annotations

import contextlib
import os
import queue
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from typing import Any, Callable, Optional


@contextlib.contextmanager
def _fd1_to_stderr():
    """파일디스크립터 1(stdout)을 잠시 stderr로 우회한다.

    pyhwpx는 import 시점(makepy 재생성)과 Hwp() 생성 중 stdout으로 진행상황을 print하는데,
    MCP stdio 서버에서 stdout은 JSON-RPC 채널(파이프)이다. 그대로 두면 (a) 프로토콜이
    오염되고 (b) 파이프 버퍼가 차면 print가 블록돼 한글 초기화가 멈춘다. 이 구간 동안만
    fd1을 stderr(클라이언트가 배수함)로 돌려 둘 다 막는다.

    안전성: 이 우회는 '서버 기동' 또는 '첫 도구 호출 대기 중'에만 쓰이며, 그 시점에 메인
    스레드는 프로토콜 응답을 내보내지 않으므로 프로토콜 stdout 손실이 없다.
    """
    try:
        sys.stdout.flush()
    except Exception:
        pass
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os.dup2(saved, 1)
        os.close(saved)


def preimport_pyhwpx() -> None:
    """pyhwpx를 1회 미리 import해 import-time makepy 재생성을 protocol 바인딩 전에 끝낸다.

    모듈 레벨 코드(gen_py 삭제 + EnsureModule)는 프로세스당 한 번만 실행되므로, 여기서
    선import해 두면 이후 워커의 `from pyhwpx import Hwp`는 캐시 적중으로 재실행되지 않는다.
    """
    with _fd1_to_stderr():
        import pyhwpx  # noqa: F401


def _dbg(msg: str) -> None:
    """진단 로그를 stderr와(설정 시) 파일에 쓴다. stdout은 MCP JSON-RPC 채널이라 절대 쓰지 않는다."""
    line = f"[hwp-session {time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    path = os.environ.get("HWP_MCP_DEBUG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SECURITY_DIR = os.path.join(_PROJECT_ROOT, "security")
_DLL_NAME = "FilePathCheckerModule.dll"
_REG_VALUE = "FilePathCheckerModule"
_REG_PATHS = (r"Software\HNC\HwpAutomation\Modules", r"Software\Hnc\HwpUserAction\Modules")


def ensure_security_module() -> Optional[str]:
    """한글 보안승인모듈(FilePathChecker)을 레지스트리에 선등록한다.

    pyhwpx 1.7.2의 `register_regedit()`는 내부에서 `pip show pyhwpx`를 subprocess로 호출해
    경로를 찾는데, venv/PATH 환경에 따라 실패하면 `location` 미할당 → UnboundLocalError로
    등록이 깨진다. 그러면 한글 2024에서 파일 입출력 보안 팝업이 떠 무인 실행이 막힌다.

    여기서 번들 DLL을 프로젝트 `security/`로 복사하고 레지스트리 값을 직접 써 둔다. 이렇게
    하면 pyhwpx의 `check_registry_key()`가 True를 반환해 버그 경로를 건너뛰고, COM
    `RegisterModule` 호출만 정상 수행된다.

    Returns: 등록한 DLL 경로(성공) 또는 None(번들 DLL을 못 찾은 경우).
    """
    import winreg

    # 1) 번들 DLL 위치 파악 → security/로 복사
    try:
        import pyhwpx

        bundled = os.path.join(os.path.dirname(os.path.abspath(pyhwpx.__file__)), _DLL_NAME)
    except Exception:
        bundled = ""
    os.makedirs(_SECURITY_DIR, exist_ok=True)
    target = os.path.join(_SECURITY_DIR, _DLL_NAME)
    if not os.path.exists(target):
        if bundled and os.path.exists(bundled):
            shutil.copy2(bundled, target)
        elif os.path.exists(target):
            pass
        else:
            return None
    dll_path = target

    # 2) 레지스트리 값 기록 (중간 키 자동 생성)
    for path in _REG_PATHS:
        try:
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE)
            winreg.SetValueEx(key, _REG_VALUE, 0, winreg.REG_SZ, dll_path)
            winreg.CloseKey(key)
        except OSError:
            continue
    return dll_path


class HwpSession:
    """전용 워커 스레드가 소유하는 단일 Hwp 인스턴스."""

    def __init__(self, *, visible: bool = False, new: bool = True):
        self._visible = visible
        self._new = new
        self._tasks: "queue.Queue[Optional[tuple[Callable, Future]]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._init_error: Optional[BaseException] = None
        self._hwp: Any = None

    # ---- lifecycle ---------------------------------------------------------
    def start(self, timeout: float = 120.0) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="hwp-com-worker", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("한글 COM 세션 초기화가 시간 초과되었습니다.")
        if self._init_error is not None:
            raise self._init_error

    def stop(self, timeout: float = 30.0) -> None:
        if self._thread is None:
            return
        self._tasks.put(None)  # sentinel → 워커 종료
        self._thread.join(timeout)
        self._thread = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- 작업 제출 ---------------------------------------------------------
    def run(self, fn: Callable[[Any], Any], timeout: Optional[float] = 300.0) -> Any:
        """`fn(hwp)`를 워커 스레드에서 실행하고 결과를 반환(블로킹)."""
        if not self.alive:
            raise RuntimeError("한글 세션이 살아있지 않습니다. start()를 먼저 호출하세요.")
        fut: Future = Future()
        self._tasks.put((fn, fut))
        return fut.result(timeout)

    # ---- 워커 본체 ---------------------------------------------------------
    def _run(self) -> None:
        import pythoncom

        _dbg("worker thread 시작")
        try:
            pythoncom.CoInitialize()
            _dbg("CoInitialize 완료")
        except Exception as e:  # 이미 초기화된 경우 등은 무시
            _dbg(f"CoInitialize 예외(무시): {e}")

        # 보안 팝업 차단 모듈을 먼저 레지스트리에 선등록(pyhwpx 버그 우회)
        try:
            dll = ensure_security_module()
            _dbg(f"ensure_security_module -> {dll}")
        except Exception as e:
            _dbg(f"ensure_security_module 예외(무시): {e}")

        try:
            _dbg("pyhwpx import ...")
            with _fd1_to_stderr():
                from pyhwpx import Hwp

            _dbg(f"Hwp(new={self._new}, visible={self._visible}) 생성 시작 ...")
            t0 = time.time()
            with _fd1_to_stderr():  # Hwp 초기화 중 stdout print가 JSON-RPC를 오염/블록하지 않게
                self._hwp = Hwp(new=self._new, visible=self._visible)
            _dbg(f"Hwp 생성 완료 ({time.time() - t0:.1f}s)")
        except BaseException as e:  # noqa: BLE001 — 초기화 실패를 메인 스레드로 전달
            _dbg(f"Hwp 생성 실패: {e!r}\n{traceback.format_exc()}")
            self._init_error = e
            self._ready.set()
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            return

        self._ready.set()

        try:
            while True:
                item = self._tasks.get()
                if item is None:
                    break
                fn, fut = item
                if not fut.set_running_or_notify_cancel():
                    continue
                try:
                    fut.set_result(fn(self._hwp))
                except BaseException as e:  # noqa: BLE001
                    fut.set_exception(e)
        finally:
            try:
                if self._hwp is not None:
                    self._hwp.quit()
            except Exception:
                pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
