import chess
import pytest

from backend.game.speech import move_to_speech


def say(fen: str, san: str) -> str:
    board = chess.Board(fen)
    text = move_to_speech(board, board.parse_san(san))
    assert board.fen() == chess.Board(fen).fen(), "board must be left unchanged"
    return text


START = chess.STARTING_FEN


@pytest.mark.parametrize(
    "fen,san,expected",
    [
        (START, "e4", "Pawn to e4"),
        (START, "Nf3", "Knight to f3"),
        ("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "exd5", "Pawn takes d5"),
        ("r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4", "Bxc6", "Bishop takes c6"),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "O-O", "Castle kingside"),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "O-O-O", "Castle queenside"),
        ("4k3/8/8/8/8/8/8/R4RK1 w - - 0 1", "Rad1", "Rook from a1 to d1"),
        ("4k3/8/8/8/8/8/8/R4RK1 w - - 0 1", "Rfd1", "Rook from f1 to d1"),
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e8=Q", "Pawn to e8, promote to queen"),
        ("3r4/4P3/8/8/8/8/k7/4K3 w - - 0 1", "exd8=N", "Pawn takes d8, promote to knight"),
        ("4k3/8/8/8/8/8/8/R3K3 w - - 0 1", "Ra8+", "Rook to a8, check"),
        ("rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2", "Qh4#", "Queen to h4, checkmate"),
        ("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3", "exf6", "Pawn takes f6"),
    ],
)
def test_move_to_speech(fen, san, expected):
    assert say(fen, san) == expected


def test_opera_game_speaks_every_move(opera_game):
    board = chess.Board()
    for san in opera_game:
        move = board.parse_san(san)
        text = move_to_speech(board, move)
        assert text and text[0].isupper()
        board.push(move)
    assert text == "Rook to d8, checkmate"
