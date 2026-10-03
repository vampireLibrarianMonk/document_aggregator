# Nav Bus ICD Review Board Notes

Date: 2026-04-22
Author: Interface Control Board

## Message Register (authoritative)

- POSITION: 32-bit word, 20 Hz, degrees. Owner: Nav Software.
- VELOCITY: 16-bit word, 10 Hz, knots. Owner: Nav Software.
- STATUS: 8-bit word, 1 Hz, discrete. Owner: Systems.

## Open Items

- The signaling rate is 100 kbps. The draft's "50 kbps" is wrong.
- The POSITION update rate must read 20 Hz, not 25 Hz.
- The STATUS message word length is 8 bits.

## Figures

- signal_timing_detail.png: Detailed timing of the POSITION message word.
