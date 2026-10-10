#!/usr/bin/env python3
"""Reproducible PGN -> deduplicated state pairs -> synthetic soft observations.

No camera/model training is performed. Use the project's Python environment.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import logging
import platform
import random
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import chess
import chess.pgn
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CLASSES = ".PNBRQKpnbrqk"
SQUARES = [chess.square(f, r) for r in range(7, -1, -1) for f in range(8)]
PROFILES = ["clean", "type_1", "type_5", "type_10", "occupancy_3",
            "color_2", "neighbor_swap", "confident_wrong_5", "region_uncertain", "mixed_10"]
SPLITS = ["train", "validation", "test"]
OPERATIONS = ["legal_move", "unchanged", "illegal_relocation"]
SOURCES = {
    "2013-01": "aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635",
    "2013-02": "c136acdf343293c45252906fee91e3b561fb26a936979f52dbe04bb649a2fd86",
}


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def state(board):
    # Ignore move counters; retain effective en passant and castling information.
    return " ".join(board.fen(en_passant="legal").split()[:4])


def labels(board):
    return np.array([CLASSES.index(p.symbol()) if (p := board.piece_at(s)) else 0
                     for s in SQUARES], dtype=np.uint8)


def transition_id(previous_fen, current_board_fen, operation):
    return digest("|".join([state(chess.Board(previous_fen)), current_board_fen, operation]))


def split_for(group_id):
    bucket = int(group_id[:8], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def phase(board, ply):
    if len(board.piece_map()) <= 12:
        return "endgame"
    return "opening" if ply <= 24 else "middlegame"


def make_row(before, after, operation, group_id, source, ply, move="", **extra):
    return {
        "id": transition_id(before.fen(), after.board_fen(), operation),
        "previous_fen": before.fen(en_passant="legal"),
        "current_board_fen": after.board_fen(),
        "next_game_fen": after.fen(en_passant="legal") if operation == "legal_move" else before.fen(en_passant="legal"),
        "operation": operation, "game_id": group_id, "split": split_for(group_id),
        "source": source, "ply": ply, "move_uci": move, "phase": phase(before, ply),
        **extra,
    }


def fetch(raw):
    raw.mkdir(parents=True, exist_ok=True)
    records = []
    for month, expected in SOURCES.items():
        name = f"lichess_db_standard_rated_{month}.pgn.zst"
        path = raw / name
        url = f"https://database.lichess.org/standard/{name}"
        if not path.exists():
            temporary = path.with_suffix(".download")
            with urllib.request.urlopen(url, timeout=90) as response, temporary.open("wb") as out:
                shutil.copyfileobj(response, out)
            temporary.rename(path)
        actual = file_hash(path)
        if actual != expected:
            raise ValueError(f"Checksum mismatch: {path}")
        records.append({"id": month, "url": url, "file": str(path.relative_to(ROOT)),
                        "sha256": actual, "bytes": path.stat().st_size,
                        "license": "CC0-1.0", "license_url": "https://database.lichess.org/",
                        "checksum_url": "https://database.lichess.org/standard/sha256sums.txt"})
    dump(raw / "sources.json", {"retrieved_date": "2026-10-11", "sources": records})
    return records


def collect(raw, game_limit, seed):
    counts = collections.Counter()
    seen_games, seen_transitions = set(), set()
    rows = []
    logging.getLogger("chess.pgn").setLevel(logging.CRITICAL)
    for month in SOURCES:
        path = raw / f"lichess_db_standard_rated_{month}.pgn.zst"
        proc = subprocess.Popen(["zstd", "-dc", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        stream = io.TextIOWrapper(proc.stdout, encoding="utf-8")
        try:
            for ordinal in range(game_limit):
                game = chess.pgn.read_game(stream)
                if game is None:
                    break
                counts["games_read"] += 1
                if game.errors or game.headers.get("Variant", "Standard") != "Standard":
                    counts["games_rejected_parse_or_variant"] += 1
                    continue
                board = game.board()
                moves = list(game.mainline_moves())
                if not board.is_valid() or len(moves) < 4:
                    counts["games_rejected_short_or_invalid"] += 1
                    continue
                gid = digest(state(board) + "|" + " ".join(m.uci() for m in moves))
                if gid in seen_games:
                    counts["duplicate_games_removed"] += 1
                    continue
                seen_games.add(gid)
                rng = random.Random(int(gid[:16], 16) ^ seed)
                chosen = set(rng.sample(range(len(moves)), min(12, len(moves))))
                pending = []
                valid = True
                for i, move in enumerate(moves):
                    if not board.is_legal(move):
                        valid = False
                        counts["games_rejected_illegal_move"] += 1
                        break
                    counts["legal_plies_replayed"] += 1
                    special = {"capture": board.is_capture(move), "castling": board.is_castling(move),
                               "en_passant": board.is_en_passant(move),
                               "promotion": chess.piece_symbol(move.promotion) if move.promotion else None}
                    keep = i in chosen or special["castling"] or special["en_passant"] or special["promotion"]
                    before = board.copy(stack=False) if keep else None
                    board.push(move)
                    if not board.is_valid():
                        valid = False
                        counts["games_rejected_invalid_successor"] += 1
                        break
                    if keep:
                        row = make_row(before, board, "legal_move", gid, f"lichess:{month}", i + 1,
                                       move.uci(), archive_game_ordinal=ordinal, provenance="recorded_pgn", **special)
                        pending.append(row)
                        # Supplement all promotion choices from reachable PGN parents.
                        if move.promotion:
                            for promotion in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]:
                                alt = chess.Move(move.from_square, move.to_square, promotion=promotion)
                                if alt == move or not before.is_legal(alt):
                                    continue
                                end = before.copy(stack=False)
                                end.push(alt)
                                flags = {**special, "promotion": chess.piece_symbol(promotion)}
                                pending.append(make_row(before, end, "legal_move", gid, f"lichess:{month}", i + 1,
                                                        alt.uci(), archive_game_ordinal=ordinal,
                                                        provenance="generated_legal_promotion_alternative", **flags))
                if not valid:
                    continue
                counts["valid_unique_games"] += 1
                for row in pending:
                    if row["id"] in seen_transitions:
                        counts["duplicate_transitions_removed"] += 1
                        continue
                    seen_transitions.add(row["id"])
                    rows.append(row)
                if (ordinal + 1) % 1000 == 0:
                    print(f"Parsed {month}: {ordinal + 1} games; {len(rows)} unique transitions", flush=True)
        finally:
            stream.close()
            proc.wait()
    return rows, counts


def remove_leakage(rows):
    # More conservative than FEN identity: exclude shared physical placements even
    # if rights/turn differ. Both endpoints are checked, before augmentation.
    memberships = collections.defaultdict(set)
    for row in rows:
        for placement in [row["previous_fen"].split()[0], row["current_board_fen"]]:
            memberships[placement].add(row["split"])
    shared = {p for p, splits in memberships.items() if len(splits) > 1}
    safe = [r for r in rows if r["previous_fen"].split()[0] not in shared and r["current_board_fen"] not in shared]
    return safe, len(rows) - len(safe), len(shared)


def illegal_row(row, seed):
    before = chess.Board(row["previous_fen"])
    rng = random.Random(int(row["id"][:16], 16) ^ seed)
    sources = list(before.piece_map())
    targets = list(chess.SquareSet(~before.occupied))
    rng.shuffle(sources)
    rng.shuffle(targets)
    legal_placements = set()
    for move in before.legal_moves:
        end = before.copy(stack=False)
        end.push(move)
        legal_placements.add(end.board_fen())
    for src in sources:
        piece = before.piece_at(src)
        if piece.piece_type == chess.KING:
            continue
        for dst in targets:
            if piece.piece_type == chess.PAWN and chess.square_rank(dst) in (0, 7):
                continue
            move = chess.Move(src, dst)
            if before.is_legal(move):
                continue
            after = before.copy(stack=False)
            after.remove_piece_at(src)
            after.set_piece_at(dst, piece)
            if after.board_fen() in legal_placements:
                continue
            return make_row(before, after, "illegal_relocation", row["game_id"], row["source"], row["ply"],
                            move.uci(), provenance="generated_physical_mistake",
                            archive_game_ordinal=row["archive_game_ordinal"],
                            capture=False, castling=False, en_passant=False, promotion=None)
    raise ValueError("No supported physical mistake found")


def select(rows, total, seed):
    selected, used = [], set()
    placement_owners = positions_by_split(rows)
    for split, fraction in zip(SPLITS, [.8, .1, .1]):
        n = int(total * fraction)
        legal_n = int(n * .9)
        noop_n = int(n * .075)
        illegal_n = n - legal_n - noop_n
        pool = sorted([r for r in rows if r["split"] == split], key=lambda r: digest(f"{seed}|{r['id']}"))
        legal = []

        def add(row):
            if row["id"] not in used and len(legal) < legal_n:
                used.add(row["id"])
                legal.append(row)

        # Reserve coverage for rare events before stratified phase selection.
        for kind, value in [("castling", True), ("en_passant", True)] + [("promotion", p) for p in "qrbn"]:
            for row in [r for r in pool if r[kind] == value][:max(2, legal_n // 200)]:
                add(row)
        for stage, ratio in [("opening", .2), ("middlegame", .45), ("endgame", .35)]:
            need = max(0, int(legal_n * ratio) - sum(r["phase"] == stage for r in legal))
            for row in [r for r in pool if r["phase"] == stage and r["id"] not in used][:need]:
                add(row)
        for row in pool:
            if len(legal) >= legal_n:
                break
            add(row)
        if len(legal) != legal_n:
            raise ValueError(f"Insufficient {split} legal seeds: {len(legal)}/{legal_n}")
        selected.extend(legal)
        derived = collections.Counter()
        for row in pool:
            before = chess.Board(row["previous_fen"])
            if derived["unchanged"] < noop_n:
                item = make_row(before, before, "unchanged", row["game_id"], row["source"], row["ply"],
                                provenance="generated_unchanged", archive_game_ordinal=row["archive_game_ordinal"],
                                capture=False, castling=False, en_passant=False, promotion=None)
            elif derived["illegal_relocation"] < illegal_n:
                item = illegal_row(row, seed)
                # New illegal placements must not duplicate a physical placement
                # assigned to another split anywhere in the safe source pool.
                if any(item["current_board_fen"] in positions for other, positions in placement_owners.items() if other != split):
                    continue
            else:
                break
            if item["id"] not in used:
                selected.append(item)
                used.add(item["id"])
                placement_owners[split].add(item["current_board_fen"])
                derived[item["operation"]] += 1
        if derived["unchanged"] != noop_n or derived["illegal_relocation"] != illegal_n:
            raise ValueError(f"Insufficient {split} derived seeds: {derived}")
    if len(selected) != total:
        raise ValueError(f"Expected {total} transitions, got {len(selected)}")
    # A generated mistake can coincide with another generated mistake. Validate
    # globally rather than silently accepting a cross-split collision.
    check_disjoint(selected)
    return sorted(selected, key=lambda r: (SPLITS.index(r["split"]), r["id"]))


def positions_by_split(rows):
    out = {s: set() for s in SPLITS}
    for row in rows:
        out[row["split"]].update([row["previous_fen"].split()[0], row["current_board_fen"]])
    return out


def check_disjoint(rows):
    layouts = positions_by_split(rows)
    groups = {s: {r["game_id"] for r in rows if r["split"] == s} for s in SPLITS}
    for i, a in enumerate(SPLITS):
        for b in SPLITS[i + 1:]:
            if layouts[a] & layouts[b] or groups[a] & groups[b]:
                raise ValueError(f"Split leakage between {a} and {b}")


def probabilities(target, profile, rng):
    # Finite non-one-hot rows. Noise is a documented simulation, not measured
    # ChessQ Lite confidence or a learned empirical error distribution.
    q = rng.dirichlet(np.ones(13), size=64)
    q[np.arange(64), target] = 0
    q /= q.sum(axis=1, keepdims=True)
    mass = rng.uniform(.90, .999, size=64)
    q *= (1 - mass[:, None])
    q[np.arange(64), target] = mass
    affected = set()
    occupied = np.flatnonzero(target)

    def wrong(index, category, confident=False):
        truth = int(target[index])
        if category == truth:
            return
        rest = rng.dirichlet(np.ones(13))
        rest[[truth, category]] = 0
        rest /= rest.sum()
        wrong_mass = rng.uniform(.94, .995) if confident else rng.uniform(.48, .85)
        true_mass = (1 - wrong_mass) * rng.uniform(.55, .9)
        q[index] = rest * (1 - wrong_mass - true_mass)
        q[index, category] = wrong_mass
        q[index, truth] = true_mass
        affected.add(int(index))

    def same_color_type(category):
        symbol = CLASSES[int(category)]
        paired = {"P": "B", "B": "P", "R": "Q", "Q": "R", "N": "B", "K": "Q"}[symbol.upper()]
        return CLASSES.index(paired if symbol.isupper() else paired.lower())

    if profile.startswith("type_"):
        n = min(int(profile.rsplit("_", 1)[1]), len(occupied))
        for i in rng.choice(occupied, n, replace=False):
            wrong(i, same_color_type(target[i]))
    elif profile == "occupancy_3":
        choices = list(rng.choice(occupied, min(2, len(occupied)), replace=False))
        empty = np.flatnonzero(target == 0)
        if len(empty):
            choices.append(rng.choice(empty))
        for i in choices:
            wrong(i, 0 if target[i] else int(rng.integers(1, 13)))
    elif profile == "color_2":
        for i in rng.choice(occupied, min(2, len(occupied)), replace=False):
            wrong(i, int(target[i]) + (6 if target[i] <= 6 else -6))
    elif profile == "neighbor_swap":
        for i in rng.permutation(occupied):
            rank, file = divmod(int(i), 8)
            neighbors = [(rank + dr) * 8 + file + df for dr, df in [(0, 1), (0, -1), (1, 0), (-1, 0)]
                         if 0 <= rank + dr < 8 and 0 <= file + df < 8]
            candidates = [j for j in neighbors if target[j] != target[i]]
            if candidates:
                j = rng.choice(candidates)
                wrong(i, int(target[j]))
                wrong(j, int(target[i]))
                break
    elif profile == "confident_wrong_5":
        for i in rng.choice(64, 5, replace=False):
            other = same_color_type(target[i]) if target[i] else int(rng.integers(1, 13))
            wrong(i, other, confident=True)
    elif profile == "region_uncertain":
        r, c = rng.integers(0, 6, size=2)
        for i in [(r + dr) * 8 + c + dc for dr in range(3) for dc in range(3)]:
            q[i] = rng.dirichlet(np.full(13, 20.0))
            affected.add(int(i))
    elif profile == "mixed_10":
        for i in rng.choice(64, 10, replace=False):
            choices = [j for j in range(13) if j != target[i]]
            wrong(i, int(rng.choice(choices)), confident=rng.random() < .3)
    elif profile != "clean":
        raise ValueError(profile)
    q = np.maximum(q, 1e-9)
    q /= q.sum(axis=1, keepdims=True)
    return q.astype(np.float32), len(affected)


def validate_rows(rows):
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate transitions")
    check_disjoint(rows)
    for row in rows:
        before = chess.Board(row["previous_fen"])
        if not before.is_valid():
            raise ValueError("Invalid previous board")
        if row["id"] != transition_id(row["previous_fen"], row["current_board_fen"], row["operation"]):
            raise ValueError("Incorrect transition identity")
        if row["operation"] == "legal_move":
            move = chess.Move.from_uci(row["move_uci"])
            if not before.is_legal(move):
                raise ValueError("Illegal move labeled legal")
            before.push(move)
            if before.board_fen() != row["current_board_fen"] or before.fen(en_passant="legal") != row["next_game_fen"]:
                raise ValueError("Incorrect legal target")
        elif row["operation"] == "unchanged":
            if before.board_fen() != row["current_board_fen"] or before.fen(en_passant="legal") != row["next_game_fen"]:
                raise ValueError("Incorrect unchanged target")
        else:
            move = chess.Move.from_uci(row["move_uci"])
            piece = before.piece_at(move.from_square)
            if not piece or before.piece_at(move.to_square) or before.is_legal(move):
                raise ValueError("Invalid physical mistake")
            after = before.copy(stack=False)
            after.remove_piece_at(move.from_square)
            after.set_piece_at(move.to_square, piece)
            if after.board_fen() != row["current_board_fen"] or row["next_game_fen"] != before.fen(en_passant="legal"):
                raise ValueError("Physical mistake changed legal game or identity")


def generate(rows, output, seed, shard_transitions=500):
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, output / "generator-source.py")
    with (output / "transitions.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    files = []
    for split in SPLITS:
        indices = [i for i, r in enumerate(rows) if r["split"] == split]
        for shard, start in enumerate(range(0, len(indices), shard_transitions)):
            batch = indices[start:start + shard_transitions]
            q, previous, target, tid, profiles, operation, affected = [], [], [], [], [], [], []
            for i in batch:
                row = rows[i]
                old = labels(chess.Board(row["previous_fen"]))
                truth = labels(chess.Board(row["current_board_fen"] + " w - - 0 1"))
                for p, profile in enumerate(PROFILES):
                    rng = np.random.default_rng(int(digest(f"{seed}|{row['id']}|{profile}")[:16], 16))
                    obs, count = probabilities(truth, profile, rng)
                    q.append(obs); previous.append(old); target.append(truth); tid.append(i)
                    profiles.append(p); operation.append(OPERATIONS.index(row["operation"])); affected.append(count)
            name = f"{split}-{shard:03d}.npz"
            np.savez_compressed(output / name, probabilities=np.stack(q), previous=np.stack(previous),
                                target=np.stack(target), transition_index=np.array(tid, dtype=np.uint32),
                                noise_profile=np.array(profiles, dtype=np.uint8),
                                operation=np.array(operation, dtype=np.uint8),
                                perturbed_squares=np.array(affected, dtype=np.uint8))
            files.append({"file": name, "samples": len(q), "sha256": file_hash(output / name)})
            print(f"Wrote {name}: {len(q)} samples", flush=True)
    return files


def validate_dataset(output):
    rows = [json.loads(line) for line in (output / "transitions.jsonl").read_text().splitlines()]
    manifest = json.loads((output / "manifest.json").read_text())
    if file_hash(output / "transitions.jsonl") != manifest["transition_sha256"]:
        raise ValueError("Transition metadata checksum mismatch")
    if manifest["classes"] != list(CLASSES) or manifest["square_order"] != [chess.square_name(s) for s in SQUARES]:
        raise ValueError("Model encoding mismatch")
    validate_rows(rows)
    truth_by_row = [labels(chess.Board(r["current_board_fen"] + " w - - 0 1")) for r in rows]
    old_by_row = [labels(chess.Board(r["previous_fen"])) for r in rows]
    state_by_row = [state(chess.Board(r["previous_fen"])).encode() for r in rows]
    expected = {(i, p) for i in range(len(rows)) for p in range(10)}
    seen, fingerprints = set(), set()
    sample_count = 0
    for entry in manifest["shards"]:
        path = output / entry["file"]
        if file_hash(path) != entry["sha256"]:
            raise ValueError("Shard checksum mismatch")
        with np.load(path, allow_pickle=False) as data:
            q = data["probabilities"]
            n = len(q)
            if n != entry["samples"] or data["previous"].shape != (n, 64) or data["target"].shape != (n, 64):
                raise ValueError("Shard size or label shape mismatch")
            if any(data[k].shape != (n,) for k in ["transition_index", "noise_profile", "operation", "perturbed_squares"]):
                raise ValueError("Sample metadata shape mismatch")
            if q.shape != (n, 64, 13) or not np.isfinite(q).all() or np.any(q < 0) or not np.allclose(q.sum(-1), 1, atol=1e-6):
                raise ValueError("Invalid probabilities")
            for j, (index, profile) in enumerate(zip(data["transition_index"], data["noise_profile"])):
                i, p = int(index), int(profile)
                if not (0 <= i < len(rows) and 0 <= p < len(PROFILES)):
                    raise ValueError("Invalid transition or noise profile index")
                key = (i, p)
                if key in seen:
                    raise ValueError("Duplicate observation variant")
                seen.add(key)
                row = rows[i]
                truth = truth_by_row[i]
                old = old_by_row[i]
                if not np.array_equal(truth, data["target"][j]) or not np.array_equal(old, data["previous"][j]):
                    raise ValueError("Labels do not match FEN / square order")
                if entry["file"].split("-")[0] != row["split"] or data["operation"][j] != OPERATIONS.index(row["operation"]):
                    raise ValueError("Incorrect split or operation")
                fingerprint = digest(state_by_row[i] + q[j].tobytes())
                if fingerprint in fingerprints:
                    raise ValueError("Duplicate model input")
                fingerprints.add(fingerprint)
                if p == 0 and not np.array_equal(q[j].argmax(-1), truth):
                    raise ValueError("Clean observation has wrong argmax")
            sample_count += n
    if seen != expected or sample_count != len(rows) * 10:
        raise ValueError("Missing observation variants")
    report = {"passed": True, "transitions": len(rows), "samples": sample_count,
              "duplicate_transitions": 0, "duplicate_inputs": 0, "cross_split_games": 0,
              "cross_split_board_placements": 0, "probability_rows_checked": sample_count * 64,
              "all_targets_replayed_or_physically_verified": True}
    dump(output / "validation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "build", "validate"])
    parser.add_argument("--raw", type=Path, default=ROOT / "data/crf/raw")
    parser.add_argument("--output", type=Path, default=ROOT / "data/crf/synthetic-v1")
    parser.add_argument("--transitions", type=int, default=20000)
    parser.add_argument("--games-per-source", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=20261011)
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps(validate_dataset(args.output), indent=2))
        return
    sources = fetch(args.raw)
    if args.command == "fetch":
        print(json.dumps(sources, indent=2))
        return
    if args.transitions % 200 or args.transitions < 200:
        parser.error("--transitions must be a positive multiple of 200")
    if args.output.exists():
        parser.error("Output already exists; use a new version directory")
    started = time.monotonic()
    rows, counts = collect(args.raw, args.games_per_source, args.seed)
    safe, excluded, shared = remove_leakage(rows)
    counts.update(cross_split_transitions_excluded=excluded, shared_placements_excluded=shared,
                  available_unique_transitions=len(rows), available_split_safe_transitions=len(safe))
    chosen = select(safe, args.transitions, args.seed)
    validate_rows(chosen)
    print("Seed validation passed", json.dumps(dict(counts)), flush=True)
    files = generate(chosen, args.output, args.seed)
    coverage = {key: dict(collections.Counter(str(r[key]) for r in chosen))
                for key in ["split", "phase", "operation", "source", "provenance", "promotion", "castling", "en_passant", "capture"]}
    manifest = {"schema_version": 1, "created_date": "2026-10-11", "seed": args.seed,
                "transitions": len(chosen), "samples": len(chosen) * 10,
                "classes": list(CLASSES), "square_order": [chess.square_name(s) for s in SQUARES],
                "noise_profiles": PROFILES, "operations": OPERATIONS,
                "sources": sources, "source_games_read_per_archive_limit": args.games_per_source,
                "counts": dict(counts), "coverage": coverage, "shards": files,
                "transition_sha256": file_hash(args.output / "transitions.jsonl"),
                "generator_sha256": file_hash(__file__), "python": platform.python_version(),
                "numpy": np.__version__, "python_chess": chess.__version__,
                "observations": "synthetic probabilities; not measured ChessQ Lite outputs",
                "decision_supervision": "operation labels only; acceptance thresholds need real validation",
                "limitations": ["Two early Lichess monthly archives; not representative of all modern play",
                                "Uniform profile mixture is a stress/training distribution, not deployment error frequency",
                                "Unchanged/illegal events and alternate promotions are generated, not observed physical events"],
                "generation_seconds": round(time.monotonic() - started, 2)}
    dump(args.output / "manifest.json", manifest)
    print(json.dumps(validate_dataset(args.output), indent=2), flush=True)


if __name__ == "__main__":
    main()
