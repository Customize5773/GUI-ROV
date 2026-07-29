/*
 * ============================================================
 * HYDROSHIPS ROV
 * Grip Controller
 * ============================================================
 */

import { ManipulatorState } from "./state.js";
import { ManipulatorProtocol } from "./protocol.js";
import {
    GripState,
    ManipulatorAction,
    GripDirection
} from "./constants.js";

export class GripController {

    /**
     * Mulai membuka gripper
     * Dipanggil saat tombol OPEN ditekan (pointerdown)
     */
    static startOpen() {

        // Update state
        ManipulatorState.grip.state = GripState.OPENING;
        ManipulatorState.grip.busy = true;

        // Buat packet
        return ManipulatorProtocol.create(
            "grip",
            ManipulatorAction.START,
            GripDirection.OPEN
        );

    }

    /**
     * Mulai menutup gripper
     * Dipanggil saat tombol CLOSE ditekan (pointerdown)
     */
    static startClose() {

        // Update state
        ManipulatorState.grip.state = GripState.CLOSING;
        ManipulatorState.grip.busy = true;

        // Buat packet
        return ManipulatorProtocol.create(
            "grip",
            ManipulatorAction.START,
            GripDirection.CLOSE
        );

    }

    /**
     * Hentikan gerakan gripper
     * Dipanggil saat tombol dilepas (pointerup)
     */
    static stop() {

        // Update state
        ManipulatorState.grip.state = GripState.STOP;
        ManipulatorState.grip.busy = false;

        // Buat packet
        return ManipulatorProtocol.create(
            "grip",
            ManipulatorAction.STOP
        );

    }

    /**
     * Mengembalikan state gripper saat ini
     */
    static getState() {
        return ManipulatorState.grip;
    }

    /**
     * Mengecek apakah gripper sedang bergerak
     */
    static isBusy() {
        return ManipulatorState.grip.busy;
    }

}