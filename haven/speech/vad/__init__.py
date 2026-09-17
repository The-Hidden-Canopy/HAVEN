"""Energy-based reference VAD, pure Python.

`EnergyVad` is the contract's reference implementation: root-mean-square
energy per 25 ms frame judged against a slowly adapting noise floor, with
hysteresis so speech does not flicker at its edges. It is deliberately
simple -- a sine tone counts as speech, silence or low noise does not --
but it is a genuinely usable VAD on clean audio and the shape every
serious VAD plugs into the same `Vad` protocol with. Fully deterministic;
no randomness anywhere.
"""

from .energy import EnergyVad

__all__ = ["EnergyVad"]
