"""Chess moves as short plain-English phrases for TTS."""

import chess

PIECE_NAMES = {
    chess.PAWN: "Pawn",
    chess.KNIGHT: "Knight",
    chess.BISHOP: "Bishop",
    chess.ROOK: "Rook",
    chess.QUEEN: "Queen",
    chess.KING: "King",
}


def move_to_speech(board: chess.Board, move: chess.Move) -> str:
    """Describe `move`, which must be legal in `board` (call before pushing it)."""
    if board.is_castling(move):
        text = "Castle kingside" if board.is_kingside_castling(move) else "Castle queenside"
    else:
        piece = board.piece_type_at(move.from_square)
        verb = "takes" if board.is_capture(move) else "to"
        target = chess.square_name(move.to_square)
        if piece != chess.PAWN and _needs_disambiguation(board, move):
            text = f"{PIECE_NAMES[piece]} from {chess.square_name(move.from_square)} {verb} {target}"
        else:
            text = f"{PIECE_NAMES[piece]} {verb} {target}"
        if move.promotion:
            text += f", promote to {PIECE_NAMES[move.promotion].lower()}"

    board.push(move)
    try:
        if board.is_checkmate():
            text += ", checkmate"
        elif board.is_check():
            text += ", check"
    finally:
        board.pop()
    return text


def _needs_disambiguation(board: chess.Board, move: chess.Move) -> bool:
    piece = board.piece_type_at(move.from_square)
    return any(
        m.to_square == move.to_square
        and m.from_square != move.from_square
        and board.piece_type_at(m.from_square) == piece
        for m in board.legal_moves
    )
