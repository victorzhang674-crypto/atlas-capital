"""Synthetic regime-switching data for the Stage 21 HMM tests (seeded)."""

import numpy as np
import pandas as pd

# Per state: (mean daily return, return std, mean vol feature, vol std, mean credit change, std)
STATES_2 = ((0.0006, 0.007, 0.11, 0.015, 0.000, 0.002), (-0.0015, 0.022, 0.32, 0.05, 0.003, 0.006))
STATES_3 = (
    (0.0006, 0.006, 0.10, 0.012, 0.000, 0.002),
    (0.0002, 0.012, 0.19, 0.020, 0.001, 0.003),
    (-0.0020, 0.025, 0.38, 0.050, 0.004, 0.007),
)


def markov_states(n: int, n_states: int, stay: float, rng: np.random.Generator) -> np.ndarray:
    """A Markov chain of `n` states that stays with probability `stay`, else jumps uniformly."""
    out = np.empty(n, dtype=int)
    out[0] = 0
    for t in range(1, n):
        if rng.random() < stay:
            out[t] = out[t - 1]
        else:
            out[t] = rng.choice([k for k in range(n_states) if k != out[t - 1]])
    return out


def regime_features(
    n: int = 1500, n_states: int = 2, seed: int = 7, stay: float = 0.985
) -> tuple[pd.DataFrame, np.ndarray]:
    """Features `ret`, `vol`, `credit`, `slope` by session and the true states (0 = calmest)."""
    rng = np.random.default_rng(seed)
    params = STATES_2 if n_states == 2 else STATES_3
    states = markov_states(n, n_states, stay, rng)
    p = np.array(params)[states]
    frame = pd.DataFrame(
        {
            "ret": rng.normal(p[:, 0], p[:, 1]),
            "vol": rng.normal(p[:, 2], p[:, 3]),
            "credit": rng.normal(p[:, 4], p[:, 5]),
            "slope": 1.0 + 0.3 * rng.standard_normal(n),
        },
        index=pd.bdate_range("2012-01-02", periods=n, name="date"),
    )
    return frame, states
