"""Sequential Probability Ratio Test (SPRT) implementation.

Provides SPRT evaluation for burn-in decisions and reverse-chronological
SPRT for stable test demotion. Pure Python implementation using math.log.
"""

from __future__ import annotations

import math


def sprt_evaluate(
    runs: int,
    passes: int,
    min_reliability: float,
    significance: float,
    margin: float = 0.10,
) -> str:
    """Evaluate SPRT for a test's reliability.

    Tests the null hypothesis H0 (reliability >= min_reliability) against
    the alternative H1 (reliability < min_reliability).

    Args:
        runs: Total number of test runs.
        passes: Number of passing runs.
        min_reliability: Minimum acceptable pass rate (e.g., 0.99).
        significance: Required confidence level (e.g., 0.95).
        margin: Difference between H0 and H1 reliability (default 0.10).

    Returns:
        "accept" if sufficient evidence test is reliable (-> stable),
        "reject" if sufficient evidence test is unreliable (-> flaky),
        "continue" if more evidence needed.
    """
    if runs <= 0:
        return "continue"

    # Compute boundaries from significance level
    alpha = 1.0 - significance  # Type I error rate
    beta = 1.0 - significance  # Type II error rate (symmetric)

    # Guard against log(0) when significance is exactly 1.0
    if alpha <= 0:
        alpha = 1e-10
    if beta <= 0:
        beta = 1e-10

    upper_boundary = math.log((1.0 - beta) / alpha)
    lower_boundary = math.log(beta / (1.0 - alpha))

    # Hypothesis probabilities
    p0 = min_reliability  # null hypothesis reliability
    p1 = max(min_reliability - margin, 1e-10)  # alternative

    # Clamp probabilities to avoid log(0)
    p0 = min(max(p0, 1e-10), 1.0 - 1e-10)
    p1 = min(max(p1, 1e-10), 1.0 - 1e-10)

    failures = runs - passes

    # Compute log-likelihood ratio
    log_ratio = passes * math.log(p0 / p1) + failures * math.log(
        (1.0 - p0) / (1.0 - p1)
    )

    if log_ratio >= upper_boundary:
        return "accept"
    elif log_ratio <= lower_boundary:
        return "reject"
    else:
        return "continue"


def demotion_evaluate(
    test_history: list[bool],
    min_reliability: float,
    significance: float,
    margin: float = 0.10,
) -> str:
    """Evaluate whether a stable test should be demoted using reverse-chronological SPRT.

    Processes the historical record from newest to oldest. SPRT determines
    the sample window by stopping when it reaches the configured confidence.
    The empirical reliability over that window is then checked against
    min_reliability.

    Args:
        test_history: List of pass/fail results ordered newest-first.
            True = passed, False = failed.
        min_reliability: Minimum acceptable pass rate.
        significance: Required confidence level.
        margin: Difference between H0 and H1 reliability (default 0.10).

    Returns:
        "demote" if reliability below threshold (-> flaky),
        "retain" if reliability meets threshold (-> stays stable),
        "inconclusive" if exhausted history without reaching confidence.
    """
    if not test_history:
        return "inconclusive"

    # Compute boundaries
    alpha = 1.0 - significance
    beta = 1.0 - significance

    if alpha <= 0:
        alpha = 1e-10
    if beta <= 0:
        beta = 1e-10

    upper_boundary = math.log((1.0 - beta) / alpha)
    lower_boundary = math.log(beta / (1.0 - alpha))

    p0 = min(max(min_reliability, 1e-10), 1.0 - 1e-10)
    p1 = max(min_reliability - margin, 1e-10)
    p1 = min(p1, 1.0 - 1e-10)

    passes = 0
    runs = 0

    for result in test_history:  # newest first
        runs += 1
        if result:
            passes += 1
        failures = runs - passes

        log_ratio = passes * math.log(p0 / p1) + failures * math.log(
            (1.0 - p0) / (1.0 - p1)
        )

        if log_ratio >= upper_boundary or log_ratio <= lower_boundary:
            # SPRT reached confidence - check empirical reliability
            observed_reliability = passes / runs
            if observed_reliability < min_reliability:
                return "demote"
            else:
                return "retain"

    return "inconclusive"
