"""OpenAI 호환 chat completions 클라이언트(표준 라이브러리 urllib만).

- `POST {base_url}/chat/completions`, tool calling(tools) 지원.
- 타임아웃 60초, 429·5xx는 지수 백오프로 2회 재시도.
- 키 값은 로그·예외 메시지에 남기지 않는다.
"""
import json
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 2
BACKOFF_BASE = 0.5


class LLMError(Exception):
    """LLM 호출 실패. 사용자에게 보여도 되는 짧은 사유만 담는다."""


class LLMClient:
    def __init__(self, base_url, api_key, model, timeout=DEFAULT_TIMEOUT,
                 retries=DEFAULT_RETRIES):
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.model = str(model or "")
        self.timeout = timeout
        self.retries = retries

    @property
    def endpoint(self):
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def chat(self, messages, tools=None, temperature=0.2):
        """messages → {"message": {...}, "usage": {...}}."""
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"content-type": "application/json",
                   "authorization": "Bearer " + self.api_key}
        last_error = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.endpoint, data=body, headers=headers,
                                             method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
            except urllib.error.HTTPError as exc:
                last_error = "HTTP %s" % exc.code
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < self.retries:
                        time.sleep(BACKOFF_BASE * (2 ** attempt))
                        continue
                raise LLMError("LLM 응답 오류(%s)" % exc.code) from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = type(exc).__name__
                if attempt < self.retries:
                    time.sleep(BACKOFF_BASE * (2 ** attempt))
                    continue
                raise LLMError("LLM 연결 실패(%s)" % type(exc).__name__) from None
            try:
                data = json.loads(raw.decode("utf-8"))
                message = data["choices"][0]["message"]
            except (ValueError, KeyError, IndexError, UnicodeDecodeError):
                raise LLMError("LLM 응답 형식이 올바르지 않습니다") from None
            usage = data.get("usage") or {}
            return {"message": message,
                    "usage": {"prompt_tokens": _int(usage.get("prompt_tokens")),
                              "completion_tokens": _int(usage.get("completion_tokens")),
                              "total_tokens": _int(usage.get("total_tokens")),
                              "model": data.get("model") or self.model}}
        raise LLMError("LLM 호출 실패(%s)" % (last_error or "알 수 없음"))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def sum_usage(items):
    """호출별 usage 합산 + 호출 횟수."""
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
             "calls": 0, "model": ""}
    for item in items:
        total["prompt_tokens"] += _int(item.get("prompt_tokens"))
        total["completion_tokens"] += _int(item.get("completion_tokens"))
        total["total_tokens"] += _int(item.get("total_tokens"))
        total["calls"] += 1
        if not total["model"] and item.get("model"):
            total["model"] = item["model"]
    return total
