"""
Phase 6 triage classifier — a small feedforward net over 384-dim
sentence-transformer embeddings, producing a single logit for the binary
"is this comment likely officiating-related" decision.

Deliberately tiny: this is a cheap pre-filter for the expensive LLM+RAG
step that actually produces the AI Verdict. Model capacity beyond what
sits below buys nothing on a ~10k-row corpus and would just overfit.
"""

from __future__ import annotations

from pathlib import Path

import torch
from torch import nn


EMBEDDING_DIM = 384
HIDDEN_DIM = 64
DROPOUT = 0.3

WEIGHTS_PATH = Path(__file__).resolve().parent / "triage_classifier_weights.pt"


class TriageClassifier(nn.Module):
    def __init__(
        self,
        embedding_dim: int = EMBEDDING_DIM,
        hidden_dim: int = HIDDEN_DIM,
        dropout: float = DROPOUT,
    ) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(embedding_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Returns raw logits (N,) — pair with BCEWithLogitsLoss for training,
        # torch.sigmoid at inference time for probabilities.
        return self.net(x).squeeze(-1)


def load_trained(
    weights_path: Path | str = WEIGHTS_PATH,
    device: str | torch.device = "cpu",
) -> TriageClassifier:
    model = TriageClassifier()
    model.load_state_dict(torch.load(weights_path, map_location=device))
    model.to(device).eval()
    return model
