---
description: "ROV Python conventions for control, MAVLink, telemetry, autonomy, mission FSM, vision, gripper, and hardware-safe tests."
applyTo: "**/*.py"
---

- Follow `CONTROL-MAPPING.md` for axis signs and ranges when changing mixer or input code.
- Reuse the existing link/MAVLink abstractions and tests; do not invent a second hardware path.
- Preserve arm/disarm, stop, mode, depth, gripper, thruster, timeout, and failsafe behavior unless explicitly requested.
- Keep telemetry and control code compatible with missing or delayed data.
- Prefer simple, deterministic code for transforms, PID, filters, validation, and state transitions.
- Update the nearest focused test when behavior changes, then run it with `python -m unittest` or the repository's existing command.
- Keep public APIs, configuration names, command ranges, and logging formats stable.