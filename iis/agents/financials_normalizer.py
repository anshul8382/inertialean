from __future__ import annotations

from iis.agents.base import BaseAgent
from iis.finance.ratios import build_period_metrics, compute_all_ratios
from iis.finance.flags import compute_data_flags, flags_to_payload
from iis.models.artifacts import AgentArtifact
from iis.models.enums import EventType, SourceTier
from iis.models.session import ResearchSession


class FinancialsNormalizerAgent(BaseAgent):
    agent_id = "financials_normalizer"

    def run(self, session: ResearchSession) -> ResearchSession:
        ratio_pack = compute_all_ratios(session.financial_line_items)
        period_metrics = build_period_metrics(session.financial_line_items)

        segment_items = [
            i.model_dump()
            for i in session.financial_line_items
            if i.statement == "segments" and i.line_item not in ("revenues", "total_revenues", "currency")
        ]

        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "normalized_financials"
        ]

        artifact = AgentArtifact(
            agent_id=self.agent_id,
            artifact_type="normalized_financials",
            payload={
                "periods": ratio_pack["periods"],
                "metrics_by_period": {p: pm.values for p, pm in period_metrics.items()},
                "computed_ratios": ratio_pack["ratios_by_period"],
                "latest_period": ratio_pack["latest_period"],
                "latest_ratios": ratio_pack["latest_ratios"],
                "ratio_catalog": ratio_pack["catalog"],
                "segments": segment_items[:50],
            },
            provenance=["financial_line_items"],
            source_tier=SourceTier.AGENT,
        )
        session.agent_artifacts.append(artifact)

        flag_list = compute_data_flags(session)
        session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "data_flags"]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id=self.agent_id,
                artifact_type="data_flags",
                payload=flags_to_payload(flag_list),
                provenance=["financial_line_items", "guidance_records"],
                source_tier=SourceTier.EXTRACTED,
            )
        )
        session.add_event(
            EventType.INFERENCE,
            agent=self.agent_id,
            details={
                "periods": len(ratio_pack["periods"]),
                "ratios_computed": len(ratio_pack["catalog"]),
                "data_flags": len(flag_list),
            },
        )
        return session
