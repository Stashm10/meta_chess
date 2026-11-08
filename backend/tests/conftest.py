import chess
import numpy as np
import pytest

from backend.game.tracker import occupancy_of

OPERA_GAME = (
    "e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5 "
    "Nxb5 cxb5 Bxb5+ Nbd7 O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6 Bxd7+ Nxd7 Qb8+ Nxb8 Rd8#"
).split()


def probs_for(occ_or_board, conf: float = 0.9, noise: float = 0.0, rng=None) -> np.ndarray:
    """Fake classifier output: `conf` on the true class, the rest split, plus optional noise."""
    occ = occupancy_of(occ_or_board) if isinstance(occ_or_board, chess.Board) else np.asarray(occ_or_board)
    p = np.full((64, 3), (1 - conf) / 2)
    p[np.arange(64), occ] = conf
    if noise:
        rng = rng or np.random.default_rng(0)
        p = p + rng.uniform(0, noise, size=p.shape)
        p /= p.sum(axis=1, keepdims=True)
    return p


@pytest.fixture
def opera_game() -> list[str]:
    return OPERA_GAME
