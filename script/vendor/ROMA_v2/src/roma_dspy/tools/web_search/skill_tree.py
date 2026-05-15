"""Skill-tree-based web search toolkit.

This toolkit wraps local `web-search-innospark-tree` scripts and normalizes
heterogeneous outputs into a stable `results` schema compatible with
AdaptiveRetrieveToolkit's web context formatter.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from roma_dspy.tools.base.base import BaseToolkit


def _discover_bundle_root() -> Path:
    """Find the standalone script bundle root from this vendored file."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "skills" / "web-search-innospark-tree").exists():
            return parent
    return Path.cwd()


class SkillTreeWebSearchToolkit(BaseToolkit):
    """Web search toolkit backed by local skill-tree scripts."""

    def _setup_dependencies(self) -> None:
        self.python_bin = self.config.get("python_bin", "python3")
        self.shell_bin = self.config.get("shell_bin", "bash")

    def _initialize_tools(self) -> None:
        bundle_root = _discover_bundle_root()
        default_root = bundle_root / "skills" / "web-search-innospark-tree"
        self.skill_root = Path(
            self.config.get("skill_root")
            or os.getenv("WEB_SEARCH_SKILL_ROOT")
            or default_root
        ).expanduser()

        self.default_sources = self.config.get(
            "default_sources", "tavily,bilibili,zhihu,youtube,duckduckgo"
        )
        self.timeout_seconds = int(self.config.get("timeout_seconds", 90))
        self.max_chars_per_item = int(self.config.get("max_chars_per_item", 1600))
        self.route_by_skill_tree = bool(self.config.get("route_by_skill_tree", True))
        self.include_provider_summaries = bool(
            self.config.get("include_provider_summaries", False)
        )
        self.max_results_per_provider = int(self.config.get("max_results_per_provider", 4))
        self.max_results_per_provider_unlimited = int(
            self.config.get("max_results_per_provider_unlimited", 8)
        )
        self.max_rss_results = int(self.config.get("max_rss_results", 3))
        self.primary_max_results = int(self.config.get("primary_max_results", 12))
        self.tavily_search_depth = str(
            self.config.get("tavily_search_depth", "advanced")
        ).lower()
        self.tavily_max_results = int(self.config.get("tavily_max_results", 20))
        self.tavily_min_score = float(self.config.get("tavily_min_score", 0.05))

        # Optional union-search aggregation layer for broader coverage.
        default_union_root = bundle_root / "skills" / "union-search-skill"
        self.union_search_enabled = bool(self.config.get("union_search_enabled", True))
        self.union_trigger_mode = str(self.config.get("union_trigger_mode", "auto")).lower()
        self.union_trigger_min_results = int(self.config.get("union_trigger_min_results", 8))
        self.union_trigger_min_providers = int(self.config.get("union_trigger_min_providers", 4))
        self.union_timeout_seconds = int(self.config.get("union_timeout_seconds", 35))
        self.union_max_workers = int(self.config.get("union_max_workers", 4))
        self.union_limit_per_platform = int(self.config.get("union_limit_per_platform", 3))
        self.unlimited_results = bool(self.config.get("unlimited_results", False))
        self.unlimited_union_limit_per_platform = int(
            self.config.get("unlimited_union_limit_per_platform", 20)
        )
        self.rss_fallback_enabled = bool(self.config.get("rss_fallback_enabled", True))
        self.rss_fallback_min_results = int(self.config.get("rss_fallback_min_results", 1))
        self.rss_fallback_limit = int(self.config.get("rss_fallback_limit", 20))
        self.rss_fallback_runtime_limit = int(self.config.get("rss_fallback_runtime_limit", 6))
        self.rss_fallback_max_feeds = int(self.config.get("rss_fallback_max_feeds", 8))
        self.rss_fallback_retry_feeds = int(self.config.get("rss_fallback_retry_feeds", 1))
        self.rss_timeout_seconds = int(self.config.get("rss_timeout_seconds", 70))
        self.rss_feed_timeout_seconds = int(self.config.get("rss_feed_timeout_seconds", 8))
        self.union_platforms = self.config.get(
            "union_platforms",
            "metaso,tavily,zhihu,bilibili,twitter,youtube,wikipedia,xiaoyuzhoufm,douyin,rss",
        )
        # Optional news-aggregator fallback for source diversity when union/provider
        # backends are partially unavailable.
        default_news_aggregator_root = bundle_root / "skills" / "news-aggregator-skill"
        self.news_aggregator_enabled = bool(self.config.get("news_aggregator_enabled", True))
        self.news_aggregator_min_providers = int(
            self.config.get("news_aggregator_min_providers", 3)
        )
        self.news_aggregator_trigger_min_results = int(
            self.config.get("news_aggregator_trigger_min_results", 6)
        )
        self.news_aggregator_limit = int(self.config.get("news_aggregator_limit", 4))
        self.news_aggregator_max_results = int(
            self.config.get("news_aggregator_max_results", 10)
        )
        self.news_aggregator_timeout_seconds = int(
            self.config.get("news_aggregator_timeout_seconds", 80)
        )
        self.news_aggregator_sources_general = str(
            self.config.get(
                "news_aggregator_sources_general",
                "hackernews,github,36kr,v2ex,tencent,wallstreetcn,producthunt,latentspace_ainews",
            )
        )
        self.news_aggregator_sources_ai = str(
            self.config.get(
                "news_aggregator_sources_ai",
                "hackernews,github,36kr,producthunt,huggingface,latentspace_ainews,ai_newsletters",
            )
        )
        # Source-mix enforcement: ensure key channels are represented in final output.
        self.source_mix_enforcement_enabled = bool(
            self.config.get("source_mix_enforcement_enabled", True)
        )
        self.min_bilibili_results = int(self.config.get("min_bilibili_results", 1))
        self.min_youtube_results = int(self.config.get("min_youtube_results", 1))
        self.min_wikipedia_results = int(self.config.get("min_wikipedia_results", 1))
        self.min_rss_mix_results = int(self.config.get("min_rss_mix_results", 1))
        self.source_mix_target_limit = int(self.config.get("source_mix_target_limit", 4))
        self.source_mix_union_platforms = str(
            self.config.get("source_mix_union_platforms", "wikipedia,metaso,tavily")
        )
        self.source_mix_domain_union_platforms = str(
            self.config.get("source_mix_domain_union_platforms", "metaso,tavily,duckduckgo,brave")
        )
        # Academic/policy routes intentionally favor structured and official
        # sources over social/video/news channels.
        self.academic_route_enabled = bool(self.config.get("academic_route_enabled", True))
        self.policy_route_enabled = bool(self.config.get("policy_route_enabled", True))
        self.academic_sources = str(
            self.config.get(
                "academic_sources",
                "academic_research,tavily,semantic_scholar,openalex,crossref,scholar,duckduckgo",
            )
        )
        self.policy_sources = str(
            self.config.get("policy_sources", "tavily,academic_research,duckduckgo")
        )
        self.academic_union_platforms = str(
            self.config.get(
                "academic_union_platforms",
                "google,tavily,brave,duckduckgo,jina,metaso,wikipedia,rss",
            )
        )
        self.policy_union_platforms = str(
            self.config.get(
                "policy_union_platforms",
                (
                    "tavily,metaso,volcengine,wikipedia,github,zhihu,twitter,"
                    "douyin,xiaoyuzhoufm,bilibili,youtube"
                ),
            )
        )
        self.academic_domain_union_platforms = str(
            self.config.get(
                "academic_domain_union_platforms",
                "google,brave,duckduckgo,tavily,jina,metaso",
            )
        )
        self.policy_domain_union_platforms = str(
            self.config.get(
                "policy_domain_union_platforms",
                "metaso",
            )
        )
        self.academic_domains = self._split_csv(
            self.config.get(
                "academic_domains",
                (
                    "semanticscholar.org,openalex.org,crossref.org,scholar.google.com,"
                    "doi.org,papers.ssrn.com,nber.org,jstor.org,sciencedirect.com,"
                    "springer.com,tandfonline.com"
                ),
            )
        )
        self.policy_domains = self._split_csv(
            self.config.get(
                "policy_domains",
                (
                    "go.jp,digital.go.jp,cio.go.jp,e-gov.go.jp,soumu.go.jp,"
                    "go.kr,mois.go.kr,korea.kr,law.go.kr,msit.go.kr,gov.sg,"
                    "smartnation.gov.sg,tech.gov.sg,go.th,dga.or.th,mdes.go.th,"
                    "gov.uk,gov,europa.eu,oecd.org,worldbank.org,un.org,itu.int,"
                    "adb.org,imf.org,nist.gov"
                ),
            )
        )
        self.profile_domain_search_enabled = bool(
            self.config.get("profile_domain_search_enabled", True)
        )
        self.profile_domain_limit = int(self.config.get("profile_domain_limit", 8))
        self.profile_domain_result_limit = int(
            self.config.get("profile_domain_result_limit", 3)
        )
        self.profile_domain_trigger_min_results = int(
            self.config.get("profile_domain_trigger_min_results", 8)
        )
        self.profile_rebalance_enabled = bool(
            self.config.get("profile_rebalance_enabled", True)
        )
        self.policy_min_official_results = int(
            self.config.get("policy_min_official_results", 1)
        )
        self.policy_min_media_results = int(
            self.config.get("policy_min_media_results", 1)
        )
        self.policy_per_domain_result_cap = int(
            self.config.get("policy_per_domain_result_cap", 2)
        )
        self.policy_academic_result_cap = int(
            self.config.get("policy_academic_result_cap", 4)
        )
        self.policy_social_result_cap = int(
            self.config.get("policy_social_result_cap", 2)
        )
        self.policy_authoritative_domains = self._split_csv(
            self.config.get(
                "policy_authoritative_domains",
                (
                    "mof.gov.cn,stats.gov.cn,mnr.gov.cn,landchina.com,"
                    "chinatax.gov.cn,shanghai.chinatax.gov.cn,cq.gov.cn,"
                    "mohurd.gov.cn,npc.gov.cn,pbc.gov.cn,ndrc.gov.cn,cass.cn,"
                    "gov.sg,smartnation.gov.sg,tech.gov.sg,imda.gov.sg,"
                    "nas.gov.sg,nlb.gov.sg,ida.gov.sg,ndlb.gov.sg,"
                    "go.jp,digital.go.jp,cio.go.jp,e-gov.go.jp,soumu.go.jp,"
                    "japan.kantei.go.jp,go.kr,mois.go.kr,korea.kr,law.go.kr,"
                    "msit.go.kr,moleg.go.kr,go.th,dga.or.th,mdes.go.th,"
                    "gov.uk,europa.eu,oecd.org,worldbank.org,un.org,itu.int,"
                    "adb.org,imf.org,nist.gov"
                ),
            )
        )
        self.policy_media_domains = self._split_csv(
            self.config.get(
                "policy_media_domains",
                (
                    "xinhuanet.com,people.com.cn,chinanews.com,jjckb.xinhuanet.com,"
                    "yicai.com,21jingji.com,caixin.com,thepaper.cn,cnstock.com"
                ),
            )
        )
        self.academic_citation_domains = self._split_csv(
            self.config.get(
                "academic_citation_domains",
                (
                    "doi.org,semanticscholar.org,openalex.org,crossref.org,"
                    "scholar.google.com,researchgate.net,mdpi.com,springer.com,"
                    "sciencedirect.com,tandfonline.com,cqvip.com"
                ),
            )
        )
        self.academic_disable_news_aggregator = bool(
            self.config.get("academic_disable_news_aggregator", True)
        )
        self.policy_disable_news_aggregator = bool(
            self.config.get("policy_disable_news_aggregator", True)
        )
        self.academic_disable_source_mix = bool(
            self.config.get("academic_disable_source_mix", True)
        )
        self.policy_disable_source_mix = bool(
            self.config.get("policy_disable_source_mix", True)
        )
        self.academic_include_rss_candidates = bool(
            self.config.get("academic_include_rss_candidates", True)
        )
        self.academic_disable_rss_fallback = bool(
            self.config.get("academic_disable_rss_fallback", False)
        )
        self.policy_disable_rss_fallback = bool(
            self.config.get("policy_disable_rss_fallback", True)
        )
        self.policy_include_social_candidates = bool(
            self.config.get("policy_include_social_candidates", False)
        )
        self.union_root = Path(
            self.config.get("union_search_root")
            or os.getenv("WEB_SEARCH_UNION_ROOT")
            or default_union_root
        ).expanduser()
        self.news_aggregator_root = Path(
            self.config.get("news_aggregator_root")
            or os.getenv("WEB_SEARCH_NEWS_AGGREGATOR_ROOT")
            or default_news_aggregator_root
        ).expanduser()

        self.global_search_script = self.skill_root / "scripts" / "global_search_multi.py"
        self.run_global_script = self.skill_root / "scripts" / "run_global_search.sh"
        self.union_cli_script = self.union_root / "scripts" / "cli" / "main.py"
        self.rss_search_script = self.union_root / "scripts" / "rss_search" / "rss_search.py"
        self.news_aggregator_script = (
            self.news_aggregator_root / "scripts" / "fetch_news.py"
        )
        self.rss_feeds_file = Path(
            self.config.get("rss_feeds_file")
            or (self.union_root / "scripts" / "rss_search" / "rss_feeds.txt")
        ).expanduser()

        if not self.skill_root.exists():
            raise ValueError(f"skill_root does not exist: {self.skill_root}")

    async def search(
        self,
        query: str,
        top_n: int = 10,
        sources: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search via skill-tree scripts and return normalized web results."""
        query = self._sanitize_query_for_search(query)
        route_plan = self._build_route_plan(query=query, sources=sources, top_n=top_n)
        source_spec = route_plan["primary_sources"]

        primary_cmd = [
            self.python_bin,
            str(self.global_search_script),
            "--query",
            query,
            "--sources",
            source_spec,
        ]
        primary_cmd.extend(self._global_search_cli_options(route_plan))
        primary = await self._run_json_cmd(primary_cmd, timeout=self.timeout_seconds)
        normalized = self._normalize_aggregated_result(
            primary,
            top_n=top_n,
            query=query,
            query_profile=route_plan.get("query_profile"),
        )
        normalized["provider_health"] = self._source_health(primary)
        normalized["backend"] = "skill_tree:global_search_multi"
        normalized["skill_tree_route"] = route_plan

        if self._needs_union_enrichment(
            normalized,
            top_n=top_n,
            force=bool(route_plan.get("force_union", False)),
        ):
            union_raw = await self._run_union_search(
                query=query,
                union_platforms=route_plan.get("union_platforms"),
                union_limit=route_plan.get("union_limit_per_platform"),
            )
            union_norm = self._normalize_union_envelope(
                union_raw,
                top_n=max(
                    int(top_n) * 8,
                    int(top_n) + 100,
                ) if self.unlimited_results else max(int(top_n) * 4, int(top_n) + 24),
                query=query,
                query_profile=route_plan.get("query_profile"),
            )
            merge_limit = None if self.unlimited_results else int(top_n)
            merged = self._merge_normalized_results(normalized, union_norm, limit=merge_limit)
            merged["provider_health"] = {
                **(normalized.get("provider_health") or {}),
                **(union_norm.get("provider_health") or {}),
            }
            merged["backend"] = "skill_tree:global+union"
            merged["skill_tree_route"] = route_plan
            merged = await self._maybe_merge_profile_domain_fallback(
                base_result=merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )
            merged = await self._maybe_apply_news_aggregator_fallback(
                base_result=merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )
            merged = await self._maybe_apply_rss_fallback(
                base_result=merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )
            if self._count_citable_results(merged) > 0:
                return await self._maybe_apply_source_mix(
                    base_result=merged,
                    query=query,
                    top_n=top_n,
                    route_plan=route_plan,
                )

            # RSS fallback from local skill inventory when paid/API providers fail.
            rss_merged = await self._maybe_apply_rss_fallback(
                base_result=merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )
            if rss_merged.get("results"):
                return await self._maybe_apply_source_mix(
                    base_result=rss_merged,
                    query=query,
                    top_n=top_n,
                    route_plan=route_plan,
                )

        normalized = await self._maybe_merge_profile_domain_fallback(
            base_result=normalized,
            query=query,
            top_n=top_n,
            route_plan=route_plan,
        )
        normalized = await self._maybe_apply_news_aggregator_fallback(
            base_result=normalized,
            query=query,
            top_n=top_n,
            route_plan=route_plan,
        )
        normalized = await self._maybe_apply_rss_fallback(
            base_result=normalized,
            query=query,
            top_n=top_n,
            route_plan=route_plan,
        )
        if self._count_citable_results(normalized) > 0:
            return await self._maybe_apply_source_mix(
                base_result=normalized,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )

        fallback_cmd = [
            self.shell_bin,
            str(self.run_global_script),
            "--query",
            query,
            "--tavily-depth",
            str(route_plan.get("tavily_depth") or self.tavily_search_depth),
            "--tavily-max-results",
            str(route_plan.get("tavily_max_results") or self.tavily_max_results),
        ]
        fallback = await self._run_json_cmd(fallback_cmd, timeout=self.timeout_seconds)
        normalized_fallback = self._normalize_aggregated_result(
            fallback,
            top_n=top_n,
            query=query,
            query_profile=route_plan.get("query_profile"),
        )
        normalized_fallback["provider_health"] = self._source_health(fallback)
        normalized_fallback["backend"] = "skill_tree:run_global_search"
        normalized_fallback["skill_tree_route"] = route_plan

        if self._needs_union_enrichment(
            normalized_fallback,
            top_n=top_n,
            force=bool(route_plan.get("force_union", False)),
        ):
            union_raw = await self._run_union_search(
                query=query,
                union_platforms=route_plan.get("union_platforms"),
                union_limit=route_plan.get("union_limit_per_platform"),
            )
            union_norm = self._normalize_union_envelope(
                union_raw,
                top_n=max(
                    int(top_n) * 8,
                    int(top_n) + 100,
                ) if self.unlimited_results else max(int(top_n) * 4, int(top_n) + 24),
                query=query,
                query_profile=route_plan.get("query_profile"),
            )
            merge_limit = None if self.unlimited_results else int(top_n)
            merged = self._merge_normalized_results(normalized_fallback, union_norm, limit=merge_limit)
            merged["provider_health"] = {
                **(normalized_fallback.get("provider_health") or {}),
                **(union_norm.get("provider_health") or {}),
            }
            merged["backend"] = "skill_tree:fallback+union"
            merged["skill_tree_route"] = route_plan
            merged = await self._maybe_merge_profile_domain_fallback(
                base_result=merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )
            merged = await self._maybe_apply_news_aggregator_fallback(
                base_result=merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )
            rss_merged = await self._maybe_apply_rss_fallback(
                base_result=merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )
            return await self._maybe_apply_source_mix(
                base_result=rss_merged,
                query=query,
                top_n=top_n,
                route_plan=route_plan,
            )

        normalized_fallback = await self._maybe_merge_profile_domain_fallback(
            base_result=normalized_fallback,
            query=query,
            top_n=top_n,
            route_plan=route_plan,
        )
        normalized_fallback = await self._maybe_apply_news_aggregator_fallback(
            base_result=normalized_fallback,
            query=query,
            top_n=top_n,
            route_plan=route_plan,
        )
        rss_merged = await self._maybe_apply_rss_fallback(
            base_result=normalized_fallback,
            query=query,
            top_n=top_n,
            route_plan=route_plan,
        )
        return await self._maybe_apply_source_mix(
            base_result=rss_merged,
            query=query,
            top_n=top_n,
            route_plan=route_plan,
        )

    def _count_citable_results(self, payload: Dict[str, Any]) -> int:
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return 0
        return sum(
            1
            for item in results
            if isinstance(item, dict) and str(item.get("url", "")).strip()
        )

    def _sanitize_query_for_search(self, query: str) -> str:
        """Remove benchmark guardrail blocks before sending text to search engines."""
        text = str(query or "")
        text = re.sub(
            r"\*\*important\*\*.*?(?:do not quote it\.\*\*|do not quote it\.)",
            " ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"the following is a rule of highest priority.*?(?:do not quote it\.\*\*|do not quote it\.)",
            " ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(r"\s+", " ", text).strip()
        return text or str(query or "")

    def _global_search_cli_options(self, route_plan: Dict[str, Any]) -> List[str]:
        return [
            "--max-results",
            str(route_plan.get("primary_max_results") or self.primary_max_results),
            "--tavily-depth",
            str(route_plan.get("tavily_depth") or self.tavily_search_depth),
            "--tavily-max-results",
            str(route_plan.get("tavily_max_results") or self.tavily_max_results),
        ]

    async def _maybe_apply_news_aggregator_fallback(
        self,
        base_result: Dict[str, Any],
        query: str,
        top_n: int,
        route_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        if route_plan.get("disable_news_aggregator"):
            return base_result
        return await self._maybe_merge_news_aggregator_fallback(
            base_result=base_result,
            query=query,
            top_n=top_n,
        )

    async def _maybe_apply_rss_fallback(
        self,
        base_result: Dict[str, Any],
        query: str,
        top_n: int,
        route_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        if route_plan.get("disable_rss_fallback"):
            return base_result
        return await self._maybe_merge_rss_fallback(
            base_result=base_result,
            query=query,
            top_n=top_n,
            force=bool(route_plan.get("force_rss_fallback", False)),
        )

    async def _maybe_apply_source_mix(
        self,
        base_result: Dict[str, Any],
        query: str,
        top_n: int,
        route_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        if route_plan.get("disable_source_mix_enforcement"):
            return self._rebalance_for_profile(base_result, top_n=top_n, route_plan=route_plan)
        mixed = await self._maybe_enforce_source_mix(
            base_result=base_result,
            query=query,
            top_n=top_n,
        )
        return self._rebalance_for_profile(mixed, top_n=top_n, route_plan=route_plan)

    async def _maybe_merge_profile_domain_fallback(
        self,
        base_result: Dict[str, Any],
        query: str,
        top_n: int,
        route_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self.profile_domain_search_enabled:
            return base_result

        profile = str(route_plan.get("query_profile") or "general")
        if profile not in ("academic", "policy"):
            return base_result

        current_count = self._count_citable_results(base_result)
        trigger_floor = min(
            max(1, int(top_n)),
            max(1, int(self.profile_domain_trigger_min_results)),
        )
        should_search_domains = current_count < trigger_floor
        if profile == "policy":
            bucket_counts = self._policy_bucket_counts(base_result)
            should_search_domains = (
                should_search_domains
                or bucket_counts.get("official", 0) < max(0, int(self.policy_min_official_results))
                or bucket_counts.get("media", 0) < max(0, int(self.policy_min_media_results))
            )
        if not should_search_domains:
            return base_result

        domains = self._select_profile_domains(query=query, profile=profile)
        if not domains:
            return base_result

        merged = base_result
        for domain in domains:
            if profile == "policy":
                bucket_counts = self._policy_bucket_counts(merged)
                enough_policy_mix = (
                    bucket_counts.get("official", 0) >= max(0, int(self.policy_min_official_results))
                    and bucket_counts.get("media", 0) >= max(0, int(self.policy_min_media_results))
                    and self._count_citable_results(merged) >= trigger_floor
                )
                if enough_policy_mix:
                    break
            domain_raw = await self._run_union_domain_search(
                query=query,
                domain=domain,
                union_platforms=route_plan.get("profile_domain_union_platforms"),
                union_limit=max(
                    1,
                    int(
                        route_plan.get("profile_domain_result_limit")
                        or self.profile_domain_result_limit
                    ),
                ),
            )
            domain_norm = self._normalize_union_envelope(
                domain_raw,
                top_n=max(4, int(top_n) * 2),
                query=query,
                query_profile=profile,
            )
            if not domain_norm.get("results"):
                continue
            merge_limit = None if self.unlimited_results else int(top_n)
            merged = self._merge_normalized_results(merged, domain_norm, limit=merge_limit)
            merged["provider_health"] = {
                **(merged.get("provider_health") or {}),
                **(domain_norm.get("provider_health") or {}),
            }
            merged["backend"] = f"{merged.get('backend', 'skill_tree')}+{profile}_domain"
        return merged

    def _rebalance_for_profile(
        self,
        payload: Dict[str, Any],
        top_n: int,
        route_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self.profile_rebalance_enabled:
            return payload

        profile = str(route_plan.get("query_profile") or "general")
        if profile != "policy":
            return payload

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return payload

        buckets: Dict[str, List[Dict[str, Any]]] = {
            "official": [],
            "media": [],
            "general": [],
            "academic": [],
            "social": [],
        }
        for item in results:
            if not isinstance(item, dict):
                continue
            bucket = self._policy_source_bucket(item)
            buckets.setdefault(bucket, []).append(item)

        for items in buckets.values():
            items.sort(key=lambda x: self._parse_score(x.get("score"), 0.0), reverse=True)

        picked: List[Dict[str, Any]] = []
        picked_keys: set[str] = set()
        picked_domains: Dict[str, int] = {}

        def take(
            items: List[Dict[str, Any]],
            cap: Optional[int] = None,
            domain_cap: Optional[int] = None,
        ) -> None:
            count = 0
            for item in items:
                if cap is not None and count >= cap:
                    break
                key = str(item.get("url") or item.get("title") or id(item)).strip()
                if not key or key in picked_keys:
                    continue
                domain = self._url_domain(str(item.get("url") or ""))
                if (
                    domain_cap is not None
                    and domain
                    and picked_domains.get(domain, 0) >= max(1, int(domain_cap))
                ):
                    continue
                picked_keys.add(key)
                if domain:
                    picked_domains[domain] = picked_domains.get(domain, 0) + 1
                picked.append(item)
                count += 1

        per_domain_cap = max(1, int(self.policy_per_domain_result_cap))
        take(buckets.get("official", []), domain_cap=per_domain_cap)
        take(buckets.get("media", []), domain_cap=per_domain_cap)
        take(buckets.get("academic", []), cap=max(0, int(self.policy_academic_result_cap)))
        take(buckets.get("general", []), domain_cap=per_domain_cap)
        take(buckets.get("social", []), cap=max(0, int(self.policy_social_result_cap)))

        out = dict(payload)
        if self.unlimited_results:
            max_items = max(int(top_n) * 3, int(top_n) + 24)
            out["results"] = picked[:max_items]
        else:
            out["results"] = picked[: max(1, int(top_n))]
        out["profile_rebalanced"] = "policy"
        return out

    def _policy_bucket_counts(self, payload: Dict[str, Any]) -> Dict[str, int]:
        counts: Dict[str, int] = {
            "official": 0,
            "media": 0,
            "general": 0,
            "academic": 0,
            "social": 0,
        }
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return counts
        for item in results:
            if not isinstance(item, dict):
                continue
            bucket = self._policy_source_bucket(item)
            counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    def _policy_source_bucket(self, item: Dict[str, Any]) -> str:
        domain = self._url_domain(str(item.get("url") or ""))
        provider = str(item.get("provider") or "").strip().lower()

        if domain == "gov.cn":
            return "official"
        if self._domain_matches(domain, self.policy_authoritative_domains):
            return "official"
        if self._domain_matches(domain, self.policy_media_domains):
            return "media"
        if self._domain_matches(domain, self.academic_citation_domains):
            return "academic"
        if provider in {"academic_research", "semantic_scholar", "openalex", "crossref", "scholar"}:
            return "academic"
        if provider in {
            "bilibili", "youtube", "twitter", "reddit", "douyin", "weibo",
            "xiaohongshu", "xiaoyuzhoufm",
        }:
            return "social"
        if any(
            marker in domain
            for marker in ("bilibili.com", "youtube.com", "youtu.be", "zhihu.com")
        ):
            return "social"
        return "general"

    def _url_domain(self, url: str) -> str:
        if not url:
            return ""
        parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def _domain_matches(self, domain: str, candidates: Sequence[str]) -> bool:
        if not domain:
            return False
        for raw in candidates:
            candidate = str(raw or "").strip().lower()
            if not candidate:
                continue
            if domain == candidate or domain.endswith(f".{candidate}"):
                return True
        return False

    async def _maybe_merge_rss_fallback(
        self,
        base_result: Dict[str, Any],
        query: str,
        top_n: int,
        force: bool = False,
    ) -> Dict[str, Any]:
        if not self.rss_fallback_enabled:
            return base_result

        current = base_result.get("results") if isinstance(base_result, dict) else None
        current_count = len(current) if isinstance(current, list) else 0
        if not force and current_count >= max(1, self.rss_fallback_min_results):
            return base_result

        rss_raw = await self._run_rss_search(query=query, top_n=top_n)
        rss_norm = self._normalize_rss_result(rss_raw, top_n=top_n)
        if not rss_norm.get("results"):
            return base_result

        merge_limit = None if self.unlimited_results else int(top_n)
        merged = self._merge_normalized_results(base_result, rss_norm, limit=merge_limit)
        merged["backend"] = f"{base_result.get('backend', 'skill_tree')}+rss_fallback"
        merged["provider_health"] = {
            **(base_result.get("provider_health") or {}),
            **(rss_norm.get("provider_health") or {"rss": "ok"}),
        }
        return merged

    async def _maybe_merge_news_aggregator_fallback(
        self,
        base_result: Dict[str, Any],
        query: str,
        top_n: int,
    ) -> Dict[str, Any]:
        if not self.news_aggregator_enabled:
            return base_result
        if not self._needs_news_aggregator_fallback(base_result, top_n=top_n):
            return base_result

        news_raw = await self._run_news_aggregator_search(query=query)
        news_norm = self._normalize_news_aggregator_result(news_raw, top_n=top_n)
        if not news_norm.get("results"):
            return base_result

        merge_limit = None if self.unlimited_results else int(top_n)
        merged = self._merge_normalized_results(base_result, news_norm, limit=merge_limit)
        merged["backend"] = f"{base_result.get('backend', 'skill_tree')}+news_aggregator"
        merged["provider_health"] = {
            **(base_result.get("provider_health") or {}),
            **(news_norm.get("provider_health") or {}),
        }
        return merged

    def _needs_news_aggregator_fallback(
        self,
        payload: Dict[str, Any],
        top_n: int,
    ) -> bool:
        results = payload.get("results") if isinstance(payload, dict) else None
        items = results if isinstance(results, list) else []
        citable_count = self._count_citable_results(payload)
        if citable_count <= 0:
            return True

        result_floor = min(
            max(1, int(top_n) if int(top_n) > 0 else 1),
            max(1, self.news_aggregator_trigger_min_results),
        )
        if len(items) < result_floor:
            return True

        providers = {
            str(item.get("provider", "")).strip()
            for item in items
            if isinstance(item, dict) and str(item.get("url", "")).strip()
        }
        providers.discard("")
        non_rss = {p for p in providers if p != "rss"}

        if providers == {"rss"}:
            return True
        if len(non_rss) < max(1, self.news_aggregator_min_providers - 1):
            return True
        return False

    async def _maybe_enforce_source_mix(
        self,
        base_result: Dict[str, Any],
        query: str,
        top_n: int,
    ) -> Dict[str, Any]:
        if not self.source_mix_enforcement_enabled:
            return base_result

        merged = base_result
        deficits = self._source_mix_deficits(merged)
        if not deficits:
            return self._rebalance_for_source_mix(merged, top_n=top_n)

        video_sources: List[str] = []
        if deficits.get("bilibili", 0) > 0:
            video_sources.append("bilibili")
        if deficits.get("youtube", 0) > 0:
            video_sources.append("youtube")
        if video_sources:
            video_raw = await self._run_primary_targeted_search(
                query=query,
                sources=",".join(video_sources),
                top_n=max(2, int(self.source_mix_target_limit)),
            )
            video_norm = self._normalize_aggregated_result(
                video_raw,
                top_n=max(4, int(self.source_mix_target_limit) * 2),
            )
            if video_norm.get("results"):
                merge_limit = None if self.unlimited_results else int(top_n)
                merged = self._merge_normalized_results(merged, video_norm, limit=merge_limit)
                merged["provider_health"] = {
                    **(merged.get("provider_health") or {}),
                    **(self._source_health(video_raw) or {}),
                }
                merged["backend"] = f"{merged.get('backend', 'skill_tree')}+source_mix_video"
        deficits = self._source_mix_deficits(merged)
        if deficits.get("bilibili", 0) > 0:
            bilibili_site = await self._run_union_domain_search(
                query=query,
                domain="bilibili.com",
            )
            bilibili_norm = self._normalize_union_envelope(
                bilibili_site,
                top_n=max(4, int(self.source_mix_target_limit) * 2),
            )
            if bilibili_norm.get("results"):
                merge_limit = None if self.unlimited_results else int(top_n)
                merged = self._merge_normalized_results(merged, bilibili_norm, limit=merge_limit)
                merged["provider_health"] = {
                    **(merged.get("provider_health") or {}),
                    **(bilibili_norm.get("provider_health") or {}),
                }
                merged["backend"] = f"{merged.get('backend', 'skill_tree')}+source_mix_bilibili_domain"

        deficits = self._source_mix_deficits(merged)
        if deficits.get("youtube", 0) > 0:
            youtube_site = await self._run_union_domain_search(
                query=query,
                domain="youtube.com/watch",
            )
            youtube_norm = self._normalize_union_envelope(
                youtube_site,
                top_n=max(4, int(self.source_mix_target_limit) * 2),
            )
            if youtube_norm.get("results"):
                merge_limit = None if self.unlimited_results else int(top_n)
                merged = self._merge_normalized_results(merged, youtube_norm, limit=merge_limit)
                merged["provider_health"] = {
                    **(merged.get("provider_health") or {}),
                    **(youtube_norm.get("provider_health") or {}),
                }
                merged["backend"] = f"{merged.get('backend', 'skill_tree')}+source_mix_youtube_domain"

        deficits = self._source_mix_deficits(merged)
        if deficits.get("wikipedia", 0) > 0 and self.union_search_enabled:
            wiki_direct_raw = await self._run_union_search(
                query=query,
                union_platforms="wikipedia",
                union_limit=max(1, int(self.source_mix_target_limit)),
            )
            wiki_direct_norm = self._normalize_union_envelope(
                wiki_direct_raw,
                top_n=max(4, int(self.source_mix_target_limit) * 2),
            )
            if wiki_direct_norm.get("results"):
                merge_limit = None if self.unlimited_results else int(top_n)
                merged = self._merge_normalized_results(merged, wiki_direct_norm, limit=merge_limit)
                merged["provider_health"] = {
                    **(merged.get("provider_health") or {}),
                    **(wiki_direct_norm.get("provider_health") or {}),
                }
                merged["backend"] = f"{merged.get('backend', 'skill_tree')}+source_mix_wiki_direct"

        deficits = self._source_mix_deficits(merged)
        if deficits.get("wikipedia", 0) > 0 and self.union_search_enabled:
            wiki_raw = await self._run_union_search(
                query=query,
                union_platforms=self.source_mix_union_platforms,
                union_limit=max(1, int(self.source_mix_target_limit)),
            )
            wiki_norm = self._normalize_union_envelope(
                wiki_raw,
                top_n=max(4, int(self.source_mix_target_limit) * 2),
            )
            if wiki_norm.get("results"):
                merge_limit = None if self.unlimited_results else int(top_n)
                merged = self._merge_normalized_results(merged, wiki_norm, limit=merge_limit)
                merged["provider_health"] = {
                    **(merged.get("provider_health") or {}),
                    **(wiki_norm.get("provider_health") or {}),
                }
                merged["backend"] = f"{merged.get('backend', 'skill_tree')}+source_mix_wiki"
        deficits = self._source_mix_deficits(merged)
        if deficits.get("wikipedia", 0) > 0:
            wiki_site = await self._run_union_domain_search(
                query=query,
                domain="wikipedia.org",
            )
            wiki_site_norm = self._normalize_union_envelope(
                wiki_site,
                top_n=max(4, int(self.source_mix_target_limit) * 2),
            )
            if wiki_site_norm.get("results"):
                merge_limit = None if self.unlimited_results else int(top_n)
                merged = self._merge_normalized_results(merged, wiki_site_norm, limit=merge_limit)
                merged["provider_health"] = {
                    **(merged.get("provider_health") or {}),
                    **(wiki_site_norm.get("provider_health") or {}),
                }
                merged["backend"] = f"{merged.get('backend', 'skill_tree')}+source_mix_wiki_domain"

        deficits = self._source_mix_deficits(merged)
        if deficits.get("rss", 0) > 0 and self.rss_fallback_enabled:
            rss_raw = await self._run_rss_search(
                query=query,
                top_n=max(1, int(self.source_mix_target_limit)),
            )
            rss_norm = self._normalize_rss_result(
                rss_raw,
                top_n=max(1, int(self.source_mix_target_limit)),
            )
            if rss_norm.get("results"):
                merge_limit = None if self.unlimited_results else int(top_n)
                merged = self._merge_normalized_results(merged, rss_norm, limit=merge_limit)
                merged["provider_health"] = {
                    **(merged.get("provider_health") or {}),
                    **(rss_norm.get("provider_health") or {"rss": "ok"}),
                }
                merged["backend"] = f"{merged.get('backend', 'skill_tree')}+source_mix_rss"

        return self._rebalance_for_source_mix(merged, top_n=top_n)

    async def _run_primary_targeted_search(
        self,
        query: str,
        sources: str,
        top_n: int,
    ) -> Dict[str, Any]:
        cmd = [
            self.python_bin,
            str(self.global_search_script),
            "--query",
            query,
            "--sources",
            sources,
        ]
        cmd.extend(self._global_search_cli_options({}))
        return await self._run_json_cmd(
            cmd,
            timeout=max(20, int(self.timeout_seconds)),
            cwd=self.skill_root,
            env_additions={"WEB_SEARCH_SKILL_ROOT": str(self.skill_root)},
        )

    async def _run_union_domain_search(
        self,
        query: str,
        domain: str,
        union_platforms: Optional[str] = None,
        union_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not self.union_search_enabled:
            return {"error": "union search disabled", "results": {}}
        domain_query = f"{self._focused_domain_query(query)} site:{domain}"
        return await self._run_union_search(
            query=domain_query,
            union_platforms=union_platforms or self.source_mix_domain_union_platforms,
            union_limit=union_limit or max(1, int(self.source_mix_target_limit)),
        )

    def _focused_domain_query(self, query: str) -> str:
        """Compact long research prompts for site-restricted search engines."""
        text = re.sub(r"\s+", " ", str(query or "")).strip()
        if not text:
            return ""

        years = re.findall(r"\b(?:19|20)\d{2}\b", text)
        terms = self._dedupe_keep_order(years[:3] + self._query_relevance_terms(text))

        if not terms:
            tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text)
            terms = self._dedupe_keep_order(tokens[:8])

        focused = " ".join(terms[:8]).strip()
        if focused:
            return focused
        return text[:120]

    def _source_mix_deficits(self, payload: Dict[str, Any]) -> Dict[str, int]:
        targets = {
            "bilibili": max(0, int(self.min_bilibili_results)),
            "youtube": max(0, int(self.min_youtube_results)),
            "wikipedia": max(0, int(self.min_wikipedia_results)),
            "rss": max(0, int(self.min_rss_mix_results)),
        }
        counts = {"bilibili": 0, "youtube": 0, "wikipedia": 0, "rss": 0}
        results = payload.get("results") if isinstance(payload, dict) else None
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                if not str(item.get("url", "")).strip():
                    continue
                bucket = self._source_bucket(item)
                if bucket in counts:
                    counts[bucket] += 1
        deficits: Dict[str, int] = {}
        for key, required in targets.items():
            gap = required - counts.get(key, 0)
            if gap > 0:
                deficits[key] = gap
        return deficits

    def _rebalance_for_source_mix(self, payload: Dict[str, Any], top_n: int) -> Dict[str, Any]:
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return payload

        by_bucket: Dict[str, List[Dict[str, Any]]] = {
            "bilibili": [],
            "youtube": [],
            "wikipedia": [],
            "rss": [],
            "other": [],
        }
        for item in results:
            if not isinstance(item, dict):
                continue
            bucket = self._source_bucket(item)
            if bucket in by_bucket:
                by_bucket[bucket].append(item)
            else:
                by_bucket["other"].append(item)

        for items in by_bucket.values():
            items.sort(key=lambda x: self._parse_score(x.get("score"), 0.0), reverse=True)

        targets = {
            "bilibili": max(0, int(self.min_bilibili_results)),
            "youtube": max(0, int(self.min_youtube_results)),
            "wikipedia": max(0, int(self.min_wikipedia_results)),
            "rss": max(0, int(self.min_rss_mix_results)),
        }

        picked: List[Dict[str, Any]] = []
        picked_ids: set[int] = set()

        for bucket in ("bilibili", "youtube", "wikipedia", "rss"):
            need = targets[bucket]
            if need <= 0:
                continue
            for item in by_bucket[bucket][:need]:
                item_id = id(item)
                if item_id in picked_ids:
                    continue
                picked_ids.add(item_id)
                picked.append(item)

        remaining = sorted(
            [it for it in results if isinstance(it, dict) and id(it) not in picked_ids],
            key=lambda x: self._parse_score(x.get("score"), 0.0),
            reverse=True,
        )
        picked.extend(remaining)

        out = dict(payload)
        if self.unlimited_results:
            out["results"] = picked
        else:
            out["results"] = picked[: max(1, int(top_n))]
        return out

    def _source_bucket(self, item: Dict[str, Any]) -> str:
        provider = str(item.get("provider", "")).strip().lower()
        url = str(item.get("url", "")).strip().lower()

        if provider == "rss":
            return "rss"
        if provider.startswith("bilibili") or "bilibili.com" in url or "b23.tv" in url:
            return "bilibili"
        if provider.startswith("youtube") or "youtube.com" in url or "youtu.be" in url:
            return "youtube"
        if provider.startswith("wikipedia") or provider == "wiki" or "wikipedia.org" in url:
            return "wikipedia"
        return "other"

    async def _run_news_aggregator_search(self, query: str) -> Dict[str, Any]:
        if not self.news_aggregator_root.exists():
            return {
                "error": f"news_aggregator_root does not exist: {self.news_aggregator_root}",
                "results": [],
            }
        if not self.news_aggregator_script.exists():
            return {
                "error": f"news aggregator script not found: {self.news_aggregator_script}",
                "results": [],
            }

        chosen_sources = self._select_news_aggregator_sources(query)
        cmd = [
            self.python_bin,
            str(self.news_aggregator_script),
            "--source",
            chosen_sources,
            "--limit",
            str(max(1, int(self.news_aggregator_limit))),
            "--no-save",
        ]
        return await self._run_json_cmd(
            cmd,
            timeout=max(20, int(self.news_aggregator_timeout_seconds)),
            cwd=self.news_aggregator_root,
            env_additions={"WEB_SEARCH_NEWS_AGGREGATOR_ROOT": str(self.news_aggregator_root)},
        )

    def _select_news_aggregator_sources(self, query: str) -> str:
        text = (query or "").lower()
        if any(
            kw in text
            for kw in ("ai", "llm", "模型", "大模型", "agent", "claude", "gpt", "gemini", "anthropic", "openai")
        ):
            return self.news_aggregator_sources_ai
        return self.news_aggregator_sources_general

    def _normalize_news_aggregator_result(self, data: Any, top_n: int) -> Dict[str, Any]:
        if not isinstance(data, list):
            return {
                "results": [],
                "providers": [],
                "provider_health": {},
                "raw": data,
            }

        mapped: List[Dict[str, Any]] = []
        provider_health: Dict[str, str] = {}
        seen: set[str] = set()

        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            source_name = str(item.get("source") or "news_aggregator").strip()
            provider = (
                source_name.lower()
                .replace(" ", "_")
                .replace("-", "_")
                .replace("/", "_")
            )
            if not provider:
                provider = "news_aggregator"

            if not url:
                continue

            dedupe_key = url or f"{provider}::{title}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            provider_health[provider] = "ok"
            summary_parts = [
                str(item.get("summary") or "").strip(),
                str(item.get("content") or "").strip(),
            ]
            heat = str(item.get("heat") or "").strip()
            ts = str(item.get("time") or "").strip()
            if heat:
                summary_parts.append(f"heat: {heat}")
            if ts:
                summary_parts.append(f"time: {ts}")
            summary = self._trim(" | ".join([p for p in summary_parts if p]))

            mapped.append(
                {
                    "title": title or f"{source_name} result",
                    "url": url,
                    "summary": summary,
                    "highlights": [self._trim(summary, 280)] if summary else [],
                    "score": 0.57,
                    "provider": provider,
                }
            )

        mapped.sort(key=lambda x: self._parse_score(x.get("score"), 0.0), reverse=True)
        max_results = None if self.unlimited_results else min(
            max(1, int(top_n)),
            max(1, int(self.news_aggregator_max_results)),
        )
        return {
            "results": mapped if max_results is None else mapped[:max_results],
            "providers": sorted(provider_health.keys()),
            "provider_health": provider_health,
            "raw": data,
        }

    def _needs_union_enrichment(self, normalized: Dict[str, Any], top_n: int, force: bool = False) -> bool:
        if force:
            return True
        if not self.union_search_enabled or self.union_trigger_mode == "off":
            return False
        if self.union_trigger_mode == "always":
            return True

        results = normalized.get("results") or []
        provider_health = normalized.get("provider_health") or {}
        healthy_providers = sum(1 for s in provider_health.values() if s == "ok")
        result_floor = min(max(1, int(top_n)), self.union_trigger_min_results)

        return len(results) < result_floor or healthy_providers < self.union_trigger_min_providers

    async def _run_union_search(
        self,
        query: str,
        union_platforms: Optional[str] = None,
        union_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not self.union_root.exists():
            return {"error": f"union_search_root does not exist: {self.union_root}"}
        if not self.union_cli_script.exists():
            return {"error": f"union cli script not found: {self.union_cli_script}"}

        platforms = union_platforms or str(self.union_platforms)
        if self.unlimited_results:
            per_platform_limit = int(max(1, self.unlimited_union_limit_per_platform))
        else:
            per_platform_limit = int(union_limit) if union_limit is not None else self.union_limit_per_platform

        cmd = [
            self.python_bin,
            str(self.union_cli_script),
            "search",
            query,
            "--platforms",
            *[p.strip() for p in str(platforms).split(",") if p.strip()],
            "--limit",
            str(per_platform_limit),
            "--max-workers",
            str(self.union_max_workers),
            "--timeout",
            str(self.union_timeout_seconds),
            "--deduplicate",
            "--format",
            "json",
        ]

        env_file = self._resolve_union_env_file()
        if env_file is not None:
            cmd.extend(["--env-file", str(env_file)])

        return await self._run_json_cmd(
            cmd,
            timeout=self.union_timeout_seconds + 5,
            cwd=self.union_root,
            env_additions={
                "WEB_SEARCH_UNION_ROOT": str(self.union_root),
                "TAVILY_SEARCH_DEPTH": self.tavily_search_depth,
                "TAVILY_INCLUDE_ANSWER": "true",
            },
        )

    async def _run_rss_search(self, query: str, top_n: int) -> Dict[str, Any]:
        if not self.rss_search_script.exists():
            return {"error": f"rss_search script not found: {self.rss_search_script}", "results": []}

        limit = max(1, min(self.rss_fallback_limit, int(top_n) if top_n > 0 else self.rss_fallback_limit))
        limit = min(limit, max(1, self.rss_fallback_runtime_limit))
        cleaned_feeds_path: Optional[Path] = None
        cmd = [
            self.python_bin,
            str(self.rss_search_script),
            "--json",
            "-l",
            str(limit),
            "--timeout",
            str(max(1, int(self.rss_feed_timeout_seconds))),
        ]
        if self.rss_feeds_file.exists():
            cleaned_feeds_path = self._build_clean_rss_feeds_file(self.rss_feeds_file, query=query)
            if cleaned_feeds_path is not None:
                cmd.extend(["--feeds", str(cleaned_feeds_path)])
            else:
                cmd.extend(["--feeds", str(self.rss_feeds_file)])

        env_file = self._resolve_union_env_file()
        if env_file is not None:
            cmd.extend(["--env-file", str(env_file)])
        cmd.append(query)

        try:
            first = await self._run_json_cmd(
                cmd,
                timeout=self.rss_timeout_seconds,
                cwd=self.union_root,
                env_additions={"WEB_SEARCH_UNION_ROOT": str(self.union_root)},
            )
            if self._is_timeout_payload(first) and cleaned_feeds_path is not None:
                retry_feeds_path = self._build_clean_rss_feeds_file(
                    self.rss_feeds_file,
                    max_feeds=max(1, self.rss_fallback_retry_feeds),
                    query=query,
                )
                if retry_feeds_path is not None:
                    retry_cmd = [
                        self.python_bin,
                        str(self.rss_search_script),
                        "--json",
                        "-l",
                        str(limit),
                        "--timeout",
                        str(max(1, int(self.rss_feed_timeout_seconds))),
                        "--feeds",
                        str(retry_feeds_path),
                    ]
                    if env_file is not None:
                        retry_cmd.extend(["--env-file", str(env_file)])
                    retry_cmd.append(query)
                    try:
                        return await self._run_json_cmd(
                            retry_cmd,
                            timeout=self.rss_timeout_seconds,
                            cwd=self.union_root,
                            env_additions={"WEB_SEARCH_UNION_ROOT": str(self.union_root)},
                        )
                    finally:
                        try:
                            retry_feeds_path.unlink(missing_ok=True)
                        except OSError:
                            pass
            return first
        finally:
            if cleaned_feeds_path is not None:
                try:
                    cleaned_feeds_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _is_timeout_payload(self, payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and "timeout" in str(payload.get("error", "")).lower()
        )

    def _build_clean_rss_feeds_file(
        self,
        feeds_file: Path,
        max_feeds: Optional[int] = None,
        query: str = "",
    ) -> Optional[Path]:
        try:
            raw_lines = feeds_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

        candidates: List[str] = []
        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if not re.match(r"^https?://", line, flags=re.IGNORECASE):
                continue
            candidates.append(line)

        if not candidates:
            return None

        query_l = str(query or "").lower()
        academic_query = any(
            term in query_l
            for term in (
                "portfolio", "diversification", "finance", "stock", "equity", "asset",
                "risk", "expected shortfall", "论文", "文献", "学术", "期刊",
                "投资组合", "股票", "金融", "财政", "税", "土地", "房产",
                "economic", "economics", "property tax", "housing",
            )
        )

        def score(url: str) -> int:
            s = 0
            low = url.lower()
            if low.startswith("https://"):
                s += 2
            if academic_query and any(
                d in low
                for d in (
                    "nber.org",
                    "arxiv.org",
                    "repec.org",
                    "onlinelibrary.wiley.com",
                    "federalreserve.gov",
                    "imf.org",
                    "worldbank.org",
                    "oecd.org",
                )
            ):
                s += 10
            if any(
                d in low
                for d in (
                    "bestblogs.dev",
                    "feeds.appinn.com",
                    "wechat2rss",
                    "raw.githubusercontent.com",
                    "justlovemaki.github.io",
                )
            ):
                s += 4
            if "feedmaker.kindle4rss.com" in low:
                s -= 5
            if "rsshub.app" in low:
                s -= 3
            return s

        candidates.sort(key=score, reverse=True)
        feeds = candidates[: max(1, int(max_feeds or self.rss_fallback_max_feeds))]
        if not feeds:
            return None

        tmp_fd, tmp_path = tempfile.mkstemp(prefix="rss_feeds_clean_", suffix=".txt")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(feeds))
            fh.write("\n")
        return Path(tmp_path)

    def _resolve_union_env_file(self) -> Optional[Path]:
        bundle_root = (
            self.skill_root.parent.parent
            if self.skill_root.parent.name == "skills"
            else self.skill_root.parent
        )
        candidates = [
            self.union_root / ".env",
            self.skill_root / ".env",
            self.skill_root.parent / "academic-research-skills" / ".env",
            self.skill_root.parent / "gs-skills" / ".env",
            bundle_root / "vendor" / "ROMA_v2" / ".env",
            bundle_root / ".env",
            Path(os.getenv("WEB_SEARCH_SKILL_ROOT", "")) / ".env" if os.getenv("WEB_SEARCH_SKILL_ROOT") else None,
        ]
        for c in candidates:
            if c is None:
                continue
            try:
                if c.exists() and c.is_file():
                    return c
            except OSError:
                continue
        return None

    async def _run_json_cmd(
        self,
        cmd: Sequence[str],
        timeout: int,
        cwd: Optional[Path] = None,
        env_additions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        run_env = os.environ.copy()
        run_env.update(self._search_env_overrides(cwd=cwd))
        if env_additions:
            run_env.update(env_additions)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd or self.skill_root),
                env=run_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": f"timeout after {timeout}s", "results": []}
        except Exception as e:
            return {"error": str(e), "results": []}

        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()

        if not out:
            return {"error": err or "empty output", "results": []}

        try:
            return json.loads(out)
        except json.JSONDecodeError:
            extracted = self._extract_json_from_text(out)
            if extracted is not None:
                return extracted
            return {
                "error": "invalid json output",
                "raw_stdout": out[:1000],
                "raw_stderr": err[:500],
                "results": [],
            }

    def _search_env_overrides(self, cwd: Optional[Path] = None) -> Dict[str, str]:
        """Load local skill env files so stale container env cannot shadow them."""
        env: Dict[str, str] = {}
        candidates: List[Path] = []

        if cwd is not None:
            candidates.append(Path(cwd) / ".env")
        candidates.extend(
            [
                self.skill_root / ".env",
                self.union_root / ".env",
            ]
        )

        seen: set[Path] = set()
        for path in candidates:
            try:
                resolved = path.expanduser().resolve()
            except Exception:
                resolved = path.expanduser()
            if resolved in seen or not resolved.exists():
                continue
            seen.add(resolved)
            env.update(self._read_env_file(resolved))

        return env

    @staticmethod
    def _read_env_file(path: Path) -> Dict[str, str]:
        values: Dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return values

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key] = value
        return values

    def _extract_json_from_text(self, text: str) -> Optional[Any]:
        decoder = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch not in "{[":
                continue
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
        return None

    def _source_health(self, data: Dict[str, Any]) -> Dict[str, str]:
        health: Dict[str, str] = {}
        if not isinstance(data, dict):
            return health

        for provider, payload in data.items():
            if provider == "error":
                continue
            if not isinstance(payload, dict):
                health[provider] = "invalid"
                continue
            if payload.get("error"):
                health[provider] = "error"
                continue
            has_text = bool(str(payload.get("answer") or payload.get("content") or "").strip())
            has_items = isinstance(payload.get("results"), list) and len(payload.get("results")) > 0
            health[provider] = "ok" if (has_text or has_items) else "empty"
        return health

    def _normalize_union_envelope(
        self,
        data: Dict[str, Any],
        top_n: int,
        query: str = "",
        query_profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"results": [], "error": "invalid union result"}

        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        platform_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(platform_results, dict):
            return {"results": [], "providers": [], "provider_health": {}, "raw": data}

        merged: Dict[str, Any] = {}
        provider_health: Dict[str, str] = {}

        for provider, result in platform_results.items():
            if not isinstance(result, dict):
                provider_health[provider] = "invalid"
                continue

            if not result.get("success"):
                provider_health[provider] = "error"
                merged[provider] = {"error": result.get("error", "platform failed")}
                continue

            items = result.get("items") if isinstance(result.get("items"), list) else []
            provider_health[provider] = "ok" if items else "empty"
            mapped_items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("name") or "").strip()
                url = str(
                    item.get("url")
                    or item.get("href")
                    or item.get("link")
                    or item.get("permalink")
                    or ""
                ).strip()
                summary = str(
                    item.get("summary")
                    or item.get("snippet")
                    or item.get("description")
                    or item.get("content")
                    or item.get("body")
                    or ""
                ).strip()
                mapped_items.append(
                    {
                        "title": title,
                        "url": url,
                        "summary": self._trim(summary),
                        "score": self._parse_score(item.get("score"), default=0.55),
                    }
                )
            merged[provider] = {"results": mapped_items}

        normalized = self._normalize_aggregated_result(
            merged,
            top_n=top_n,
            query=query,
            query_profile=query_profile,
        )
        normalized["provider_health"] = provider_health
        normalized["raw_union"] = data
        return normalized

    def _normalize_rss_result(self, data: Dict[str, Any], top_n: int) -> Dict[str, Any]:
        # rss_search.py returns a plain list for --json output.
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("results"), list):
            items = data.get("results", [])
        else:
            return {"results": [], "providers": ["rss"], "provider_health": {"rss": "empty"}, "raw": data}

        mapped_items: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("weixin_link") or item.get("link") or item.get("url") or "").strip()
            summary = str(item.get("summary") or item.get("content") or "").strip()
            mapped_items.append(
                {
                    "title": title or "rss result",
                    "url": url,
                    "summary": self._trim(summary),
                    "highlights": [self._trim(summary, 280)] if summary else [],
                    "score": 0.58,
                    "provider": "rss",
                }
            )

        mapped_items.sort(key=lambda x: self._parse_score(x.get("score"), 0.0), reverse=True)
        max_results = None if self.unlimited_results else max(1, int(top_n))
        return {
            "results": mapped_items if max_results is None else mapped_items[:max_results],
            "providers": ["rss"],
            "provider_health": {"rss": "ok" if mapped_items else "empty"},
            "raw": data,
        }

    def _merge_normalized_results(
        self, primary: Dict[str, Any], secondary: Dict[str, Any], limit: Optional[int]
    ) -> Dict[str, Any]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def collect(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                title = str(item.get("title", "")).strip()
                key = url or title
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)

        collect(primary.get("results"))
        collect(secondary.get("results"))

        # Keep provider diversity first, then backfill by score.
        by_provider: Dict[str, List[Dict[str, Any]]] = {}
        for item in merged:
            provider = str(item.get("provider") or "unknown")
            by_provider.setdefault(provider, []).append(item)
        for provider_items in by_provider.values():
            provider_items.sort(key=lambda x: self._parse_score(x.get("score"), 0.0), reverse=True)

        selected_seed: List[Dict[str, Any]] = []
        chosen_ids: set[int] = set()

        for provider in sorted(by_provider.keys()):
            best = by_provider[provider][0]
            selected_seed.append(best)
            chosen_ids.add(id(best))

        remaining = [it for it in merged if id(it) not in chosen_ids]
        remaining.sort(key=lambda x: self._parse_score(x.get("score"), 0.0), reverse=True)
        selected_seed.extend(remaining)

        per_provider_cap = (
            max(1, int(self.max_results_per_provider_unlimited))
            if self.unlimited_results
            else max(1, int(self.max_results_per_provider))
        )
        rss_cap = max(1, min(per_provider_cap, int(self.max_rss_results)))
        provider_counts: Dict[str, int] = {}
        selected: List[Dict[str, Any]] = []

        for item in selected_seed:
            provider = str(item.get("provider") or "unknown")
            cap = rss_cap if provider == "rss" else per_provider_cap
            if provider_counts.get(provider, 0) >= cap:
                continue
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            selected.append(item)

        out = dict(primary)
        if limit is None or int(limit) <= 0:
            out["results"] = selected
        else:
            out["results"] = selected[: max(1, int(limit))]
        out["providers"] = list(dict.fromkeys((primary.get("providers") or []) + (secondary.get("providers") or [])))
        out["raw"] = {
            "primary": primary.get("raw"),
            "secondary": secondary.get("raw") or secondary.get("raw_union"),
        }
        return out

    def _build_route_plan(self, query: str, sources: Optional[str], top_n: int) -> Dict[str, Any]:
        if sources:
            # Respect explicit caller override.
            return {
                "route_enabled": False,
                "selected_branches": [],
                "branch_scores": {},
                "primary_sources": sources,
                "union_platforms": str(self.union_platforms),
                "union_limit_per_platform": self.union_limit_per_platform,
                "force_union": False,
                "reason": "explicit sources override",
            }

        if not self.route_by_skill_tree:
            return {
                "route_enabled": False,
                "selected_branches": [],
                "branch_scores": {},
                "primary_sources": self.default_sources,
                "union_platforms": str(self.union_platforms),
                "union_limit_per_platform": self.union_limit_per_platform,
                "force_union": False,
                "reason": "route_by_skill_tree disabled",
            }

        text = query.lower()
        tokens = set(re.findall(r"[a-z0-9_+\-/\.]+|[\u4e00-\u9fff]+", text))
        is_video_intent = self._is_video_intent(text)
        is_text_news_intent = self._is_text_news_intent(text)
        query_profile = self._detect_query_profile(text)

        has_url = bool(re.search(r"https?://", text))

        branch_keywords = {
            "academic": [
                "academic", "scholar", "paper", "papers", "journal", "literature", "citation",
                "citations", "doi", "crossref", "openalex", "semantic scholar", "ssrn", "nber",
                "meta-analysis", "systematic review", "empirical", "methodology", "peer-reviewed",
                "学术", "论文", "文献", "期刊", "作者", "引用", "实证研究", "方法论",
            ],
            "policy": [
                "policy", "government", "governance", "regulation", "regulatory", "official",
                "ministry", "agency", "law", "act", "strategy", "master plan", "digital government",
                "e-government", "national informatization", "oecd", "world bank", "united nations",
                "政府", "政策", "法规", "法律", "法案", "战略", "规划", "总体规划", "官方",
                "部门", "数字政府", "电子政务", "信息化", "韩国", "日本",
                "财政部", "自然资源部", "统计局", "土地财政", "土地出让", "房产税",
                "房地产税", "地方财政", "保障性住房", "住房券", "先租后售",
            ],
            "platform-content": [
                "bilibili", "b站", "知乎", "zhihu", "微博", "weibo", "小红书", "xiaohongshu",
                "twitter", "reddit", "youtube", "ins", "instagram", "tiktok",
                "市场", "舆情", "人物", "watchlist", "finance", "stock", "股票", "行情",
            ],
            "multi-platform-search": [
                "多平台", "batch", "批量", "cross-platform", "github", "repo", "code",
                "stackoverflow", "duckduckgo", "bing", "brave", "wikipedia", "rss",
                "image", "图片", "下载", "联合搜索", "union", "social",
            ],
            "deep-search": [
                "深度", "调研", "报告", "分析", "交叉验证", "dual", "grok", "tavily",
                "最新", "实时", "latest", "real-time", "compare", "对比", "strategy", "财报",
            ],
            "news": [
                "新闻", "news", "快讯", "日报", "早报", "hacker news", "hn", "product hunt",
                "36kr", "华尔街见闻", "腾讯财经", "social news", "tech news", "ai news",
            ],
            "trend-research": [
                "近30天", "30天", "趋势", "trend", "热度", "舆情走势", "讨论变化", "topic research",
                "quick mode", "deep mode", "last30days",
            ],
            "intelligence": [
                "情报", "briefing", "alpha", "radar", "bounty", "变现", "revenue architect",
                "web3", "solana", "悬赏", "商业机会",
            ],
            "web-access": [
                "网页", "链接", "read this", "reader", "rss", "feed", "字幕", "transcript",
                "视频内容", "youtube 链接", "bilibili 链接", "github 链接", "tweet link",
            ],
        }

        branch_scores: Dict[str, int] = {}
        for branch, kws in branch_keywords.items():
            score = 0
            for kw in kws:
                if kw in text or kw in tokens:
                    score += 1
            branch_scores[branch] = score

        sorted_branches = sorted(
            branch_scores.items(),
            key=lambda kv: (kv[1], kv[0]),
            reverse=True,
        )

        selected: List[str] = []
        best_score = sorted_branches[0][1] if sorted_branches else 0
        if has_url and "web-access" not in selected:
            selected.append("web-access")

        if best_score <= 0:
            selected = ["platform-content", "multi-platform-search", "deep-search", "news"]
            if has_url:
                selected.append("web-access")
        else:
            for name, score in sorted_branches:
                if score <= 0:
                    continue
                if score == best_score or len(selected) < 3:
                    selected.append(name)
            selected = selected[:6]
            if has_url and "web-access" not in selected:
                selected.append("web-access")
        selected = list(dict.fromkeys(selected))

        if query_profile == "academic" and self.academic_route_enabled:
            selected = ["academic", "deep-search", "multi-platform-search"] + [
                b for b in selected if b == "web-access"
            ]
        elif query_profile == "policy" and self.policy_route_enabled:
            selected = ["policy", "deep-search", "multi-platform-search"] + [
                b for b in selected if b == "web-access"
            ]
        selected = list(dict.fromkeys(selected))

        source_map = {
            "academic": self._split_csv(self.academic_sources),
            "policy": self._split_csv(self.policy_sources),
            "platform-content": ["tavily", "bilibili", "zhihu", "youtube", "duckduckgo"],
            "multi-platform-search": ["tavily", "duckduckgo", "bilibili", "zhihu", "youtube"],
            "deep-search": ["grok", "tavily", "duckduckgo", "zhihu", "bilibili", "youtube"],
            "news": ["tavily", "duckduckgo", "zhihu", "bilibili", "youtube"],
            "trend-research": ["tavily", "duckduckgo", "youtube", "bilibili", "zhihu", "grok"],
            "intelligence": ["grok", "tavily", "duckduckgo", "youtube", "zhihu", "bilibili"],
            "web-access": ["tavily", "duckduckgo", "youtube", "bilibili", "zhihu"],
        }
        union_map = {
            "academic": self._split_csv(self.academic_union_platforms),
            "policy": self._split_csv(self.policy_union_platforms),
            "platform-content": ["zhihu", "bilibili", "twitter", "youtube", "xiaoyuzhoufm", "douyin", "reddit", "rss"],
            "multi-platform-search": ["github", "wikipedia", "reddit", "twitter", "duckduckgo", "brave", "yahoo", "rss"],
            "deep-search": ["metaso", "tavily", "wikipedia", "github", "twitter", "youtube", "reddit"],
            "news": ["metaso", "tavily", "twitter", "youtube", "wikipedia", "reddit", "zhihu", "bilibili", "rss"],
            "trend-research": ["twitter", "reddit", "youtube", "bilibili", "xiaoyuzhoufm", "zhihu", "metaso", "tavily", "rss"],
            "intelligence": ["twitter", "reddit", "github", "tavily", "metaso", "youtube", "zhihu", "rss"],
            "web-access": ["rss", "twitter", "youtube", "bilibili", "github", "wikipedia", "zhihu", "metaso", "tavily"],
        }

        primary_supported = {
            "grok", "tavily", "academic_research", "semantic_scholar", "crossref",
            "openalex", "scholar", "bilibili", "zhihu", "youtube", "duckduckgo",
        }
        union_supported = {
            "baidu", "bilibili", "bing", "brave", "douyin", "duckduckgo", "github", "google",
            "jina", "metaso", "reddit", "rss", "tavily", "twitter", "volcengine", "weibo",
            "wikipedia", "xiaohongshu", "xiaoyuzhoufm", "yahoo", "yandex", "youtube", "zhihu",
        }

        primary_sources: List[str] = []
        union_platforms: List[str] = []
        for branch in selected:
            src_candidates = source_map.get(branch, [])
            if query_profile in ("academic", "policy"):
                src_candidates = self._filter_profile_sources(src_candidates, query_profile)
            elif is_text_news_intent and not is_video_intent:
                src_candidates = self._deprioritize_video_sources(src_candidates)
            for s in src_candidates:
                if s in primary_supported and s not in primary_sources:
                    primary_sources.append(s)
            union_candidates = union_map.get(branch, [])
            if query_profile in ("academic", "policy"):
                union_candidates = self._filter_profile_sources(union_candidates, query_profile)
            elif is_text_news_intent and not is_video_intent:
                union_candidates = self._deprioritize_video_sources(union_candidates)
            for p in union_candidates:
                if p in union_supported and p not in union_platforms:
                    union_platforms.append(p)

        if query_profile == "academic" and self.academic_route_enabled:
            primary_sources = [
                s for s in self._filter_profile_sources(self._split_csv(self.academic_sources), "academic")
                if s in primary_supported
            ]
            union_platforms = [
                p for p in self._filter_profile_sources(self._split_csv(self.academic_union_platforms), "academic")
                if p in union_supported
            ]
        elif query_profile == "policy" and self.policy_route_enabled:
            primary_sources = [
                s for s in self._filter_profile_sources(self._split_csv(self.policy_sources), "policy")
                if s in primary_supported
            ]
            union_platforms = [
                p for p in self._filter_profile_sources(self._split_csv(self.policy_union_platforms), "policy")
                if p in union_supported
            ]

        if not primary_sources:
            primary_sources = [s.strip() for s in self.default_sources.split(",") if s.strip()]
        if not union_platforms:
            union_platforms = [p.strip() for p in str(self.union_platforms).split(",") if p.strip()]

        force_union = False if query_profile in ("academic", "policy") else any(
            b in selected
            for b in ("multi-platform-search", "deep-search", "news", "trend-research", "intelligence")
        )
        union_limit = self.union_limit_per_platform + (1 if "multi-platform-search" in selected else 0)
        if "news" in selected or "trend-research" in selected:
            union_limit += 1
        if query_profile in ("academic", "policy"):
            union_limit = max(union_limit, self.union_limit_per_platform + 2)

        disable_news = (
            query_profile == "academic" and self.academic_disable_news_aggregator
        ) or (
            query_profile == "policy" and self.policy_disable_news_aggregator
        )
        disable_source_mix = (
            query_profile == "academic" and self.academic_disable_source_mix
        ) or (
            query_profile == "policy" and self.policy_disable_source_mix
        )
        disable_rss = (
            query_profile == "academic" and self.academic_disable_rss_fallback
        ) or (
            query_profile == "policy" and self.policy_disable_rss_fallback
        )
        force_rss = (
            query_profile == "academic"
            and self.academic_include_rss_candidates
            and not disable_rss
        )
        profile_domain_union_platforms = (
            self.academic_domain_union_platforms
            if query_profile == "academic"
            else self.policy_domain_union_platforms
            if query_profile == "policy"
            else self.source_mix_domain_union_platforms
        )

        return {
            "route_enabled": True,
            "query_profile": query_profile,
            "selected_branches": selected,
            "branch_scores": branch_scores,
            "primary_sources": ",".join(primary_sources),
            "union_platforms": ",".join(union_platforms),
            "union_limit_per_platform": int(max(1, union_limit)),
            "force_union": bool(force_union),
            "disable_news_aggregator": bool(disable_news),
            "disable_source_mix_enforcement": bool(disable_source_mix),
            "disable_rss_fallback": bool(disable_rss),
            "force_rss_fallback": bool(force_rss),
            "profile_domain_union_platforms": profile_domain_union_platforms,
            "profile_domain_limit": int(max(1, self.profile_domain_limit)),
            "profile_domain_result_limit": int(max(1, self.profile_domain_result_limit)),
            "profile_domains": self._select_profile_domains(query=query, profile=query_profile),
            "tavily_depth": self.tavily_search_depth,
            "tavily_max_results": int(max(1, self.tavily_max_results)),
            "primary_max_results": int(max(1, self.primary_max_results)),
            "reason": "skill-tree query routing",
            "requested_top_n": int(top_n),
        }

    def _is_video_intent(self, text: str) -> bool:
        return any(
            kw in text
            for kw in (
                "视频", "b站", "bilibili", "youtube", "字幕", "transcript", "播客", "podcast"
            )
        )

    def _is_text_news_intent(self, text: str) -> bool:
        return any(
            kw in text
            for kw in (
                "新闻", "news", "快讯", "日报", "报道", "媒体", "来源",
                "政策", "融资", "发布", "解读", "汇总", "分析",
            )
        )

    def _detect_query_profile(self, text: str) -> str:
        strong_policy_keywords = (
            "土地财政", "土地出让", "国有土地使用权出让收入", "房产税", "房地产税",
            "地方财政", "保障性住房", "保障性租赁住房", "住房券", "先租后售",
            "财政部", "自然资源部", "统计局", "国家统计局", "住房和城乡建设部",
        )
        if any(kw in text for kw in strong_policy_keywords):
            return "policy"

        academic_keywords = (
            "academic", "scholar", "paper", "papers", "journal", "literature", "citation",
            "citations", "doi", "crossref", "openalex", "semantic scholar", "ssrn", "nber",
            "meta-analysis", "systematic review", "empirical", "methodology", "peer-reviewed",
            "portfolio diversification", "standard deviation", "expected shortfall",
            "学术", "论文", "文献", "期刊", "作者", "引用", "实证研究", "方法论", "研究综述",
        )
        policy_keywords = (
            "policy", "government", "governance", "regulation", "regulatory", "official",
            "ministry", "agency", "law", "act", "strategy", "master plan", "digital government",
            "e-government", "national informatization", "public sector", "oecd", "world bank",
            "united nations", "政府", "政策", "法规", "法律", "法案", "战略", "规划",
            "总体规划", "官方", "部门", "数字政府", "电子政务", "信息化", "韩国", "日本",
            "财政部", "自然资源部", "统计局", "土地财政", "土地出让", "房产税",
            "房地产税", "地方财政", "保障性住房", "住房券", "先租后售",
        )

        academic_score = sum(1 for kw in academic_keywords if kw in text)
        policy_score = sum(1 for kw in policy_keywords if kw in text)

        if academic_score <= 0 and policy_score <= 0:
            return "general"
        if academic_score >= policy_score + 1:
            return "academic"
        if policy_score >= academic_score + 1:
            return "policy"
        if any(kw in text for kw in ("doi", "journal", "paper", "论文", "文献", "引用")):
            return "academic"
        if any(kw in text for kw in ("government", "official", "政策", "政府", "官方", "规划")):
            return "policy"
        return "general"

    def _filter_profile_sources(self, providers: List[str], profile: str) -> List[str]:
        if profile == "academic":
            noisy = {
                "bilibili", "youtube", "zhihu", "twitter", "reddit", "douyin",
                "weibo", "xiaohongshu", "xiaoyuzhoufm", "github", "grok",
            }
            if not self.academic_include_rss_candidates:
                noisy.add("rss")
        elif profile == "policy":
            noisy = {"weibo", "xiaohongshu", "grok"}
            if not self.policy_include_social_candidates:
                noisy.update(
                    {
                        "bilibili", "youtube", "twitter", "reddit", "douyin",
                        "xiaoyuzhoufm", "github",
                    }
                )
        else:
            noisy = set()
        return [p for p in providers if p not in noisy]

    def _deprioritize_video_sources(self, providers: List[str]) -> List[str]:
        video = {"youtube", "bilibili"}
        text_first = [p for p in providers if p not in video]
        tail_video = [p for p in providers if p in video]
        return text_first + tail_video

    def _split_csv(self, value: Any) -> List[str]:
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        return [p.strip() for p in str(value or "").split(",") if p.strip()]

    def _select_profile_domains(self, query: str, profile: str) -> List[str]:
        text = (query or "").lower()
        if profile == "academic":
            prioritized: List[str] = []
            if any(kw in text for kw in ("finance", "business", "portfolio", "diversification", "投资", "组合", "分散")):
                prioritized.extend(["papers.ssrn.com", "nber.org", "doi.org"])
            prioritized.extend(["semanticscholar.org", "openalex.org", "crossref.org", "scholar.google.com"])
            prioritized.extend(self.academic_domains)
            return self._dedupe_keep_order(prioritized)[: max(1, int(self.profile_domain_limit))]

        if profile == "policy":
            prioritized = []
            country_groups: List[List[str]] = []
            if any(
                kw in text
                for kw in (
                    "china", "chinese", "中国", "全国", "地方", "土地财政", "土地出让",
                    "房产税", "房地产税", "住房", "财政部", "自然资源部", "统计局",
                )
            ):
                prioritized.extend(
                    [
                        "mof.gov.cn",
                        "stats.gov.cn",
                        "mnr.gov.cn",
                        "gov.cn",
                        "landchina.com",
                        "chinatax.gov.cn",
                        "shanghai.chinatax.gov.cn",
                        "cq.gov.cn",
                        "cass.cn",
                        "yicai.com",
                        "21jingji.com",
                        "xinhuanet.com",
                        "people.com.cn",
                        "chinanews.com",
                        "mohurd.gov.cn",
                        "npc.gov.cn",
                        "pbc.gov.cn",
                        "ndrc.gov.cn",
                        "caixin.com",
                        "thepaper.cn",
                    ]
                )
            if any(kw in text for kw in ("japan", "日本")):
                country_groups.append([
                    "digital.go.jp", "cio.go.jp", "e-gov.go.jp", "soumu.go.jp",
                    "japan.kantei.go.jp", "go.jp",
                ])
            if any(kw in text for kw in ("korea", "south korea", "韩国", "韓國")):
                country_groups.append([
                    "mois.go.kr", "korea.kr", "law.go.kr", "msit.go.kr", "moleg.go.kr", "go.kr",
                ])
            if any(kw in text for kw in ("singapore", "新加坡")):
                country_groups.append([
                    "smartnation.gov.sg", "tech.gov.sg", "gov.sg", "imda.gov.sg",
                    "nas.gov.sg", "nlb.gov.sg", "ida.gov.sg",
                ])
            if any(kw in text for kw in ("thailand", "泰国", "泰國")):
                country_groups.append(["dga.or.th", "mdes.go.th", "go.th"])
            if country_groups:
                per_group = max(2, int(self.profile_domain_limit) // max(1, len(country_groups)))
                for i in range(per_group):
                    for group in country_groups:
                        if i < len(group):
                            prioritized.append(group[i])
            prioritized.extend(["oecd.org", "worldbank.org", "un.org", "itu.int"])
            prioritized.extend(self.policy_domains)
            return self._dedupe_keep_order(prioritized)[: max(1, int(self.profile_domain_limit))]

        return []

    def _dedupe_keep_order(self, values: Sequence[str]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for value in values:
            item = str(value or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    def _normalize_aggregated_result(
        self,
        data: Dict[str, Any],
        top_n: int,
        query: str = "",
        query_profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"results": [], "error": "invalid aggregated result"}

        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        providers: List[str] = []

        for provider, payload in data.items():
            if provider == "error":
                continue
            providers.append(provider)
            if not isinstance(payload, dict):
                continue

            provider_items = payload.get("results")

            answer_text = payload.get("answer") or payload.get("content")
            # Only emit provider-level summary when the provider has no structured item list.
            if (
                self.include_provider_summaries
                and
                isinstance(answer_text, str)
                and answer_text.strip()
                and (not isinstance(provider_items, list) or len(provider_items) == 0)
            ):
                key = f"answer::{provider}"
                if key not in seen:
                    seen.add(key)
                    results.append(
                        {
                            "title": f"{provider} summary",
                            "url": "",
                            "summary": self._trim(answer_text),
                            "highlights": [self._trim(answer_text, 280)],
                            "score": 0.65,
                            "provider": provider,
                        }
                    )

            if not isinstance(provider_items, list):
                continue

            for item in provider_items:
                if not isinstance(item, dict):
                    continue

                url = str(item.get("url") or item.get("link") or "").strip()
                title = str(item.get("title") or item.get("name") or "").strip()
                snippet = (
                    item.get("summary")
                    or item.get("snippet")
                    or item.get("description")
                    or item.get("content")
                    or ""
                )
                summary = self._trim(str(snippet))

                dedupe_key = url or f"{provider}::{title}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                if not title:
                    title = f"{provider} result"

                score = self._parse_score(item.get("score"), default=0.6)
                if (
                    str(provider or "").strip().lower() == "tavily"
                    and score < self.tavily_min_score
                ):
                    continue

                if not self._should_keep_result(
                    provider=provider,
                    url=url,
                    title=title,
                    summary=summary,
                    query=query,
                    query_profile=query_profile,
                ):
                    continue

                results.append(
                    {
                        "title": title,
                        "url": url,
                        "summary": summary,
                        "highlights": [self._trim(summary, 280)] if summary else [],
                        "score": score,
                        "provider": provider,
                    }
                )

        results.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
        max_results = None if self.unlimited_results else max(1, int(top_n))
        return {
            "results": results if max_results is None else results[:max_results],
            "providers": providers,
            "raw": data,
        }

    def _should_keep_result(
        self,
        provider: str,
        url: str,
        title: str,
        summary: str,
        query: str,
        query_profile: Optional[str],
    ) -> bool:
        """Drop low-relevance academic/citation hits before they pollute reports."""
        provider_l = str(provider or "").strip().lower()
        domain = self._url_domain(str(url or ""))
        academic_like = (
            provider_l in {"academic_research", "semantic_scholar", "openalex", "crossref", "scholar"}
            or self._domain_matches(domain, self.academic_citation_domains)
        )
        if not academic_like:
            # General web/search providers already perform relevance ranking.
            # Keep them here so source collection is recall-first; downstream
            # rebalance decides what evidence is worth surfacing.
            return True

        terms = self._query_relevance_terms(query)
        if not terms:
            return True

        profile = str(query_profile or "").lower()
        haystack = f"{title} {summary} {url}".lower()
        hits = sum(1 for term in terms if term.lower() in haystack)
        threshold = 1 if len(terms) <= 2 else 2
        if profile == "policy":
            threshold = 1 if len(terms) <= 2 else 2
        return hits >= threshold

    def _query_relevance_terms(self, query: str) -> List[str]:
        text = str(query or "")
        if not text:
            return []

        known_terms = [
            "土地财政", "土地出让", "土地出让收入", "国有土地使用权出让收入",
            "房产税", "房地产税", "普通商品房", "利润税", "改善型住房",
            "刘尚希", "贾康", "保障性住房", "保障性租赁住房", "住房券",
            "先租后售", "地方财政", "地方一般公共预算收入", "一般公共预算收入",
            "财政部", "统计局", "国家统计局", "自然资源部", "社科院",
            "国务院发展研究中心", "房价收入比", "家庭财富", "基尼系数",
            "挤出效应", "地方政府债务", "土地增值", "招拍挂",
        ]
        generic = {
            "中国", "全国", "研究", "报告", "学术", "论文", "文献", "核心期刊",
            "分析", "数据", "变化", "趋势", "定义", "概念", "相关性", "机制",
            "政策", "税率", "2021", "2020", "2019", "2018", "2022", "2023",
            "latest", "news", "report", "analysis", "research", "paper", "journal",
            "government", "public", "sector", "national", "information", "infrastructure",
            "plan", "policy", "strategy", "official", "initiative", "vision",
        }

        terms: List[str] = []
        for term in known_terms:
            if term in text and term not in terms:
                terms.append(term)

        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text):
            token = token.strip()
            if not token or token.lower() in generic or token in generic:
                continue
            if len(token) > 14 and re.fullmatch(r"[\u4e00-\u9fff]+", token):
                continue
            if token not in terms:
                terms.append(token)

        return terms[:14]

    def _trim(self, text: str, limit: Optional[int] = None) -> str:
        if not text:
            return ""
        n = limit or self.max_chars_per_item
        return text if len(text) <= n else text[: n - 3] + "..."

    def _parse_score(self, value: Any, default: float) -> float:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            s = value.strip().lower()
            if not s:
                return default
            if s in ("high", "very_high"):
                return 0.85
            if s in ("medium", "mid"):
                return 0.65
            if s == "low":
                return 0.45
            try:
                return float(s)
            except ValueError:
                return default
        return default
