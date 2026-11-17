"""시스템 프롬프트 로더.

프롬프트는 코드가 아니라 `jeju_agent/prompts/*.md` 파일에 둔다. 파일이 없거나
읽을 수 없으면 짧은 내장 기본값으로 대체해 앱이 죽지 않게 한다.
"""
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_FILE = "system.md"

FALLBACK_PROMPT = (
    "당신은 제주 동네 상권 진단 Agent입니다. 반드시 도구를 호출해 데이터를 확인하고, "
    "도구 결과의 숫자만 출처·기간과 함께 인용하세요. 단정하지 말고 강점·주의로 표현하며, "
    "센터 업로드 데이터 수치는 답변에 쓰지 말고 '화면에 별도 표시'로만 안내하세요."
)


def load_system_prompt(name=SYSTEM_FILE):
    try:
        text = (PROMPT_DIR / name).read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_PROMPT
    return text or FALLBACK_PROMPT
