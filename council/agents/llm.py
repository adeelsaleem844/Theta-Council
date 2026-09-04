"""Agent 5 - the Adjudicator. The language model, on a short leash.

What the model is for
---------------------
The four deterministic agents are good at arithmetic and bad at context. They
cannot tell that three of today's five candidates are the same bet on the same
index in three costumes, or that a 1.4x variance premium across every megacap
at once smells like an upcoming macro print rather than five independent
opportunities. That cross-sectional judgement is what the model contributes.

What the model cannot do
------------------------
It is handed a closed list of pre-validated structures and may only:

  * reject any of them, with a reason
  * lower conviction, which lowers size (the multiplier is clamped to <= 1.0)
  * set a desk posture, which can only ever tighten the risk budget

It never sees an order endpoint, never picks a strike, never sets a quantity,
and cannot approve an id that was not offered. Ids it invents are dropped. If
the call fails, times out, or returns unparseable output, the desk continues
deterministically and the journal records that it did - the model is an
improvement to the process, not a dependency of it.

Provider: Featherless AI (open-weight models, OpenAI-compatible) by default,
Anthropic optionally, and `none` for a fully deterministic run.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..util import clamp, log

TIMEOUT = 75

# If the configured Featherless model is not currently served, fall through this
# list rather than dropping the reasoning layer for the whole session. Verified
# against featherless.ai's published catalogue; the last entry is the model used
# in their own quickstart, so it is the safest floor.
FEATHERLESS_FALLBACKS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct",
]
MODEL_ERROR_HINTS = ("model", "not found", "unavailable", "unknown", "no such")

SYSTEM = """You are the risk-adjudicating portfolio manager of a small, \
defined-risk options income desk. You are the last human-like check before \
orders go out.

Your desk's edge is the variance risk premium: it sells option spreads when \
implied volatility is meaningfully above the underlying's realised volatility, \
and buys spreads when implied is below realised. Every candidate you are shown \
has already passed liquidity, expectancy, delta-band and sizing screens.

Your job is judgement the screens cannot do:
- Correlation. Several candidates on correlated underlyings (index ETFs plus \
megacap tech) are ONE bet, not several. Cut the weakest.
- Regime coherence. If a high variance premium appears across the whole tape at \
once, that is usually an upcoming macro event being priced, not free money. \
Reduce posture.
- Concentration of direction. Do not let the book become one-way short puts.
- Quality. Prefer higher edge_ratio, higher liquidity, better track_record_prior.

Hard constraints on your output:
- You may only reference candidate ids that were given to you.
- size_multiplier is in [0.0, 1.0]. You can shrink a position. You can NEVER \
grow one. There is no way to request more size.
- Rejecting everything is a valid and sometimes correct answer.

Reply with JSON only, no prose, no code fences:
{"posture": "normal|defensive|stand_down",
 "posture_reason": "<one sentence>",
 "decisions": [{"id": "<candidate id>", "action": "approve|reject",
                "size_multiplier": 0.0-1.0, "conviction": 0.0-1.0,
                "thesis": "<one sentence, cite the numbers>",
                "invalidators": ["<what would prove this wrong>"]}]}"""

POSTURE_MULT = {"normal": 1.0, "defensive": 0.5, "stand_down": 0.0}


class Adjudicator:
    name = "adjudicator"

    def __init__(self, cfg):
        self.cfg = cfg
        self.provider = cfg.llm_provider
        self.last_raw = ""
        self.last_error = ""
        self.model = ""
        if self.provider == "featherless" and not cfg.featherless_key:
            self.provider = "none"
            self.last_error = "FEATHERLESS_API_KEY not set"
        if self.provider == "anthropic" and not cfg.anthropic_key:
            self.provider = "none"
            self.last_error = "ANTHROPIC_API_KEY not set"
        if self.provider == "featherless":
            self.model = cfg.featherless_model
        elif self.provider == "anthropic":
            self.model = cfg.anthropic_model

    @property
    def enabled(self) -> bool:
        return self.provider in ("featherless", "anthropic")

    # ── transport ────────────────────────────────────────────────────────────

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"content-type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def _featherless(self, prompt: str, model: str) -> str:
        payload = self._post(
            f"{self.cfg.featherless_base.rstrip('/')}/chat/completions",
            {"authorization": f"Bearer {self.cfg.featherless_key}"},
            {"model": model,
             "messages": [{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}],
             "temperature": 0.2, "max_tokens": 1400})
        return payload["choices"][0]["message"]["content"]

    def _complete(self, prompt: str) -> str:
        if self.provider == "featherless":
            tried = []
            for model in [self.cfg.featherless_model] + FEATHERLESS_FALLBACKS:
                if model in tried:
                    continue
                tried.append(model)
                try:
                    text = self._featherless(prompt, model)
                    if model != self.model:
                        log("WARN", "featherless model substituted",
                            asked=self.model, using=model)
                        self.model = model
                    return text
                except urllib.error.HTTPError as exc:
                    body = exc.read().decode("utf-8", "replace")[:200].lower()
                    unavailable = exc.code in (400, 402, 404, 422) and any(
                        h in body for h in MODEL_ERROR_HINTS)
                    if not unavailable:
                        raise
                    log("WARN", "featherless model unavailable, trying the next",
                        model=model, detail=body[:120])
            raise RuntimeError(f"no featherless model available (tried {tried})")
        if self.provider == "anthropic":
            payload = self._post(
                "https://api.anthropic.com/v1/messages",
                {"x-api-key": self.cfg.anthropic_key,
                 "anthropic-version": "2023-06-01"},
                {"model": self.cfg.anthropic_model, "max_tokens": 1400,
                 "temperature": 0.2, "system": SYSTEM,
                 "messages": [{"role": "user", "content": prompt}]})
            return "".join(b.get("text", "") for b in payload.get("content", []))
        raise RuntimeError("no llm provider")

    # ── parsing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> dict:
        s = (text or "").strip()
        if s.startswith("```"):
            s = s.split("```")[1] if len(s.split("```")) > 1 else s
            if s.lower().startswith("json"):
                s = s[4:]
        start = s.find("{")
        if start < 0:
            raise ValueError("no JSON object in model output")
        depth, end = 0, -1
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            raise ValueError("unterminated JSON object")
        return json.loads(s[start:end])

    # ── entry point ──────────────────────────────────────────────────────────

    def adjudicate(self, brief, candidates: list, regimes: dict,
                   portfolio_regime: str, open_book: list) -> dict:
        """Returns {posture, posture_reason, applied:[...], used_llm, model}."""
        if not candidates:
            return self._passthrough(candidates, "no candidates to review")
        if not self.enabled:
            return self._passthrough(
                candidates, f"deterministic mode ({self.last_error or 'LLM_PROVIDER=none'})")

        prompt = json.dumps({
            "desk": {
                "equity": round(brief.equity, 2),
                "portfolio_regime": portfolio_regime,
                "open_structures": open_book,
                "market_open": brief.is_open,
            },
            "symbol_state": [s.compact() for s in brief.symbols.values() if not s.error],
            "regimes": {k: {"label": v.label, "trend": round(v.trend_score, 3),
                            "conviction": round(v.conviction, 3), "notes": v.notes}
                        for k, v in regimes.items()},
            "candidates": [c.compact() for c in candidates],
        }, separators=(",", ":"))

        try:
            raw = self._complete(prompt)
            self.last_raw = raw
            parsed = self._extract_json(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            self.last_error = f"HTTP {exc.code}: {detail}"
            log("WARN", "adjudicator unavailable, continuing deterministically",
                error=self.last_error)
            return self._passthrough(candidates, f"llm failed: {self.last_error}")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log("WARN", "adjudicator unavailable, continuing deterministically",
                error=self.last_error)
            return self._passthrough(candidates, f"llm failed: {self.last_error}")

        posture = str(parsed.get("posture", "normal")).lower()
        if posture not in POSTURE_MULT:
            posture = "normal"
        cap = POSTURE_MULT[posture]
        by_id = {c.cid: c for c in candidates}
        applied, seen = [], set()

        for d in parsed.get("decisions", []) or []:
            cid = str(d.get("id", ""))
            cand = by_id.get(cid)
            if cand is None:
                # A hallucinated id is dropped on the floor, loudly.
                log("WARN", "adjudicator referenced an unknown candidate", id=cid)
                continue
            seen.add(cid)
            action = str(d.get("action", "approve")).lower()
            mult = clamp(float(d.get("size_multiplier", 1.0) or 0.0), 0.0, 1.0)
            conv = clamp(float(d.get("conviction", 0.5) or 0.0), 0.0, 1.0)
            if action == "reject":
                mult = 0.0
            cand.conviction = clamp(mult * cap, 0.0, 1.0)
            if d.get("thesis"):
                cand.thesis = f"{cand.thesis} | PM: {str(d['thesis'])[:280]}"
            inv = d.get("invalidators") or []
            if isinstance(inv, list):
                cand.invalidators += [str(x)[:140] for x in inv[:3]]
            cand.tags.append(f"llm:{action}")
            applied.append({"id": cid, "action": action, "conviction": conv,
                            "size_multiplier": cand.conviction})

        # Silence is not consent: anything the model did not mention is dropped.
        for cand in candidates:
            if cand.cid not in seen:
                cand.conviction = 0.0
                cand.tags.append("llm:unreviewed")
                applied.append({"id": cand.cid, "action": "unreviewed",
                                "conviction": 0.0, "size_multiplier": 0.0})

        log("INFO", "adjudicated", posture=posture, model=self.model,
            approved=sum(1 for a in applied if a["size_multiplier"] > 0),
            reviewed=len(applied))
        return {
            "posture": posture,
            "posture_reason": str(parsed.get("posture_reason", ""))[:300],
            "applied": applied, "used_llm": True, "model": self.model,
            "raw": raw[:4000],
        }

    @staticmethod
    def _passthrough(candidates: list, why: str) -> dict:
        """No model: the deterministic score becomes the conviction dial."""
        applied = []
        for c in candidates:
            c.conviction = clamp(0.35 + 0.65 * c.score, 0.0, 1.0)
            c.tags.append("deterministic")
            applied.append({"id": c.cid, "action": "approve",
                            "conviction": c.conviction,
                            "size_multiplier": c.conviction})
        return {"posture": "normal", "posture_reason": why, "applied": applied,
                "used_llm": False, "model": "", "raw": ""}
