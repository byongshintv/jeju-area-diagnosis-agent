"""LLM 모드 Agent(OpenAI 호환 tool calling, 최대 8스텝).

규칙 모드와 같은 도구(`analysis.call_tool`)를 쓰고, 단계·산출물·보고서도 같은 형식으로
돌려준다. 실패하면 `RuleAgent`로 넘기고 결과 `notice`에 사유를 남긴다.

개인정보·반출 경계: 센터 업로드/가상 데이터가 관련된 도구 결과는 기본적으로
LLM에 보내지 않고 자리표시 문구만 보낸다. `CENTER_TO_LLM=aggregates`이고 반출 점검에서
가린 칸이 없을 때만 요약을 보낼 수 있다.
"""
import json
import time
from datetime import datetime

from .analysis import TOOL_SCHEMAS, build_report_tool, call_tool
from .llm import LLMClient, LLMError, sum_usage
from .prompts import load_system_prompt

MAX_LLM_STEPS = 8
CENTER_PLACEHOLDER = ("센터 데이터 분석 결과는 화면에 별도 표시됩니다. "
                      "(개인정보·반출 경계로 LLM 요청에는 수치를 포함하지 않았습니다.)")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def get_hub(ctx):
    if not hasattr(ctx, "_data_hub"):
        from .analysis import DataHub

        ctx._data_hub = DataHub(ctx.catalog)
    return ctx._data_hub


def execute_step(ctx, job, name, args, label=None):
    """도구 한 번 실행 → 결과 dict. 단계는 실행 즉시 job에 누적한다(규칙·LLM 공용)."""
    started = time.time()
    try:
        result = call_tool(get_hub(ctx), name, args)
    except Exception as exc:  # 도구 실패는 단계로 남기고 계속한다.
        result = {"summary_for_llm": "도구 오류: %s" % type(exc).__name__,
                  "artifacts": [], "datasets_used": [], "data": {"error": str(exc)}}
    ms = int((time.time() - started) * 1000)
    summary = result.get("summary_for_llm", "")
    step = {"tool": name, "args": _short_args(args), "summary": summary[:600],
            "datasets": result.get("datasets_used", []),
            "started_at": _now(), "ms": ms,
            "_artifacts": result.get("artifacts") or [],
            "_data": result.get("data") or {}}
    job.add_step(step)
    ctx.log.step(name, _short_args(args), ms, result.get("datasets_used", []))
    return result


def _short_args(args):
    """단계 기록용 인자 요약. 묶음 값(보고서 세션·표)은 내용 대신 크기만 적는다.

    build_report의 session에는 센터 데이터로 계산한 문장이 들어 있어, 그대로 적으면
    steps.log에 센터 수치가 남는다.
    """
    parts = []
    for key, value in (args or {}).items():
        if isinstance(value, dict):
            value = "{%d개 항목}" % len(value)
        elif isinstance(value, (list, tuple)):
            value = "[%d개]" % len(value)
        parts.append("%s=%s" % (key, value))
    return ", ".join(parts)[:300]


def _public_steps(steps):
    return [{k: v for k, v in step.items() if not k.startswith("_")} for step in steps]


def tool_message_payload(ctx, result):
    """도구 결과 → LLM에 보내는 tool 메시지 본문(센터 값은 경계 규칙 적용)."""
    summary = result.get("summary_for_llm", "")
    datasets = [str(d) for d in (result.get("datasets_used") or [])]
    center = any(d.startswith("center") for d in datasets)
    if center:
        if centers_allowed(ctx) and _aggregate_safe(result):
            summary = summary + "\n(센터 집계값은 반출 점검을 통과한 값만 포함했습니다.)"
        elif _center_sections_note(result):
            summary = _strip_center_lines(summary) + "\n" + _center_sections_note(result)
        else:
            summary = CENTER_PLACEHOLDER
    return json.dumps({"summary": summary, "datasets_used": datasets}, ensure_ascii=False)


CENTER_SECTION_LABELS = (("demand", "카드 이용금액"), ("time", "시간대·요일"),
                          ("price", "메뉴·가격대"))
CENTER_SECTION_HEADINGS = (("demand", "수요"), ("time", "시간·요일"),
                           ("price", "가격대"))


def _strip_center_lines(summary):
    """센터에서 나온 문장만 빼고 허브 공개 데이터 문장은 남긴다."""
    kept = [line for line in str(summary or "").splitlines()
            if "센터" not in line and "center:" not in line]
    return "\n".join(line for line in kept if line.strip())


def _center_sections_note(result):
    """어떤 진단 항목이 센터 데이터로 채워졌는지만 알린다(값 아님)."""
    data = result.get("data") or {}
    labels = []
    headings = []
    for (key, label), (_, heading) in zip(CENTER_SECTION_LABELS, CENTER_SECTION_HEADINGS):
        for line in data.get(key) or []:
            if "센터" in line:
                if label not in labels:
                    labels.append(label)
                if heading not in headings:
                    headings.append(heading)
                break
    if not labels:
        return ""
    lines = ["[센터 데이터 분석 완료] " + ", ".join("%s: %s" % (h, "화면 표시") for h in headings)]
    lines.append("[센터 데이터 분석 완료] %s 항목은 위 도구로 이미 분석되었습니다. 수치는 화면에 "
                 "별도 표시되며(개인정보·반출 경계로 LLM에는 전송하지 않음), '제공되지 않았다'고 "
                 "말하지 말고 '화면에 표시됨'으로 안내하세요. 제주 전체 허브 값으로 대신 "
                 "해석하지도 마세요." % ", ".join(labels))
    return "\n".join(lines)


def centers_allowed(ctx):
    return str(ctx.config.get("center_to_llm") or "off").lower() == "aggregates"


def _aggregate_safe(result):
    """반출 점검에서 가린 칸·비공개 합계가 없으면 집계값 반출 허용."""
    check = (result.get("data") or {}).get("export_check")
    if not check:
        return False
    return not check.get("suppressed") and not check.get("total_hidden")


class LLMAgent:
    def __init__(self, ctx, fallback_factory):
        self.ctx = ctx
        self.fallback_factory = fallback_factory

    def run(self, job):
        client = LLMClient(self.ctx.config.get("llm_base_url"),
                           self.ctx.config.get("llm_api_key"),
                           self.ctx.config.get("llm_model"))
        usage_items = []
        outside = _outside_region_note(job.message)
        if outside:
            self._clarify_outside(job, outside)
            return
        messages = [{"role": "system", "content": load_system_prompt() + _context_note(self.ctx)},
                    {"role": "user", "content": job.message}]
        try:
            for step_no in range(MAX_LLM_STEPS):
                last = step_no == MAX_LLM_STEPS - 1
                response = client.chat(messages, tools=None if last else TOOL_SCHEMAS)
                usage_items.append(response.get("usage") or {})
                message = response.get("message") or {}
                tool_calls = [] if last else (message.get("tool_calls") or [])
                if tool_calls:
                    messages.append({"role": "assistant",
                                     "content": message.get("content") or "",
                                     "tool_calls": tool_calls})
                    for call in tool_calls:
                        name, args = _parse_call(call)
                        result = execute_step(self.ctx, job, name, args)
                        messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                         "content": tool_message_payload(self.ctx, result)})
                    continue
                content = str(message.get("content") or "").strip()
                if content:
                    job.result = self._finish(job, content, sum_usage(usage_items))
                    job.status = "done"
                    return
                if last:
                    break
                messages.append({"role": "user", "content": "도구 결과를 바탕으로 답변을 작성해 주세요."})
        except LLMError as exc:
            self.ctx.llm_fallback_reason = str(exc)
            self.ctx.log.write("agent", "llm_fallback", 0, note=str(exc))
        except Exception as exc:  # 어떤 LLM 오류도 앱 전체를 막지 않는다.
            self.ctx.llm_fallback_reason = "%s: %s" % (type(exc).__name__, exc)
            self.ctx.log.write("agent", "llm_fallback", 0, note=self.ctx.llm_fallback_reason)
        else:
            self.ctx.llm_fallback_reason = "LLM이 도구 호출 없이 답하지 못했습니다"
        self._fallback(job)

    def _clarify_outside(self, job, token):
        """제주 밖 지역은 LLM을 부르지 않고 규칙 모드와 같은 되묻기를 돌려준다."""
        from .agent import region_candidates

        question = ("제주 밖 지역(%s)은 이 서비스의 데이터 범위가 아닙니다. "
                    "제주 읍면동 중 어디를 보시겠어요?" % token)
        job.result = {
            "mode": "llm", "answer_markdown": question,
            "steps": [], "artifacts": [], "report_id": None,
            "clarification": {"question": question,
                              "candidates": region_candidates()[:10]},
            "notice": "", "datasets": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0, "calls": 0, "model": self.ctx.config.get("llm_model") or ""},
        }
        job.status = "done"

    # -- 결과 조립 ----------------------------------------------------------
    def _finish(self, job, answer, usage):
        artifacts = self._artifacts_of(job)
        report = self._report_of(job, artifacts)
        report_id = self.ctx.reports.add(report) if report else None
        clarification = self._clarification_of(job)
        center_section = self._center_section(job)
        if center_section:
            answer = answer.rstrip() + "\n\n" + center_section
        if report_id:
            answer += "\n\n보고서 `%s`가 만들어졌습니다. 화면에서 표·차트를 함께 볼 수 있습니다." % report_id
        datasets = []
        for step in job.steps:
            for dataset_id in step.get("datasets") or []:
                if dataset_id not in datasets:
                    datasets.append(dataset_id)
        return {
            "mode": "llm",
            "answer_markdown": answer.strip(),
            "steps": _public_steps(job.steps),
            "artifacts": artifacts,
            "report_id": report_id,
            "datasets": datasets,
            "clarification": clarification,
            "notice": "",
            "usage": usage,
        }

    def _center_section(self, job):
        """센터 데이터 수치 문단은 LLM이 아니라 로컬 템플릿이 채운다."""
        diagnosis = {}
        for step in reversed(job.steps):
            if step["tool"] == "diagnose_area":
                data = step.get("_data") or {}
                if data.get("scope"):
                    diagnosis = data
                    break
        if not diagnosis:
            return ""
        datasets = [str(d) for d in (diagnosis.get("datasets") or [])]
        if not any(d.startswith("center") for d in datasets):
            return ""
        lines = []
        for group in ("demand", "time", "price"):
            for line in diagnosis.get(group) or []:
                if "센터" in line:
                    lines.append("- " + line)
        if not lines:
            return ""
        head = "**센터 데이터 분석 결과(화면 표시용·로컬 생성)**"
        tail = "센터 데이터 수치는 개인정보·반출 경계에 따라 LLM에 전송하지 않고 화면에서만 표시합니다."
        export = diagnosis.get("export_check") or {}
        if export.get("notice"):
            tail += " " + export["notice"]
        return "\n".join([head] + lines + ["", tail])

    def _clarification_of(self, job):
        for step in reversed(job.steps):
            data = step.get("_data") or {}
            item = data.get("clarification")
            if item and item.get("question"):
                return {"question": item["question"],
                        "candidates": item.get("candidates") or []}
        return None

    def _report_of(self, job, artifacts):
        """모델이 build_report를 안 불렀으면 마지막 진단 결과로 보고서를 만든다."""
        diagnosis = {}
        for step in reversed(job.steps):
            if step["tool"] == "diagnose_area":
                diagnosis = step.get("_data") or {}
                break
        if not diagnosis:
            return None
        session = {"question": job.message,
                   "region": (diagnosis.get("scope") or {}).get("name"),
                   "industry": diagnosis.get("industry"),
                   "diagnosis": diagnosis,
                   "steps": _public_steps(job.steps),
                   "artifacts": artifacts,
                   "notice": "",
                   "datasets": diagnosis.get("datasets") or []}
        result = build_report_tool(get_hub(self.ctx), session)
        return (result.get("data") or {}).get("report")

    def _artifacts_of(self, job):
        seen = set()
        out = []
        for step in job.steps:
            for artifact in step.get("_artifacts") or []:
                key = artifact.get("id")
                if key in seen:
                    continue
                seen.add(key)
                out.append(artifact)
        return out

    def _fallback(self, job):
        reason = self.ctx.llm_fallback_reason or "LLM 사용 불가"
        job.add_step({"tool": "fallback_rule", "args": "", "summary": reason,
                      "datasets": [], "started_at": _now(), "ms": 0})
        self.fallback_factory(self.ctx, reason).run(job)
        if job.result:
            job.result["notice"] = "LLM을 사용할 수 없어 규칙 모드로 답했습니다(%s)." % reason
            job.result.setdefault("mode", "rule")


def _outside_region_note(message):
    from .agent import find_outside_region, _region_in

    token = find_outside_region(message)
    if not token:
        return None
    name, _ = _region_in(message)
    return None if name else token


def _context_note(ctx):
    """현재 세션에서 쓸 수 있는 센터(업로드·가상) 데이터의 메타정보만 알린다(값 아님)."""
    items = [d for d in ctx.catalog.datasets() if d["source_kind"] == "center"]
    if not items:
        return ("\n\n# 이번 세션 데이터\n"
                "- 업로드된 센터 데이터가 없습니다. 센터 데이터를 묻는 질문이면 그 사실을 안내하고, "
                "허브 공개 데이터로 답할 수 있는 범위를 설명하세요.")
    lines = ["", "# 이번 세션 데이터", "업로드·가상 센터 데이터가 있습니다(수치는 전송하지 않음, 메타만):"]
    for item in items:
        lines.append("- %s: %s (%s, %s행)" % (item["id"], item["name"],
                                              item.get("period") or "-", item.get("rows")))
    lines.append("센터 데이터를 묻는 질문이면 `diagnose_area`(지역·업종)로 분석을 실행하세요. "
                 "수치는 화면에 별도 표시되므로 당신은 해석과 한계만 설명합니다.")
    return "\n".join(lines)


def _parse_call(call):
    function = (call or {}).get("function") or {}
    name = function.get("name") or ""
    raw = function.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    return name, args
