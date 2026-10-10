"""BioSemi channel naming (A1..H32, EX1..EX8) and mapping to 10-20 names."""

from __future__ import annotations

PRESETS = ("biosemi16", "biosemi32", "biosemi64", "biosemi128", "biosemi160", "biosemi256")


def biosemi_labels(n_electrodes: int, n_ex: int = 0) -> list[str]:
    """Raw ActiveTwo labels: A1..A32, B1..B32, ... then EX1..EXn."""
    labels = [f"{chr(ord('A') + i // 32)}{i % 32 + 1}" for i in range(n_electrodes)]
    return labels + [f"EX{i + 1}" for i in range(n_ex)]


def biosemi_to_1020(preset: str) -> dict[str, str]:
    """Map A1.. labels to 10-20 names of an MNE BioSemi montage.

    MNE's ``biosemiNN`` montages list channels in the BioSemi cap order
    (A1, A2, ..., B1, ...), which is what this relies on. Verify against your
    cap's layout sheet before relying on it for topographic analyses.
    """
    import mne

    if preset not in PRESETS:
        raise ValueError(f"Unknown montage preset {preset!r}; choose from {PRESETS}")
    names = mne.channels.make_standard_montage(preset).ch_names
    return dict(zip(biosemi_labels(len(names)), names))
