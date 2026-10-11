"""Frozen-v1 residual scorers of cached probabilities; no image model involved."""
from __future__ import annotations

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

try:
    from . import crf_postprocessor as v1
except ImportError:
    import crf_postprocessor as v1

KINDS = ("feature_mlp", "square_mlp", "move_mlp1", "move_mlp2", "cnn", "attention")


def head(inputs, hidden, depth=1):
    layers = []
    for _ in range(depth):
        layers.extend((nn.Linear(inputs, hidden), nn.SiLU()))
        inputs = hidden
    layers.append(nn.Linear(inputs, 1))
    nn.init.zeros_(layers[-1].weight)
    nn.init.zeros_(layers[-1].bias)
    return nn.Sequential(*layers)


def delta(values, squares, before, after):
    """Candidate-local differences, cancelling unchanged squares exactly."""
    b, obs = values.shape[:2]
    padded = F.pad(values, (0, 0, 0, 1)).flatten(-2)
    def take(label):
        index = (squares * 13 + label).reshape(b, 1, -1).expand(-1, obs, -1)
        return padded.gather(2, index).reshape(b, obs, -1, 4)
    return (take(after) - take(before)).sum(-1)


def gather_squares(values, squares):
    b, obs, _, width = values.shape
    padded = F.pad(values, (0, 0, 0, 1))
    index = squares.reshape(b, 1, -1, 1).expand(-1, obs, -1, width)
    return padded.gather(2, index).reshape(b, obs, -1, 4, width)


class ResidualScorer(nn.Module):
    def __init__(self, kind, baseline):
        super().__init__()
        if kind not in KINDS:
            raise ValueError(kind)
        self.kind = kind
        self.register_buffer("baseline", torch.as_tensor(np.array(baseline), dtype=torch.float32))
        coords = torch.stack(torch.meshgrid(torch.linspace(-1, 1, 8),
                                           torch.linspace(-1, 1, 8), indexing="ij"), -1)
        self.register_buffer("coords", coords.reshape(64, 2))
        if kind == "feature_mlp":
            self.output = head(25, 32)
        elif kind.startswith("move_mlp"):
            self.encode = nn.Sequential(nn.Linear(39, 8), nn.SiLU())
            self.piece = nn.Embedding(13, 4)
            # 4 slots: 8 encoded + 4 before + 4 after + 2 coords + 2 log-evidence.
            self.output = head(4 * 20 + 7, 32, 2 if kind == "move_mlp2" else 1)
        else:
            self.operation = nn.Linear(7, 1, bias=False)
            nn.init.zeros_(self.operation.weight)
            if kind == "square_mlp":
                self.encode = nn.Sequential(nn.Linear(39, 32), nn.SiLU(), nn.Linear(32, 13))
                last = self.encode[-1]
            elif kind == "cnn":
                self.encode = nn.Sequential(nn.Conv2d(41, 16, 3, padding=1), nn.SiLU(),
                                            nn.Conv2d(16, 16, 3, padding=1), nn.SiLU(),
                                            nn.Conv2d(16, 13, 1))
                last = self.encode[-1]
            else:
                self.project = nn.Linear(41, 32)
                self.encode = nn.TransformerEncoderLayer(32, 4, 64, dropout=0.,
                                                         batch_first=True, norm_first=True)
                self.output = nn.Linear(32, 13)
                last = self.output
            nn.init.zeros_(last.weight)
            nn.init.zeros_(last.bias)

    def forward(self, q, previous, squares, before, after, flags, mask):
        b, obs = q.shape[:2]
        base = v1.torch_scores(q, squares, before, after, flags, self.baseline)
        logq = q.clamp_min(v1.EPSILON).log() / 8.
        if self.kind == "feature_mlp":
            # Exactly the 18 visual v1 feature differences plus 7 operation flags.
            gathered_log = gather_squares(logq, squares)
            change = (F.one_hot(after, 13) - F.one_hot(before, 13)).float()[:, None]
            class_features = (gathered_log * change).sum(-2)
            group_q = torch.stack((q[..., 0], q[..., 1:7].sum(-1), q[..., 7:].sum(-1)), -1)
            groups = torch.as_tensor(v1.GROUPS, device=q.device)
            group_change = (F.one_hot(groups[after], 3) - F.one_hot(groups[before], 3)).float()[:, None]
            group_features = (gather_squares(group_q.clamp_min(v1.EPSILON).log() / 8., squares)
                              * group_change).sum(-2)
            prob_delta = delta(q, squares, before, after)[..., None]
            match_delta = delta(F.one_hot(q.argmax(-1), 13).float(), squares, before, after)[..., None]
            features = torch.cat((class_features, group_features, prob_delta, match_delta,
                                  flags[:, None].expand(-1, obs, -1, -1)), -1)
            residual = self.output(features).squeeze(-1)
        else:
            prior = F.one_hot(previous, 13).float()[:, None].expand(-1, obs, -1, -1)
            features = torch.cat((q, logq, prior), -1)
            if self.kind.startswith("move_mlp"):
                encoded = gather_squares(self.encode(features), squares)
                coords = gather_squares(self.coords[None, None].expand(b, obs, -1, -1), squares)
                local_q = gather_squares(logq, squares)
                def evidence(label):
                    return local_q.gather(-1, label[:, None, ..., None].expand(-1, obs, -1, -1, -1))
                piece_before = self.piece(before)[:, None].expand(-1, obs, -1, -1, -1)
                piece_after = self.piece(after)[:, None].expand(-1, obs, -1, -1, -1)
                local = torch.cat((encoded, piece_before, piece_after, coords,
                                   evidence(before), evidence(after)), -1)
                local = local * (squares != 64)[:, None, ..., None]
                joined = torch.cat((local.flatten(-2), flags[:, None].expand(-1, obs, -1, -1)), -1)
                residual = self.output(joined).squeeze(-1)
            else:
                if self.kind == "square_mlp":
                    potentials = self.encode(features)
                else:
                    features = torch.cat((features, self.coords[None, None].expand(b, obs, -1, -1)), -1)
                    if self.kind == "cnn":
                        image = features.reshape(b * obs, 8, 8, 41).permute(0, 3, 1, 2)
                        potentials = self.encode(image).permute(0, 2, 3, 1).reshape(b, obs, 64, 13)
                    else:
                        tokens = self.project(features.reshape(b * obs, 64, 41))
                        potentials = self.output(self.encode(tokens)).reshape(b, obs, 64, 13)
                residual = (delta(potentials, squares, before, after)
                            + self.operation(flags).squeeze(-1)[:, None])
        return (base + residual).masked_fill(~mask[:, None], -1e9)


def load_model(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if checkpoint["schema"] != 1 or checkpoint["classes"] != v1.CLASSES:
        raise ValueError("Incompatible neural checkpoint")
    model = ResidualScorer(checkpoint["kind"], checkpoint["state_dict"]["baseline"].numpy())
    model.load_state_dict(checkpoint["state_dict"])
    return model.eval(), checkpoint


@torch.no_grad()
def score_one(model, q, previous, options):
    inputs = [torch.as_tensor(x, dtype=torch.long)[None]
              for x in (previous, options.squares.astype(np.int64), options.before, options.after)]
    return model(torch.as_tensor(q, dtype=torch.float32)[None, None], *inputs,
                 torch.as_tensor(options.flags, dtype=torch.float32)[None],
                 torch.ones((1, len(options.boards)), dtype=torch.bool))[0, 0].numpy()
