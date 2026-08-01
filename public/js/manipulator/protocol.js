/*
 * ============================================================
 * HYDROSHIPS ROV
 * Manipulator Protocol
 * ============================================================
 */
import { ProtocolVersion } from "./constants.js";

export class ManipulatorProtocol {

    /**
     * Membuat paket command manipulator
     *
     * @param {string} device      grip | rotate | cutter | dll
     * @param {string} action      start | stop
     * @param {string|null} direction open | close | left | right
     * @param {Object} data        data tambahan
     */
    static create(device, action, direction = null, data = {}) {

        let name = "";

        let value = "";

        if (device === "grip") {

            name = "gripper";

            if (action === "start")
                value = direction;

            else
                value = "stop";

        }

        else if (device === "rotate") {

            name = "gripper_rotate";

            if (action === "start")
                value = direction;

            else
                value = "stop";

        }

        return {

            version: ProtocolVersion,

            timestamp: Date.now(),

            type: "cmd",

            name,

            value,

            data

        };

    }

}