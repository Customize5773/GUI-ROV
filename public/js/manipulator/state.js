/*
 * ============================================================
 * HYDROSHIPS ROV
 * Manipulator State
 * ============================================================
 */

import {
    GripState,
    RotateState,
    HardwareType
} from "./constants.js";

export const ManipulatorState = {

    /*
     * ============================================================
     * Grip State
     * ============================================================
     */
    grip: {

        // OPENING | CLOSING | STOP
        state: GripState.STOP,

        // Future:
        // Servo Position / Encoder Position
        position: null,

        // Menandakan grip sedang bergerak
        busy: false

    },

    /*
     * ============================================================
     * Rotate State
     * ============================================================
     */
    rotate: {

        // LEFT | RIGHT | STOP
        state: RotateState.STOP,

        // Future:
        // Servo Angle / Encoder
        angle: null,

        // Menandakan rotate sedang bergerak
        busy: false

    },

    /*
     * ============================================================
     * Hardware Configuration
     * ============================================================
     */
    hardware: {
        grip: HardwareType.SERVO,
        rotate: HardwareType.SERVO

    },

    /*
     * ============================================================
     * Safety
     * ============================================================
     */
    safety: {

        enabled: true,

        emergencyStop: false,

        error: null

    }

};