/* Exercise the actual UDP telemetry handler without binding ports or starting
   control_main/joystick. Cached detections must retain their Pi receipt/age. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../server.js'), 'utf8');
const start = source.indexOf('udp.on("message", (buf, rinfo) => {');
const end = source.indexOf('udp.on("error",', start);
assert.ok(start >= 0 && end > start);
let handler;
const packets = [];
const broadcasts = [];
vm.runInNewContext(source.slice(start, end), {
  udp: { on: (_, callback) => { handler = callback; } },
  controlModeUdp: { send: (buf, port, host, callback) => {
    assert.equal(port, 14603);
    assert.equal(host, '127.0.0.1');
    packets.push(JSON.parse(buf.toString()));
    callback(null);
  } },
  broadcast: packet => broadcasts.push(packet),
  CONTROL_HOOK_PORT: 14603, DEBUG: false, Buffer, Date,
});
const decoded = { center: [320, 240], area: 20000, frame_w: 640, frame_h: 480, data: 'QR1' };
const region = { center: [321, 240], area: 40000, frame_w: 640, frame_h: 480 };
const data = {
  armed: true, depth: 0.93, qr_vision: decoded, qr_region: region,
  vision_receipts: {
    qr_vision: { received: 100, age: 0.4 },
    qr_region: { received: 100.3, age: 0.1 },
  },
};
handler(Buffer.from(JSON.stringify(data)), {});
assert.deepEqual(packets, [
  { type: 'qr_vision', value: decoded, received: 100, age: 0.4 },
  { type: 'qr_region', value: region, received: 100.3, age: 0.1 },
  { type: 'vehicle_state', value: { depth: 0.93, armed: true } },
]);
packets.length = 0;
data.vision_receipts.qr_vision.age = 2.5;
data.vision_receipts.qr_region.age = 2.2;
handler(Buffer.from(JSON.stringify(data)), {});
assert.equal(packets[0].received, 100);
assert.equal(packets[0].age, 2.5); // never stamped as a new camera observation
packets.length = 0;
handler(Buffer.from(JSON.stringify({ armed: false, depth: 0, qr_vision: null, qr_region: null })), {});
assert.deepEqual(packets, [{ type: 'vehicle_state', value: { depth: 0, armed: false } }]);
packets.length = 0;
handler(Buffer.from(JSON.stringify({ type: 'param_ack', name: 'example' })), {});
assert.equal(packets.length, 0);
assert.equal(broadcasts.at(-1).type, 'param_ack');
console.log('control telemetry: receipt/age, depth/armed, null vision, envelope PASS');
