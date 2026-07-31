from __future__ import annotations

import json
from dataclasses import dataclass, replace

from news_agent.cluster import (
    DuplicateGatePair,
    choose_merged_headline,
    duplicate_gate_candidates,
)
from news_agent.models import AgentConfig, CategoryAssignment, StoryCluster
from news_agent.openai_budget import OpenAIBudget
from news_agent.openai_client import request_structured_response


DUPLICATE_GATE_SYSTEM_PROMPT = (
    "You are given groups of candidate news stories. Within each group, decide "
    "which stories describe the same underlying event and should become one "
    "combined paragraph for the reader.\n\n"
    "Put stories together when they report the same event from different "
    "outlets, even when their angles, headlines, or emphasis differ. Put them "
    "together when one is a retrospective, timeline, explainer, or background "
    "piece about the same event another reports; differing depth or format is "
    "not a different event.\n\n"
    "Keep stories apart when they are separate developments involving the same "
    "company, person, or topic, including a follow-up that adds a materially new "
    "event, and including a market move reported alongside the news that caused "
    "it.\n\n"
    "Return one entry per set of stories that belong together, listing every "
    "cluster_id in that set. A set must contain at least two cluster_ids. Omit "
    "any story that belongs with nothing else. Never place one cluster_id in two "
    "sets, and never list a cluster_id that was not in the same candidate group."
)

DUPLICATE_GATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "same_event_sets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "cluster_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["cluster_ids"],
            },
        }
    },
    "required": ["same_event_sets"],
}


@dataclass(frozen=True)
class DuplicateGateStats:
    deck_size: int = 0
    eligible_pairs: int = 0
    candidate_components: int = 0
    clusters_offered: int = 0
    components_dropped: int = 0
    sets_returned: int = 0
    sets_rejected: int = 0
    sets_merged: int = 0
    clusters_removed: int = 0
    cross_category_merges: int = 0
    request_made: bool = False


def apply_duplicate_gate(
    category_clusters: dict[str, list[StoryCluster]],
    config: AgentConfig,
    *,
    assignments: dict[str, CategoryAssignment],
    budget: OpenAIBudget,
) -> tuple[dict[str, list[StoryCluster]], list[StoryCluster], DuplicateGateStats]:
    deck = _flatten_deck(category_clusters)
    pairs = duplicate_gate_candidates(deck, config.duplicate_gate)
    components = _connected_components(pairs)
    offered, dropped = _select_components(
        components,
        pairs,
        config.duplicate_gate.max_clusters_per_request,
    )
    stats = DuplicateGateStats(
        deck_size=len(deck),
        eligible_pairs=len(pairs),
        candidate_components=len(components),
        clusters_offered=sum(len(component) for component in offered),
        components_dropped=dropped,
    )
    if not offered:
        return category_clusters, [], stats

    cluster_ids = {id(cluster): f"c{index}" for index, cluster in enumerate(deck)}
    id_to_cluster = {cluster_id: cluster for cluster, cluster_id in (
        (cluster, cluster_ids[id(cluster)]) for cluster in deck
    )}
    id_to_component: dict[str, int] = {}
    for component_index, component in enumerate(offered):
        for cluster in component:
            id_to_component[cluster_ids[id(cluster)]] = component_index

    payload = _build_payload(offered, cluster_ids, config)
    outcome = request_structured_response(
        stage="duplicate_gate",
        budget_stage="duplicate_gate",
        default_model=config.openai_costs.model,
        system_prompt=DUPLICATE_GATE_SYSTEM_PROMPT,
        user_payload=payload,
        schema_name="duplicate_gate",
        schema=DUPLICATE_GATE_SCHEMA,
        max_output_tokens=config.duplicate_gate.max_output_tokens_per_request,
        budget=budget,
        reasoning_effort=config.duplicate_gate.reasoning_effort,
    )
    stats = replace(stats, request_made=True)
    if outcome.response is None:
        return category_clusters, [], stats

    try:
        data = json.loads(outcome.response.output_text)
        returned_sets = data["same_event_sets"]
        if not isinstance(returned_sets, list):
            raise TypeError("same_event_sets must be a list")
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError):
        budget.record_failure("duplicate_gate", "duplicate_gate_malformed_response")
        return category_clusters, [], stats

    stats = replace(stats, sets_returned=len(returned_sets))
    claimed: set[str] = set()
    accepted: list[list[StoryCluster]] = []
    rejected = 0
    for item in returned_sets:
        ids = item.get("cluster_ids") if isinstance(item, dict) else None
        if (
            not isinstance(ids, list)
            or len(ids) < 2
            or len(ids) > config.duplicate_gate.max_component_size
            or not all(isinstance(cluster_id, str) for cluster_id in ids)
            or len(set(ids)) != len(ids)
            or any(cluster_id not in id_to_component for cluster_id in ids)
            or len({id_to_component[cluster_id] for cluster_id in ids}) != 1
            or any(cluster_id in claimed for cluster_id in ids)
        ):
            rejected += 1
            continue
        claimed.update(ids)
        accepted.append([id_to_cluster[cluster_id] for cluster_id in ids])

    removed: list[StoryCluster] = []
    cross_category_merges = 0
    for members in accepted:
        destination = max(
            members,
            key=lambda cluster: (
                cluster.importance,
                cluster.total_score,
                cluster.latest_published_at,
                cluster.key,
            ),
        )
        absorbed = [cluster for cluster in members if cluster is not destination]
        if len({cluster.category for cluster in members}) > 1:
            cross_category_merges += 1
        _merge_into_destination(destination, absorbed, assignments)
        removed.extend(absorbed)

    removed_ids = {id(cluster) for cluster in removed}
    merged_categories = {
        category: [
            cluster for cluster in clusters if id(cluster) not in removed_ids
        ]
        for category, clusters in category_clusters.items()
    }
    stats = replace(
        stats,
        sets_rejected=rejected,
        sets_merged=len(accepted),
        clusters_removed=len(removed),
        cross_category_merges=cross_category_merges,
    )
    return merged_categories, removed, stats


def _flatten_deck(category_clusters: dict[str, list[StoryCluster]]) -> list[StoryCluster]:
    deck: list[StoryCluster] = []
    seen: set[int] = set()
    for clusters in category_clusters.values():
        for cluster in clusters:
            if id(cluster) not in seen:
                deck.append(cluster)
                seen.add(id(cluster))
    return deck


def _connected_components(pairs: list[DuplicateGatePair]) -> list[list[StoryCluster]]:
    by_id: dict[int, StoryCluster] = {}
    adjacency: dict[int, set[int]] = {}
    for pair in pairs:
        left_id = id(pair.left)
        right_id = id(pair.right)
        by_id[left_id] = pair.left
        by_id[right_id] = pair.right
        adjacency.setdefault(left_id, set()).add(right_id)
        adjacency.setdefault(right_id, set()).add(left_id)

    components: list[list[StoryCluster]] = []
    unseen = set(adjacency)
    while unseen:
        root = min(unseen, key=lambda item: by_id[item].key)
        pending = [root]
        component_ids: set[int] = set()
        while pending:
            current = pending.pop()
            if current in component_ids:
                continue
            component_ids.add(current)
            pending.extend(adjacency[current] - component_ids)
        unseen -= component_ids
        components.append(
            sorted((by_id[item] for item in component_ids), key=lambda cluster: cluster.key)
        )
    return components


def _select_components(
    components: list[list[StoryCluster]],
    pairs: list[DuplicateGatePair],
    cluster_limit: int,
) -> tuple[list[list[StoryCluster]], int]:
    pair_score = {
        frozenset((id(pair.left), id(pair.right))): pair.title_jaccard
        for pair in pairs
    }

    def component_score(component: list[StoryCluster]) -> tuple[int, float, str]:
        ids = {id(cluster) for cluster in component}
        best_score = max(
            (
                score
                for pair_ids, score in pair_score.items()
                if pair_ids <= ids
            ),
            default=0.0,
        )
        return (-len(component), -best_score, component[0].key)

    offered: list[list[StoryCluster]] = []
    used = 0
    dropped = 0
    for component in sorted(components, key=component_score):
        if used + len(component) > cluster_limit:
            dropped += 1
            continue
        offered.append(component)
        used += len(component)
    return offered, dropped


def _build_payload(
    components: list[list[StoryCluster]],
    cluster_ids: dict[int, str],
    config: AgentConfig,
) -> str:
    groups: list[dict[str, object]] = []
    for group_index, component in enumerate(components):
        clusters: list[dict[str, str]] = []
        for cluster in component:
            representative = max(
                cluster.articles,
                key=lambda article: (article.evidence_score, article.reputation),
            )
            clusters.append(
                {
                    "id": cluster_ids[id(cluster)],
                    "title": cluster.title,
                    "published_at": cluster.latest_published_at.isoformat(),
                    "source": representative.source,
                    "summary": representative.best_available_text[
                        : config.duplicate_gate.summary_truncate_chars
                    ],
                }
            )
        groups.append({"group_id": f"g{group_index}", "clusters": clusters})
    return json.dumps({"groups": groups}, ensure_ascii=False)


def _merge_into_destination(
    destination: StoryCluster,
    absorbed: list[StoryCluster],
    assignments: dict[str, CategoryAssignment],
) -> None:
    members = [destination, *absorbed]
    seen_urls: set[str] = set()
    merged_articles = []
    for cluster in members:
        for article in cluster.articles:
            canonical_url = (article.canonical_url or article.url).split("?", 1)[0]
            if canonical_url not in seen_urls:
                merged_articles.append(article)
                seen_urls.add(canonical_url)
    destination.articles = merged_articles
    destination.importance = max(cluster.importance for cluster in members)
    destination.merged_from = tuple(
        dict.fromkeys(
            (
                *destination.merged_from,
                *(cluster.key for cluster in absorbed),
                *(
                    key
                    for cluster in absorbed
                    for key in cluster.merged_from
                ),
            )
        )
    )
    destination.title = choose_merged_headline(destination.articles) or destination.title

    member_assignments = [
        assignments[cluster.key] for cluster in members if cluster.key in assignments
    ]
    if not member_assignments:
        return
    destination_assignment = assignments.get(destination.key, member_assignments[0])
    outlier_urls = tuple(
        dict.fromkeys(
            url
            for assignment in member_assignments
            for url in assignment.outlier_urls
        )
    )
    assignments[destination.key] = replace(
        destination_assignment,
        category=destination.category or destination_assignment.category,
        outlier_urls=outlier_urls,
    )
