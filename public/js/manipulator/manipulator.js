/*
 * ============================================================
 * HYDROSHIPS ROV
 * Manipulator Manager
 * ============================================================
 */

import { GripController } from "./grip.js";
import { RotateController } from "./rotate.js";

export class Manipulator {

    /* =======================================================
     * Grip
     * ======================================================= */

    static openGrip() {
        return GripController.startOpen();
    }

    static closeGrip() {
        return GripController.startClose();
    }

    static stopGrip() {
        return GripController.stop();
    }

    /* =======================================================
     * Rotate
     * ======================================================= */

    static rotateLeft() {
        return RotateController.startLeft();
    }

    static rotateRight() {
        return RotateController.startRight();
    }

    static stopRotate() {
        return RotateController.stop();
    }

    /* =======================================================
    * General
    * ======================================================= */
   
    static stopAll() {
        return [
            GripController.stop(),
            RotateController.stop()
        ];
    }

    static isBusy() {
        return (
            GripController.isBusy() ||
            RotateController.isBusy()
        );
    }

}