/*
 * ============================================================
 * HYDROSHIPS ROV
 * Rotate Controller
 * ============================================================
 */

import { ManipulatorState } from "./state.js";
import { ManipulatorProtocol } from "./protocol.js";
import {
    RotateState,
    ManipulatorAction,
    RotateDirection
} from "./constants.js";

export class RotateController {

    /**
     * Mulai rotasi ke kiri
     */
    static startLeft() {

        ManipulatorState.rotate.state = RotateState.LEFT;
        ManipulatorState.rotate.busy = true;

        return ManipulatorProtocol.create(
            "rotate",
            ManipulatorAction.START,
            RotateDirection.LEFT
        );

    }

    /**
     * Mulai rotasi ke kanan
     */
    static startRight() {

        ManipulatorState.rotate.state = RotateState.RIGHT;
        ManipulatorState.rotate.busy = true;

        return ManipulatorProtocol.create(
            "rotate",
            ManipulatorAction.START,
            RotateDirection.RIGHT
        );

    }

    /**
     * Hentikan rotasi
     */
    static stop() {

        ManipulatorState.rotate.state = RotateState.STOP;
        ManipulatorState.rotate.busy = false;

        return ManipulatorProtocol.create(
            "rotate",
            ManipulatorAction.STOP
        );

    }

}