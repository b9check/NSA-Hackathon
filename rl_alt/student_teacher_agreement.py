"""Student-teacher action agreement diagnostic.

Measures how often the trained PPO student picks the same action as the
scripted teacher on matched states (states drawn from the teacher's own
rollout distribution), broken down by model, regime, and action slot.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np

# Make sibling imports work whether run as module or script.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl_alt.rl_env import OverwatchEnv
from rl_alt.baseline import predict_baseline_action

SLOT_NAMES = [
    "recon_move",
    "recon_pol",
    "strike_move",
    "strike_pol",
    "sam_move",
    "sam_pol",
    "radar",
]

REGIMES: dict[str, list[float]] = {
    "offensive": [1.0, -0.2, 0.3, -0.8],
    "defensive": [0.5, -1.0, 0.2, 0.0],
    "recon-heavy": [0.3, -0.3, 1.0, -0.2],
}

MODELS: list[tuple[str, str, float]] = [
    ("bc_200k_scale1", "rl/overwatch_bc_200k.zip", 1.0),
    ("bc_scale10_teacher_100k", "rl/overwatch_bc_scale10_teacher_100k.zip", 10.0),
]

SEEDS = list(range(10))
MAX_STEPS = 20


def load_model(zip_path: Path):
    from stable_baselines3 import PPO

    return PPO.load(str(zip_path), device="cpu")


def collect(model, scale: float, regime: str, weights: list[float]):
    """Return list of (teacher_action, student_action) tuples."""
    env = OverwatchEnv(weight_obs_scale=scale)
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    for seed in SEEDS:
        env.set_reward_weights(weights)
        obs, _ = env.reset(seed=seed)
        for _ in range(MAX_STEPS):
            teacher = np.asarray(predict_baseline_action(env), dtype=np.int64)
            student, _ = model.predict(obs, deterministic=True)
            student = np.asarray(student, dtype=np.int64)
            pairs.append((teacher.copy(), student.copy()))
            obs, _, term, trunc, _ = env.step(teacher)
            if term or trunc:
                break
    return pairs


def per_slot_agreement(pairs):
    if not pairs:
        return [float("nan")] * 7, float("nan")
    teach = np.stack([p[0] for p in pairs])
    stud = np.stack([p[1] for p in pairs])
    slot = (teach == stud).mean(axis=0)
    full = float((teach == stud).all(axis=1).mean())
    return slot.tolist(), full


def disagreement_dist(pairs, slot_idx: int):
    counter: Counter = Counter()
    total = 0
    for t, s in pairs:
        if t[slot_idx] != s[slot_idx]:
            counter[int(s[slot_idx])] += 1
            total += 1
    if total == 0:
        return {}, 0
    return {k: v / total for k, v in counter.most_common()}, total


def fmt_pct(x: float) -> str:
    if not np.isfinite(x):
        return "  n/a"
    return f"{100 * x:5.1f}%"


def render_full_table(results) -> str:
    regimes = list(REGIMES.keys())
    header = "| Model | " + " | ".join(regimes) + " |"
    sep = "|" + "---|" * (len(regimes) + 1)
    lines = [header, sep]
    for model_name, regime_data in results.items():
        row = [model_name]
        for r in regimes:
            row.append(fmt_pct(regime_data[r]["full"]))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_slot_table(model_name, regime_data) -> str:
    lines = [f"### {model_name} per-slot agreement"]
    header = "| Regime | " + " | ".join(SLOT_NAMES) + " |"
    sep = "|" + "---|" * (len(SLOT_NAMES) + 1)
    lines += [header, sep]
    for r in REGIMES:
        slots = regime_data[r]["slots"]
        row = [r] + [fmt_pct(v) for v in slots]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main() -> None:
    results: dict = {}
    load_errors: dict[str, str] = {}

    for model_name, rel_path, scale in MODELS:
        zip_path = ROOT / rel_path
        if not zip_path.exists():
            load_errors[model_name] = f"missing file: {zip_path}"
            print(f"[skip] {model_name}: {load_errors[model_name]}")
            continue
        try:
            model = load_model(zip_path)
        except Exception as e:  # noqa: BLE001
            load_errors[model_name] = f"{type(e).__name__}: {e}"
            print(f"[skip] {model_name}: {load_errors[model_name]}")
            continue

        regime_data: dict = {}
        for regime, weights in REGIMES.items():
            print(f"[run] {model_name} / {regime} (scale={scale})")
            pairs = collect(model, scale, regime, weights)
            slots, full = per_slot_agreement(pairs)
            worst_idx = int(np.argmin(slots)) if pairs else 0
            wrong, n_disagree = disagreement_dist(pairs, worst_idx)
            regime_data[regime] = {
                "pairs": pairs,
                "slots": slots,
                "full": full,
                "worst_idx": worst_idx,
                "wrong": wrong,
                "n_disagree": n_disagree,
                "n": len(pairs),
            }
        results[model_name] = regime_data

    # Compute, across regimes per model, which slot has overall lowest agreement.
    worst_summary: dict = {}
    for model_name, regime_data in results.items():
        all_pairs = []
        for r in REGIMES:
            all_pairs.extend(regime_data[r]["pairs"])
        slots, _ = per_slot_agreement(all_pairs)
        worst_idx = int(np.argmin(slots))
        wrong, n_disagree = disagreement_dist(all_pairs, worst_idx)
        worst_summary[model_name] = {
            "slot_idx": worst_idx,
            "slot_name": SLOT_NAMES[worst_idx],
            "agreement": slots[worst_idx],
            "wrong": wrong,
            "n_disagree": n_disagree,
            "n_total": len(all_pairs),
        }

    out = []
    out.append("## Student-Teacher Action Agreement\n")
    out.append("### Full-action agreement (all 7 slots match)\n")
    out.append(render_full_table(results) + "\n")
    for model_name in results:
        out.append("")
        out.append(render_slot_table(model_name, results[model_name]) + "\n")

    out.append("### Worst-slot disagreement breakdown (across regimes)\n")
    for model_name, ws in worst_summary.items():
        line = (
            f"- **{model_name}** worst slot: `{ws['slot_name']}` "
            f"agreement={fmt_pct(ws['agreement'])} "
            f"(disagree on {ws['n_disagree']}/{ws['n_total']}). "
            f"Student picks: " + ", ".join(f"{k}: {fmt_pct(v)}" for k, v in ws["wrong"].items())
        )
        out.append(line)
    out.append("")

    if load_errors:
        out.append("### Load errors")
        for k, v in load_errors.items():
            out.append(f"- {k}: {v}")
        out.append("")

    # Interpretation
    fulls = []
    for model_name in results:
        for r in REGIMES:
            fulls.append(results[model_name][r]["full"])
    mean_full = float(np.mean(fulls)) if fulls else float("nan")
    if mean_full > 0.85:
        bucket = ">85% -> BC fidelity is fine; gap is a PPO-finetune issue."
    elif mean_full >= 0.60:
        bucket = "60-85% -> capacity-bounded; BC partially imitates the teacher but loses detail."
    else:
        bucket = "<60% -> BC is collapsing; student is not imitating the teacher."

    scale_note = ""
    if "bc_200k_scale1" in results and "bc_scale10_teacher_100k" in results:
        s1 = np.mean([results["bc_200k_scale1"][r]["full"] for r in REGIMES])
        s10 = np.mean([results["bc_scale10_teacher_100k"][r]["full"] for r in REGIMES])
        if s10 < s1 - 0.05:
            scale_note = (
                f"Scale-10 ({100*s10:.1f}%) shows *worse* full-action agreement than "
                f"scale-1 ({100*s1:.1f}%) -- a smoking gun for over-amplification of the "
                f"weight obs hurting BC fidelity."
            )
        elif s10 > s1 + 0.05:
            scale_note = (
                f"Scale-10 ({100*s10:.1f}%) is *better* than scale-1 ({100*s1:.1f}%); "
                f"the 10x weight scaling is helping the student attend to regime."
            )
        else:
            scale_note = (
                f"Scale-10 ({100*s10:.1f}%) and scale-1 ({100*s1:.1f}%) are comparable; "
                f"the scale knob does not materially shift BC fidelity."
            )

    out.append("### Interpretation\n")
    out.append(
        f"Mean full-action agreement across loaded models/regimes is {fmt_pct(mean_full)}. "
        f"This fits the bucket: **{bucket}** "
        + scale_note
        + " The worst-slot breakdowns above show *which* head is dropping fidelity, "
        "i.e. where targeted BC re-weighting or longer training would help most."
    )
    out.append("")

    if load_errors:
        out.append(
            "Caveat: "
            + "; ".join(f"{k} could not be loaded ({v})" for k, v in load_errors.items())
            + "."
        )

    findings_path = HERE / "findings_student_teacher.md"
    findings_path.write_text("\n".join(out), encoding="utf-8")

    # Stdout
    print()
    print("\n".join(out))
    print(f"\n[wrote] {findings_path}")


if __name__ == "__main__":
    main()
