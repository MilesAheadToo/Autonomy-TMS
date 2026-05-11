#!/usr/bin/env python3
"""PPO fine-tune driver for the CapacityPromise policy over the twin.

torch required (GPU container).

Usage:
    python scripts/finetune/rl/train_capacity_promise_rl.py \\
        --total-updates 50 --rollout-length 256 --phase 2 \\
        --checkpoint /app/checkpoints/{tenant}/{config}/trm/capacity_promise_rl_v1.pt
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.finetune.rl.ppo_trainer import HAS_TORCH, PPOConfig, PPOTrainer  # noqa: E402
from scripts.finetune.rl.twin_env import CapacityPromiseTwinEnv  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_capacity_promise_rl")


def main() -> int:
    if not HAS_TORCH:
        logger.error("torch is required for PPO training but is not installed.")
        return 2

    ap = argparse.ArgumentParser(description="PPO fine-tune CapacityPromise over the twin")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--phase", type=int, default=2, choices=(1, 2, 3))
    ap.add_argument("--horizon-steps", type=int, default=50)
    ap.add_argument("--total-updates", type=int, default=50)
    ap.add_argument("--rollout-length", type=int, default=256)
    ap.add_argument("--minibatch-size", type=int, default=64)
    ap.add_argument("--epochs-per-update", type=int, default=4)
    ap.add_argument("--learning-rate", type=float, default=3e-4)
    ap.add_argument("--checkpoint", type=Path, default=None,
                    help="Optional output path for the trained policy")
    args = ap.parse_args()

    env = CapacityPromiseTwinEnv(
        seed=args.seed,
        phase=args.phase,
        horizon_steps=args.horizon_steps,
    )
    config = PPOConfig(
        rollout_length=args.rollout_length,
        minibatch_size=args.minibatch_size,
        epochs_per_update=args.epochs_per_update,
        total_updates=args.total_updates,
        learning_rate=args.learning_rate,
    )
    trainer = PPOTrainer(env=env, config=config)
    logger.info(f"Starting PPO training: phase={args.phase} updates={args.total_updates}")
    trainer.train()
    if args.checkpoint:
        trainer.save_checkpoint(args.checkpoint)
        logger.info(f"Checkpoint saved to {args.checkpoint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
