"""SB3 policy helpers for reward-conditioned observations."""
from __future__ import annotations

import torch
from gymnasium import spaces
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from torch import nn


class RewardConditionedExtractor(BaseFeaturesExtractor):
    """Encode battlefield state and reward weights through separate towers.

    The environment keeps a single flat Box observation for SB3 compatibility:
    all grid/state features first, followed by the 4 reward weights. A plain
    MLP can treat those 4 values as a tiny tail on a 604-dim vector. This
    extractor gives the weight tail its own representation before fusing it
    with the state embedding.
    """

    def __init__(
        self,
        observation_space: spaces.Box,
        state_features_dim: int = 128,
        weight_features_dim: int = 32,
    ) -> None:
        if observation_space.shape is None or len(observation_space.shape) != 1:
            raise ValueError("RewardConditionedExtractor expects a flat Box observation")
        obs_dim = int(observation_space.shape[0])
        if obs_dim <= 4:
            raise ValueError("observation must contain state features plus 4 reward weights")
        if state_features_dim <= 0 or weight_features_dim <= 0:
            raise ValueError("feature dimensions must be positive")

        super().__init__(observation_space, features_dim=state_features_dim + weight_features_dim)
        self.state_dim = obs_dim - 4
        self.state_net = nn.Sequential(
            nn.Linear(self.state_dim, 256),
            nn.ReLU(),
            nn.Linear(256, state_features_dim),
            nn.ReLU(),
        )
        self.weight_net = nn.Sequential(
            nn.Linear(4, 32),
            nn.ReLU(),
            nn.Linear(32, weight_features_dim),
            nn.ReLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        state = observations[:, : self.state_dim]
        weights = observations[:, self.state_dim :]
        return torch.cat((self.state_net(state), self.weight_net(weights)), dim=1)


def policy_kwargs_for_arch(arch: str) -> dict | None:
    """Return SB3 policy kwargs for the requested architecture name."""
    if arch == "mlp":
        return None
    if arch == "conditioned":
        return {
            "features_extractor_class": RewardConditionedExtractor,
            "features_extractor_kwargs": {
                "state_features_dim": 128,
                "weight_features_dim": 32,
            },
            "net_arch": {
                "pi": [128, 128],
                "vf": [128, 128],
            },
        }
    raise ValueError(f"Unknown policy architecture: {arch}")
