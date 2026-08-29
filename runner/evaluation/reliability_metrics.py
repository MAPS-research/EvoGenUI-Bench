from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from runtime.types import EvaluationResult, JsonDict

PAPER_TURN_COUNT = 5


@dataclass(frozen=True, slots=True)
class _EpisodeReliabilityStats:
    turn_numbers: tuple[int, ...]
    passes: tuple[bool, ...]
    countable: tuple[bool, ...]
    cpt: int
    apr_num: int
    apr_den: int


def generation_response_received(result: EvaluationResult) -> bool | None:
    """Return the paper's APR eligibility flag when it is knowable from a result.

    A successfully normalized generation necessarily came from a model response.
    A generation failure represented by ``provider_response`` also came from a
    response, even though that response could not be parsed or validated.  A
    ``provider_request`` failure happened before a model response was obtained.
    Other generation failures need bundle-level evidence and are intentionally
    reported as unknown instead of being guessed from evaluator completion.
    """

    if result.generation_pass:
        return True
    details = result.details if isinstance(result.details, dict) else {}
    explicit = details.get("generation_response_received")
    if isinstance(explicit, bool):
        return explicit
    if "provider_response" in details:
        return True
    if "provider_request" in details:
        return False
    return None


def paper_reliability_metrics(conversations: Sequence[JsonDict]) -> JsonDict:
    """Compute TP, TP@5, CPT, and APR exactly as defined in the paper.

    Each conversation must contain an ordered ``turns_breakdown`` list with one
    entry for every requested slot.  Every entry needs a boolean
    ``official_pass``.  For turns after the first, a boolean
    ``generation_response_received`` is required whenever the preceding turn
    passed.  That field is the APR eligibility indicator; evaluator completion
    and ``fully_evaluated`` are deliberately ignored.

    Rates are returned as fractions in ``[0, 1]``.  APR is ``None`` when there
    are no eligible adjacent transitions.
    """

    requested_turns = 0
    passed_turns = 0
    five_turn_pass_count = 0
    cpt_total = 0
    apr_num = 0
    apr_den = 0

    for conversation_index, conversation in enumerate(conversations):
        if not isinstance(conversation, dict):
            raise ValueError(f"conversation {conversation_index} must be an object")
        breakdown = conversation.get("turns_breakdown")
        if not isinstance(breakdown, list):
            raise ValueError(
                f"conversation {conversation_index} must contain a turns_breakdown list"
            )
        if len(breakdown) != PAPER_TURN_COUNT:
            raise ValueError(
                f"conversation {conversation_index} has {len(breakdown)} requested turns; "
                f"TP@5 requires exactly {PAPER_TURN_COUNT}"
            )

        pass_sequence: list[bool] = []
        turn_numbers: list[int] = []
        for slot_index, slot in enumerate(breakdown):
            if not isinstance(slot, dict):
                raise ValueError(
                    f"conversation {conversation_index} turn slot {slot_index} must be an object"
                )
            official_pass = slot.get("official_pass")
            if not isinstance(official_pass, bool):
                raise ValueError(
                    f"conversation {conversation_index} turn slot {slot_index} "
                    "must contain boolean official_pass"
                )
            turn = slot.get("turn")
            if not isinstance(turn, int) or isinstance(turn, bool):
                raise ValueError(
                    f"conversation {conversation_index} turn slot {slot_index} "
                    "must contain an integer turn"
                )
            pass_sequence.append(official_pass)
            turn_numbers.append(turn)

        if any(
            current <= previous
            for previous, current in zip(turn_numbers, turn_numbers[1:], strict=False)
        ):
            raise ValueError(f"conversation {conversation_index} turns must be strictly increasing")

        requested_turns += len(pass_sequence)
        passed_turns += sum(pass_sequence)
        five_turn_pass_count += int(all(pass_sequence))

        prefix_passes = 0
        for official_pass in pass_sequence:
            if not official_pass:
                break
            prefix_passes += 1
        cpt_total += prefix_passes

        for slot_index in range(1, len(breakdown)):
            if not pass_sequence[slot_index - 1]:
                continue
            response_received = breakdown[slot_index].get("generation_response_received")
            if not isinstance(response_received, bool):
                raise ValueError(
                    f"conversation {conversation_index} turn {turn_numbers[slot_index]} needs "
                    "boolean generation_response_received for APR"
                )
            if not response_received:
                continue
            apr_den += 1
            apr_num += int(pass_sequence[slot_index])

    episode_count = len(conversations)
    return {
        "requested_turns": requested_turns,
        "passed_turns": passed_turns,
        "turn_pass_rate": (passed_turns / requested_turns if requested_turns else None),
        "five_turn_episode_count": episode_count,
        "five_turn_pass_count": five_turn_pass_count,
        "tp_at_5": five_turn_pass_count / episode_count if episode_count else None,
        "cpt": cpt_total / episode_count if episode_count else None,
        "apr_num": apr_num,
        "apr_den": apr_den,
        "apr": apr_num / apr_den if apr_den else None,
    }


def paper_reliability_diagnostics(
    conversations: Sequence[JsonDict],
    *,
    bootstrap_samples: int = 1000,
    seed: int = 7,
) -> JsonDict:
    """Compute paper-aligned supplementary reliability diagnostics.

    The bootstrap resamples whole five-turn conversations, so the sampling unit
    remains the task/episode rather than an individual turn.  The main paper
    metrics are returned unchanged under ``paper_metrics``.  The supplementary
    countable-only TP excludes slots whose generation call received no model
    response; it does not replace requested-slot TP.

    ``generation_response_received`` must be a boolean for every slot here.
    Countable-only TP excludes only provider generation failures with no model
    response; dependency-blocked requested slots remain countable failures.  A
    passing slot cannot be marked as having received no response.  Bootstrap
    intervals use the linear 2.5th and 97.5th percentiles.  APR's interval is
    ``None`` when no bootstrap sample has an eligible adjacent-transition
    denominator.
    """

    if not isinstance(bootstrap_samples, int) or isinstance(bootstrap_samples, bool):
        raise ValueError("bootstrap_samples must be a positive integer")
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")

    paper_metrics = paper_reliability_metrics(conversations)
    episodes = _episode_reliability_stats(conversations)

    turn_position_stats: list[JsonDict] = []
    if episodes:
        for slot_index, turn in enumerate(episodes[0].turn_numbers):
            passed = sum(int(episode.passes[slot_index]) for episode in episodes)
            requested = len(episodes)
            turn_position_stats.append(
                {
                    "position": slot_index + 1,
                    "turn": turn,
                    "requested_turns": requested,
                    "passed_turns": passed,
                    "turn_pass_rate": passed / requested,
                }
            )

    position_rates = [float(position["turn_pass_rate"]) for position in turn_position_stats]
    independence_tp_at_5 = math.prod(position_rates) if position_rates else None
    observed_tp_at_5 = paper_metrics["tp_at_5"]
    observed_to_independence_ratio = (
        observed_tp_at_5 / independence_tp_at_5
        if isinstance(observed_tp_at_5, (int, float))
        and independence_tp_at_5 is not None
        and independence_tp_at_5 > 0
        else None
    )

    countable_requested_turns = sum(sum(episode.countable) for episode in episodes)
    countable_passed_turns = sum(
        sum(
            passed and countable
            for passed, countable in zip(episode.passes, episode.countable, strict=True)
        )
        for episode in episodes
    )
    total_requested_turns = len(episodes) * PAPER_TURN_COUNT

    bootstrap_ci = _bootstrap_episode_confidence_intervals(
        episodes,
        samples=bootstrap_samples,
        seed=seed,
    )

    return {
        "paper_metrics": paper_metrics,
        "turn_position_stats": turn_position_stats,
        "independence_tp_at_5": independence_tp_at_5,
        "observed_tp_at_5": observed_tp_at_5,
        "observed_to_independence_ratio": observed_to_independence_ratio,
        "countable_turns": {
            "requested_turns": countable_requested_turns,
            "passed_turns": countable_passed_turns,
            "excluded_provider_no_response_turns": (
                total_requested_turns - countable_requested_turns
            ),
            "turn_pass_rate": (
                countable_passed_turns / countable_requested_turns
                if countable_requested_turns
                else None
            ),
        },
        "bootstrap": {
            "unit": "task_episode",
            "samples": bootstrap_samples,
            "seed": seed,
            "confidence_level": 0.95,
            "ci": bootstrap_ci,
        },
    }


def _episode_reliability_stats(
    conversations: Sequence[JsonDict],
) -> list[_EpisodeReliabilityStats]:
    episodes: list[_EpisodeReliabilityStats] = []
    expected_turn_numbers: tuple[int, ...] | None = None

    for conversation_index, conversation in enumerate(conversations):
        breakdown = conversation["turns_breakdown"]
        turn_numbers = tuple(int(slot["turn"]) for slot in breakdown)
        if expected_turn_numbers is None:
            expected_turn_numbers = turn_numbers
        elif turn_numbers != expected_turn_numbers:
            raise ValueError(
                f"conversation {conversation_index} turn positions {turn_numbers} do not match "
                f"{expected_turn_numbers}"
            )

        passes = tuple(bool(slot["official_pass"]) for slot in breakdown)
        countable_values: list[bool] = []
        response_received_values: list[bool] = []
        for slot_index, (slot, passed) in enumerate(zip(breakdown, passes, strict=True)):
            response_received = slot.get("generation_response_received")
            if not isinstance(response_received, bool):
                raise ValueError(
                    f"conversation {conversation_index} turn slot {slot_index} must contain "
                    "boolean generation_response_received for countable TP"
                )
            if passed and not response_received:
                raise ValueError(
                    f"conversation {conversation_index} turn {turn_numbers[slot_index]} cannot "
                    "pass without a generation response"
                )
            provider_no_response = (
                not response_received
                and slot.get("failure_reason") == "generation"
                and slot.get("failure_bucket") in {"infra", "timeout"}
            )
            countable_values.append(not provider_no_response)
            response_received_values.append(response_received)
        countable = tuple(countable_values)

        cpt = 0
        for passed in passes:
            if not passed:
                break
            cpt += 1

        apr_num = 0
        apr_den = 0
        for slot_index in range(1, PAPER_TURN_COUNT):
            if not passes[slot_index - 1] or not response_received_values[slot_index]:
                continue
            apr_den += 1
            apr_num += int(passes[slot_index])

        episodes.append(
            _EpisodeReliabilityStats(
                turn_numbers=turn_numbers,
                passes=passes,
                countable=countable,
                cpt=cpt,
                apr_num=apr_num,
                apr_den=apr_den,
            )
        )

    return episodes


def _bootstrap_episode_confidence_intervals(
    episodes: Sequence[_EpisodeReliabilityStats],
    *,
    samples: int,
    seed: int,
) -> JsonDict:
    if not episodes:
        return {
            "tp": None,
            "tp_at_5": None,
            "cpt": None,
            "apr": None,
        }

    rng = random.Random(seed)
    episode_count = len(episodes)
    tp_values: list[float] = []
    tp_at_5_values: list[float] = []
    cpt_values: list[float] = []
    apr_values: list[float] = []

    for _ in range(samples):
        sampled = [episodes[rng.randrange(episode_count)] for _ in range(episode_count)]
        tp_values.append(
            sum(sum(episode.passes) for episode in sampled) / (episode_count * PAPER_TURN_COUNT)
        )
        tp_at_5_values.append(sum(int(all(episode.passes)) for episode in sampled) / episode_count)
        cpt_values.append(sum(episode.cpt for episode in sampled) / episode_count)
        apr_num = sum(episode.apr_num for episode in sampled)
        apr_den = sum(episode.apr_den for episode in sampled)
        if apr_den:
            apr_values.append(apr_num / apr_den)

    return {
        "tp": _percentile_95_interval(tp_values),
        "tp_at_5": _percentile_95_interval(tp_at_5_values),
        "cpt": _percentile_95_interval(cpt_values),
        "apr": _percentile_95_interval(apr_values),
    }


def _percentile_95_interval(values: Sequence[float]) -> JsonDict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "lower": _linear_percentile(ordered, 0.025),
        "upper": _linear_percentile(ordered, 0.975),
    }


def _linear_percentile(ordered_values: Sequence[float], quantile: float) -> float:
    index = (len(ordered_values) - 1) * quantile
    lower_index = math.floor(index)
    upper_index = math.ceil(index)
    if lower_index == upper_index:
        return ordered_values[lower_index]
    weight = index - lower_index
    return ordered_values[lower_index] * (1 - weight) + ordered_values[upper_index] * weight
