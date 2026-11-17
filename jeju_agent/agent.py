"""규칙 모드 Agent와 Job 관리.

LLM이 없거나 실패하면 사전으로 지역·업종·의도를 뽑아 같은 도구를 고정 순서로 실행하고,
템플릿 문장으로 답한다. `POST /api/chat` → `{job_id}`, `GET /api/jobs/<id>` →
`{status, steps_so_far, result?}`. 단계는 실행 즉시 누적되어 UI가 실시간으로 보여준다.
"""
import re
import threading
import time
from datetime import datetime

from .agent_llm import _public_steps, execute_step
from .dictionaries import resolve_industry, resolve_region
from .reports import ReportStore

MAX_STEPS = 12


def _now():
    return datetime.now().isoformat(timespec="seconds")


class Job:
    def __init__(self, job_id, session_id, message):
        self.id = job_id
        self.session_id = session_id
        self.message = message
        self.status = "running"
        self.steps = []
        self.result = None
        self.error = None
        self.created_at = _now()
        self._lock = threading.Lock()

    def add_step(self, step):
        with self._lock:
            self.steps.append(step)

    def snapshot(self):
        with self._lock:
            steps = [{k: v for k, v in step.items() if not k.startswith("_")}
                     for step in self.steps]
            payload = {"job_id": self.id, "status": self.status,
                       "steps_so_far": steps}
            if self.result is not None:
                payload["result"] = self.result
            if self.error:
                payload["error"] = self.error
            return payload


class JobStore:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()
        self._counter = 0

    def create(self, session_id, message):
        with self._lock:
            self._counter += 1
            job_id = "j%d_%d" % (int(time.time() * 1000), self._counter)
            job = Job(job_id, session_id, message)
            self._jobs[job_id] = job
            if len(self._jobs) > 200:
                for old in list(self._jobs)[:50]:
                    if self._jobs[old].status != "running":
                        self._jobs.pop(old, None)
            return job

    def get(self, job_id):
        return self._jobs.get(job_id)


# ---------------------------------------------------------------------------
# 의도 추출
# ---------------------------------------------------------------------------

COMPARE_WORDS = ("비교", "vs", "VS", "대비", "어디가", "어느 쪽", "어느쪽")
TREND_WORDS = ("추세", "트렌드", "변화", "늘", "줄", "증가", "감소", "최근 흐름")
UPLOAD_WORDS = ("업로드", "올린", "센터 데이터", "내 데이터", "가상 데이터", "시연 데이터")


def detect_intent(message):
    text = str(message or "")
    if any(word in text for word in COMPARE_WORDS):
        return "compare"
    if any(word in text for word in TREND_WORDS):
        return "trend"
    if any(word in text for word in UPLOAD_WORDS):
        return "upload"
    return "diagnose"


OUT_OF_JEJU = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
               "강원", "충북", "충남", "전북", "전남", "경북", "경남", "화성", "수원",
               "동탄", "판교", "제주도 밖")


def find_outside_region(message):
    text = str(message or "")
    for token in OUT_OF_JEJU:
        if token in text:
            return token
    return None


def _regions_in(message):
    """문장에서 지역 표현 2~5개를 순서대로 뽑아 확정한다."""
    text = str(message or "")
    found = []
    for token in _CANDIDATE_TOKENS(text):
        name, _ = resolve_region(token)
        if name and name not in found:
            found.append(name)
    return found[:5]


def _CANDIDATE_TOKENS(text):
    """읍면동처럼 보이는 조각(…동/읍/면)을 긴 것부터 뽑는다."""
    tokens = re.findall(r"[가-힣]{1,6}(?:동|읍|면)", text)
    tokens.sort(key=len, reverse=True)
    return tokens


def _industry_in(message):
    text = str(message or "")
    name, _ = resolve_industry(text)
    if name:
        return name
    # 문장 전체에서 못 찾으면 업종 별칭을 직접 찾는다.
    from .dictionaries import get_industries

    for industry in get_industries():
        for alias in [industry["name"]] + industry.get("aliases", []):
            if alias and alias in text:
                return industry["name"]
    return None


def _region_in(message):
    text = str(message or "")
    name, candidates = resolve_region(text)
    if name:
        return name, []
    for token in _CANDIDATE_TOKENS(text):
        name, candidates = resolve_region(token)
        if name:
            return name, []
    return None, candidates


# ---------------------------------------------------------------------------
# 규칙 모드 실행기
# ---------------------------------------------------------------------------

class RuleAgent:
    def __init__(self, ctx, fallback_reason=""):
        self.ctx = ctx
        self.fallback_reason = fallback_reason or getattr(ctx, "llm_fallback_reason", "")

    # -- 공용 실행 ----------------------------------------------------------
    def _run(self, job, name, args):
        return execute_step(self.ctx, job, name, args)

    # -- 진입점 -------------------------------------------------------------
    def run(self, job):
        try:
            self._dispatch(job)
        except Exception as exc:
            job.error = "%s: %s" % (type(exc).__name__, exc)
            job.status = "error"
            self.ctx.log.write("agent", "job_error", 500, note=job.error)
            return
        if job.status == "running":
            job.status = "done"

    def _dispatch(self, job):
        message = job.message
        intent = detect_intent(message)
        outside = find_outside_region(message)
        region, candidates = _region_in(message)

        if outside and not region:
            job.result = self._clarify(
                job, "제주 밖 지역(%s)은 이 서비스의 데이터 범위가 아닙니다. "
                     "제주 읍면동 중 어디를 보시겠어요?" % outside,
                self._region_candidates())
            return
        if not region and intent != "trend":
            job.result = self._clarify(
                job, "어느 읍면동을 말씀하시는 건가요? 제주 43개 읍면동 중에서 알려 주세요.",
                self._region_candidates())
            return

        if intent == "compare":
            regions = _regions_in(message)
            if len(regions) < 2:
                regions = _regions_in(message) or ([region] if region else [])
            industry = _industry_in(message)
            if len(regions) < 2:
                job.result = self._clarify(job, "비교할 읍면동 두 곳을 알려 주세요.",
                                           self._region_candidates())
                return
            if not industry:
                job.result = self._clarify(job, "어떤 업종으로 비교할까요?",
                                           self._industry_candidates())
                return
            self._run(job, "resolve_region", {"text": regions[0]})
            self._run(job, "resolve_industry", {"text": industry})
            compared = self._run(job, "compare_areas", {"regions": regions,
                                                        "industry": industry})
            for name in regions[:2]:
                self._run(job, "diagnose_area", {"region": name, "industry": industry})
            result = self._finish(job, message, regions[0], industry,
                                  None, intent="compare",
                                  compare_rows=compared["data"].get("rows"))
            return

        if not region:
            job.result = self._clarify(job, "어느 읍면동을 말씀하시는 건가요?",
                                       self._region_candidates())
            return
        industry = _industry_in(message)
        if not industry:
            self._run(job, "resolve_region", {"text": region})
            job.result = self._clarify(job, "어떤 업종을 보시겠어요?",
                                       self._industry_candidates())
            return

        self._run(job, "resolve_region", {"text": region})
        self._run(job, "resolve_industry", {"text": industry})
        if intent == "trend":
            self._run(job, "trend", {"dataset_id": "hub:583", "metric": "visit_pop",
                                     "filters": [{"col": "emd", "eq": region}]})
        diagnosis = self._run(job, "diagnose_area", {"region": region, "industry": industry})
        if diagnosis.get("data", {}).get("clarification"):
            job.result = self._clarify(
                job, diagnosis["data"]["clarification"].get("question")
                or "지역을 다시 알려 주세요.", self._region_candidates())
            return
        self._finish(job, message, region, industry, diagnosis.get("data"))

    # -- 결과 조립 ----------------------------------------------------------
    def _finish(self, job, message, region, industry, diagnosis, intent="diagnose",
                compare_rows=None):
        scope = (diagnosis or {}).get("scope") or {}
        scope_name = scope.get("name") or region
        industry_name = (diagnosis or {}).get("industry") or industry
        artifacts = []
        datasets = []
        for step in job.steps:
            for dataset_id in step.get("datasets") or []:
                if dataset_id not in datasets:
                    datasets.append(dataset_id)
        diagnosis_artifacts = self._artifacts_of(job, "diagnose_area")
        if intent == "compare" and compare_rows:
            answer = self._compare_answer(compare_rows, industry_name)
            artifacts = self._artifacts_of(job, "compare_areas")
        else:
            answer = self._answer(diagnosis or {}, scope_name, industry_name)
            artifacts = diagnosis_artifacts

        report_session = {
            "question": message,
            "region": scope_name,
            "industry": industry_name,
            "diagnosis": diagnosis or {},
            "steps": _public_steps(job.steps),
            "artifacts": artifacts,
            "notice": self._notice(),
        }
        report_result = self._run(job, "build_report", {"session": report_session})
        report = (report_result.get("data") or {}).get("report") or {}
        report_id = self.ctx.reports.add(report) if report else None

        answer = answer.strip()
        if report_id:
            answer += "\n\n보고서 `%s`가 만들어졌습니다. 화면에서 표·차트를 함께 볼 수 있습니다." % report_id
        job.result = {
            "mode": "rule",
            "answer_markdown": answer,
            "steps": _public_steps(job.steps),
            "artifacts": artifacts,
            "report_id": report_id,
            "datasets": datasets,
            "notice": self._notice(),
        }
        return job.result

    def _artifacts_of(self, job, tool_name):
        for step in reversed(job.steps):
            if step["tool"] == tool_name:
                return step.get("_artifacts") or []
        return []

    def _answer(self, diagnosis, scope_name, industry_name):
        if not diagnosis:
            return "%s %s에 대한 근거 데이터를 찾지 못했습니다. 다른 지역·업종으로 시도해 주세요." % (
                scope_name, industry_name)
        lines = ["**%s %s 상권 진단**" % (scope_name, industry_name), ""]
        demand = diagnosis.get("demand") or []
        if demand:
            lines.append("**수요**")
            lines.extend("- " + line for line in demand)
            lines.append("")
        competition = diagnosis.get("competition") or []
        if competition:
            lines.append("**경쟁**")
            lines.extend("- " + line for line in competition)
            lines.append("")
        time_lines = diagnosis.get("time") or []
        if time_lines:
            lines.append("**시간·요일**")
            lines.extend("- " + line for line in time_lines)
            lines.append("")
        price = diagnosis.get("price") or []
        if price:
            lines.append("**가격대**")
            lines.extend("- " + line for line in price)
            lines.append("")
        access = diagnosis.get("access") or []
        if access:
            lines.append("**접근성**")
            lines.extend("- " + line for line in access)
            lines.append("")
        judgments = diagnosis.get("judgments") or []
        if judgments:
            lines.append("**종합** (제주 43개 읍면동 안 상대 위치)")
            for item in judgments:
                lines.append("- %s: %s — %s" % (
                    item["metric"], item["position"], item["tone"]))
            lines.append("")
        lines.append("숫자는 위 데이터·기간 기준이며 창업 성과를 보장하지 않습니다. "
                     "임대료·인건비 등 비용은 포함되지 않았습니다.")
        return "\n".join(lines)

    def _compare_answer(self, rows, industry_name):
        lines = ["**%s 지역 비교**" % industry_name, ""]
        for row in rows:
            lines.append("- **%s**: 유동인구 지수(허브 집계값) %s, 동종 점포 %s개, 점포당 카드이용금액 %s원, "
                         "정류소 일평균 승차 %s명" % (
                             row.get("name"), _fmt(row.get("floating")),
                             _fmt(row.get("stores")), _fmt(row.get("sales_per_store")),
                             _fmt(row.get("bus"))))
        lines.append("")
        lines.append("수치는 각 지역 스냅샷 기준이며, 유동인구는 hub:583, 점포 수는 hub:793, "
                     "카드이용금액은 hub:748, 버스는 hub:613에서 왔습니다.")
        return "\n".join(lines)

    def _notice(self):
        if self.fallback_reason:
            return "LLM을 사용할 수 없어 규칙 모드로 답했습니다(%s)." % self.fallback_reason
        return ""

    def _clarify(self, job, question, candidates):
        return {
            "mode": "rule",
            "answer_markdown": question,
            "steps": _public_steps(job.steps),
            "artifacts": [],
            "report_id": None,
            "datasets": [],
            "clarification": {"question": question, "candidates": candidates[:10]},
            "notice": self._notice(),
        }

    def _region_candidates(self):
        return region_candidates()

    def _industry_candidates(self):
        from .dictionaries import get_industries

        return [i["name"] for i in get_industries()]


def region_candidates():
    from .dictionaries import get_regions

    return [r["name"] for r in get_regions() if r["level"] == "행정동"]


def _fmt(value):
    try:
        return "{:,.0f}".format(float(value))
    except (TypeError, ValueError):
        return "-"
