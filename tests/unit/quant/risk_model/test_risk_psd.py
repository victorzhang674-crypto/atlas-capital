"""PSD repair and condition number (Stage 18, brief task 3)."""

import numpy as np
import pandas as pd
import pytest

from atlas.quant.risk_model.psd import (
    condition_number,
    floor_condition,
    is_psd,
    nearest_correlation,
    nearest_psd,
)

# Three "correlations" that cannot hold together: x = (1, -1, 1) gives x'Ax = 3 - 5.4 < 0.
BROKEN = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])

# Higham (2002), "Computing the nearest correlation matrix", the 4x4 example A = tridiag(-1, 2, -1)
# and its nearest correlation matrix as published.
HIGHAM_A = np.array(
    [[2.0, -1.0, 0.0, 0.0], [-1.0, 2.0, -1.0, 0.0], [0.0, -1.0, 2.0, -1.0], [0.0, 0.0, -1.0, 2.0]]
)
HIGHAM_X = np.array(
    [
        [1.0, -0.8084125, 0.1915875, 0.10677505],
        [-0.8084125, 1.0, -0.65623269, 0.1915875],
        [0.1915875, -0.65623269, 1.0, -0.8084125],
        [0.10677505, 0.1915875, -0.8084125, 1.0],
    ]
)


def _min_eig(m: np.ndarray) -> float:
    return float(np.linalg.eigvalsh(m).min())


def test_is_psd_and_condition_number_known_values() -> None:
    assert is_psd(np.diag([1.0, 2.0, 4.0]))
    assert condition_number(np.diag([1.0, 2.0, 4.0])) == pytest.approx(4.0)
    singular = np.array([[1.0, 1.0], [1.0, 1.0]])
    assert is_psd(singular)  # eigenvalues 0 and 2: PSD, not definite
    assert condition_number(singular) == float("inf")
    assert not is_psd(BROKEN)
    assert condition_number(BROKEN) == float("inf")
    assert is_psd(-1e-15 * np.eye(2) + np.diag([1.0, 0.0]))  # rounding below zero is tolerated


def test_nearest_psd_repairs_a_broken_matrix() -> None:
    out = nearest_psd(BROKEN)
    assert np.allclose(out, out.T, atol=0)
    assert _min_eig(out) >= -1e-12
    # Higham (1988): the nearest PSD matrix in Frobenius norm clips the negative eigenvalues,
    # so its distance to the input is the root sum of their squares.
    eig = np.linalg.eigvalsh(BROKEN)
    assert np.linalg.norm(out - BROKEN) == pytest.approx(np.sqrt(np.sum(eig[eig < 0] ** 2)))
    # No other PSD matrix nearby is closer: random PSD perturbations of the answer.
    rng = np.random.default_rng(7)
    best = np.linalg.norm(out - BROKEN)
    for _ in range(200):
        g = rng.normal(scale=0.05, size=(3, 3))
        candidate = out + g @ g.T
        assert np.linalg.norm(candidate - BROKEN) >= best - 1e-12


def test_nearest_psd_is_identity_on_psd_input() -> None:
    rng = np.random.default_rng(1)
    g = rng.normal(size=(5, 5))
    psd = g @ g.T
    assert np.allclose(nearest_psd(psd), psd, atol=1e-12)
    assert np.allclose(nearest_psd(psd, keep_diagonal=True), psd, atol=1e-12)
    assert np.allclose(nearest_correlation(np.eye(3)), np.eye(3))


def test_nearest_correlation_matches_higham_2002_example() -> None:
    out = nearest_correlation(HIGHAM_A)
    assert np.allclose(out, HIGHAM_X, atol=1e-4)
    assert np.allclose(np.diag(out), 1.0, atol=1e-14)
    assert _min_eig(out) >= -1e-12
    assert np.allclose(out, out.T, atol=0)


def test_nearest_psd_keep_diagonal_keeps_variances() -> None:
    vol = np.array([0.01, 0.02, 0.03])
    cov = BROKEN * np.outer(vol, vol)
    out = nearest_psd(cov, keep_diagonal=True)
    assert np.allclose(np.diag(out), vol**2, rtol=1e-12)
    assert _min_eig(out) >= -1e-12 * np.abs(cov).max()
    corr = out / np.outer(vol, vol)
    assert np.allclose(corr, nearest_correlation(BROKEN), atol=1e-10)


def test_floor_condition_bounds_condition_number() -> None:
    rng = np.random.default_rng(3)
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    m = q @ np.diag([1.0, 1e-3, 1e-8]) @ q.T
    out = floor_condition(m, 1e4)
    eig = np.linalg.eigvalsh(out)
    assert eig.min() >= 1e-4 * (1 - 1e-9)
    assert eig.max() == pytest.approx(1.0)
    assert condition_number(out) <= 1e4 * (1 + 1e-9)
    assert np.allclose(np.sort(eig)[1:], [1e-3, 1.0])  # eigenvalues above the floor are kept
    assert np.allclose(out, out.T, atol=0)
    well = np.diag([1.0, 2.0])
    assert np.array_equal(floor_condition(well, 1e4), well)  # within the bound: unchanged
    with pytest.raises(ValueError, match="max_condition"):
        floor_condition(well, 1.0)


@pytest.mark.parametrize(
    "bad",
    [
        np.ones((2, 3)),
        np.array([[1.0, np.nan], [np.nan, 1.0]]),
        np.array([[1.0, np.inf], [np.inf, 1.0]]),
        np.ones(3),
    ],
)
def test_psd_inputs_validated(bad: np.ndarray) -> None:
    for fn in (nearest_psd, nearest_correlation, is_psd, condition_number):
        with pytest.raises(ValueError, match="matrix"):
            fn(bad)
    with pytest.raises(ValueError, match="matrix"):
        floor_condition(bad, 10.0)
    with pytest.raises(ValueError, match="positive diagonal"):
        nearest_psd(np.diag([1.0, 0.0]), keep_diagonal=True)


def test_psd_keeps_dataframe_labels() -> None:
    names = ["A", "B", "C"]
    frame = pd.DataFrame(BROKEN, index=names, columns=names)
    for out in (
        nearest_psd(frame),
        nearest_psd(frame, keep_diagonal=True),
        nearest_correlation(frame),
        floor_condition(frame + 2 * np.eye(3), 1e4),
    ):
        assert isinstance(out, pd.DataFrame)
        assert list(out.index) == names and list(out.columns) == names
    assert isinstance(nearest_psd(BROKEN), np.ndarray)
