import json
import logging
from statistics import mean

import httpx

from app.config import LLMConfig
from app.models import AggregatedAnalysis, EventPacket, ModelAnalysis
from app.prompts import SYSTEM_PROMPT, make_user_prompt
from app.services.runtime_config import RuntimeConfigService

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self, runtime_config: RuntimeConfigService):
        self.runtime_config = runtime_config

    async def analyze(self, packet: EventPacket) -> AggregatedAnalysis:
        target_market = packet.candidate_markets[0]
        runtime = await self.runtime_config.snapshot()
        llms = runtime.llms
        outputs: list[ModelAnalysis] = []
        for llm in llms:
            outputs.append(await self._analyze_with_one(llm, packet, target_market.question))

        if not outputs:
            outputs = [
                ModelAnalysis(
                    model_name="fallback",
                    thesis="No active LLM configured; cannot infer directional edge.",
                    probability_shift=0.0,
                    confidence=0.0,
                    risks=["no_models_configured"],
                    recommended_side="SKIP",
                    time_horizon_hours=24,
                )
            ]

        yes_votes = sum(1 for o in outputs if o.recommended_side == "YES")
        no_votes = sum(1 for o in outputs if o.recommended_side == "NO")
        consensus_side = "SKIP"
        if yes_votes > no_votes:
            consensus_side = "YES"
        elif no_votes > yes_votes:
            consensus_side = "NO"

        consensus_confidence = float(mean([o.confidence for o in outputs]))
        summary = " | ".join(f"{o.model_name}: {o.thesis[:140]}" for o in outputs)
        return AggregatedAnalysis(
            packet=packet,
            model_outputs=outputs,
            consensus_side=consensus_side,
            consensus_confidence=consensus_confidence,
            summary_reasoning=summary,
        )

    async def _analyze_with_one(
        self, llm: LLMConfig, packet: EventPacket, market_question: str
    ) -> ModelAnalysis:
        user_prompt = make_user_prompt(
            packet.world_event.title,
            packet.world_event.summary,
            market_question,
        )
        payload = {
            "model": llm.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {llm.api_key}"}
        url = llm.base_url.rstrip("/") + "/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            side_raw = str(parsed.get("recommended_side", "SKIP")).upper()
            side = side_raw if side_raw in {"YES", "NO", "SKIP"} else "SKIP"
            return ModelAnalysis(
                model_name=llm.name,
                thesis=str(parsed.get("thesis", ""))[:400],
                probability_shift=float(parsed.get("probability_shift", 0.0)),
                confidence=float(parsed.get("confidence", 0.0)),
                risks=[str(x) for x in parsed.get("risks", [])][:5],
                recommended_side=side,
                time_horizon_hours=int(parsed.get("time_horizon_hours", 24)),
            )
        except Exception as exc:
            logger.exception("LLM analysis failed for %s: %s", llm.name, exc)
            return ModelAnalysis(
                model_name=llm.name,
                thesis=f"Model error: {type(exc).__name__}",
                probability_shift=0.0,
                confidence=0.0,
                risks=["model_error"],
                recommended_side="SKIP",
                time_horizon_hours=24,
            )
