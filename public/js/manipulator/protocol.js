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

        return {
            version: ProtocolVersion,
            timestamp: Date.now(),

            type: "cmd",
            name: "manipulator",

            device,
            action,
            direction,

            data
        };

    }

}