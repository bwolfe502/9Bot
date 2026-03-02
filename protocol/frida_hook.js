/**
 * frida_hook.js -- Frida Gadget hook script for Kingdom Guard protocol capture
 *
 * Hooks into the game's IL2CPP runtime (libil2cpp.so) to intercept network
 * messages at the TFW.NetMsgData layer:
 *
 *   1. NetMsgData.FromByte  (inbound — server → client)
 *   2. NetMsgData.MakeByte  (outbound — client → server)
 *
 * The game uses TLS for transport encryption, so we intercept above the
 * TLS layer at the message framing level. Each message has a 4-byte
 * msg_id (BKDR hash of bare class name) followed by protobuf payload.
 *
 * Data is sent to the Python host via Frida's send() API.
 * The Python side receives (payload_obj, binary_data) tuples.
 *
 * Hook targets are resolved dynamically at runtime via IL2CPP's metadata
 * API (il2cpp_class_from_name + il2cpp_class_get_methods). This makes the
 * script independent of game version and LIEF patching delta.
 *
 * NetMsgData class layout (IL2CPP ARM64):
 *   +0x00  klass ptr       (8 bytes)
 *   +0x08  monitor ptr     (8 bytes)
 *   +0x10  MsgID           (uint32)
 *   +0x18  Buffer          (PooledMemoryStream ptr)
 *
 * Method signatures:
 *   static NetMsgData FromByte(byte[] bytes, int offset, int len)
 *     - args[0]=byte[], args[1]=offset, args[2]=len, args[3]=MethodInfo*
 *     - Wire frame at bytes[offset]: [4-byte msg_id][protobuf payload]
 *     - Returns NetMsgData with MsgID at +0x10
 *
 *   static void MakeByte(Stream output, uint msgID, byte[] rawData, int offset, int len)
 *     - args[0]=Stream, args[1]=msgID, args[2]=byte[], args[3]=offset,
 *       args[4]=len, args[5]=MethodInfo*
 */

"use strict";

// ------------------------------------------------------------------ //
//  Constants
// ------------------------------------------------------------------ //

var MODULE_NAME = "libil2cpp.so";

// IL2CPP class metadata for dynamic resolution
var NETMSGDATA_NAMESPACE = "TFW";
var NETMSGDATA_CLASS = "NetMsgData";

// Safety limits
var MAX_MESSAGE_SIZE = 1 * 1024 * 1024;  // 1 MB
var RATE_LIMIT_WINDOW_MS = 1000;
var RATE_LIMIT_MAX = 200;  // higher limit since we're at message level

// ------------------------------------------------------------------ //
//  Counters and rate-limiting state
// ------------------------------------------------------------------ //

var stats = {
    totalRecv: 0,
    totalSend: 0,
    totalErrors: 0,
    totalSkippedSize: 0,
    totalSkippedRate: 0,
    startTime: Date.now()
};

var rateLimiter = {
    windowStart: Date.now(),
    count: 0,
    warned: false,

    allow: function () {
        var now = Date.now();
        if (now - this.windowStart > RATE_LIMIT_WINDOW_MS) {
            this.windowStart = now;
            this.count = 0;
            this.warned = false;
        }
        this.count++;
        if (this.count <= RATE_LIMIT_MAX) {
            return true;
        }
        if (!this.warned) {
            console.log("[frida_hook] WARN: rate limit exceeded (" +
                        this.count + " msgs in " + RATE_LIMIT_WINDOW_MS +
                        "ms window), sampling 1-in-10");
            this.warned = true;
        }
        stats.totalSkippedRate++;
        return (this.count % 10) === 0;
    }
};

// Resolved addresses (set during installHooks)
var resolvedFromByte = null;
var resolvedMakeByte = null;

// ------------------------------------------------------------------ //
//  IL2CPP helper functions
// ------------------------------------------------------------------ //

/**
 * Read a slice of an Il2CppArray (byte[]) starting at offset for len bytes.
 *
 * IL2CPP byte[] layout (ARM64):
 *   +0x00  klass ptr       (8 bytes)
 *   +0x08  monitor ptr     (8 bytes)
 *   +0x10  bounds ptr      (8 bytes, NULL for 1D arrays)
 *   +0x18  max_length      (uint32)
 *   +0x20  vector[0]       (element data starts here)
 *
 * @param {NativePointer} ptr - Pointer to the Il2CppArray.
 * @param {number} offset - Start offset within the array data.
 * @param {number} len - Number of bytes to read.
 * @returns {ArrayBuffer|null}
 */
function readIl2CppByteArraySlice(ptr, offset, len) {
    if (ptr === null || ptr === undefined || ptr.isNull()) {
        return null;
    }

    try {
        var arrLen = ptr.add(0x18).readU32();

        if (offset < 0 || len <= 0 || (offset + len) > arrLen) {
            console.log("[frida_hook] WARN: slice out of bounds: " +
                        "offset=" + offset + " len=" + len +
                        " arrLen=" + arrLen);
            return null;
        }

        if (len > MAX_MESSAGE_SIZE) {
            stats.totalSkippedSize++;
            return null;
        }

        var dataPtr = ptr.add(0x20).add(offset);
        return dataPtr.readByteArray(len);
    } catch (e) {
        console.log("[frida_hook] ERROR in readIl2CppByteArraySlice: " + e.message);
        stats.totalErrors++;
        return null;
    }
}

/**
 * Poll for a module to be loaded, then invoke the callback.
 */
function waitForModule(name, cb) {
    var attempts = 0;
    var maxAttempts = 120;  // 60 seconds at 500ms intervals
    var timer = setInterval(function () {
        attempts++;
        var mod = Process.findModuleByName(name);
        if (mod !== null) {
            clearInterval(timer);
            cb(mod);
        } else if (attempts >= maxAttempts) {
            clearInterval(timer);
            console.log("[frida_hook] ERROR: module '" + name +
                        "' not found after " + (maxAttempts * 500 / 1000) + "s");
        }
    }, 500);
}

// ------------------------------------------------------------------ //
//  Dynamic method resolution via IL2CPP runtime API
// ------------------------------------------------------------------ //

/**
 * Resolve NetMsgData.FromByte and NetMsgData.MakeByte addresses at runtime.
 *
 * Uses IL2CPP exported functions to walk the metadata:
 *   1. il2cpp_domain_get() → domain
 *   2. il2cpp_domain_get_assemblies() → assembly list
 *   3. il2cpp_assembly_get_image() → image per assembly
 *   4. il2cpp_class_from_name(image, namespace, class) → class
 *   5. il2cpp_class_get_methods(class, iter) → method list
 *   6. il2cpp_method_get_name(method) → name
 *   7. MethodInfo[0] → native function pointer
 *
 * @param {Module} mod - The libil2cpp.so module object.
 * @returns {{fromByte: NativePointer|null, makeByte: NativePointer|null}}
 */
function resolveNetMsgData(mod) {
    function ex(name) {
        var addr = mod.findExportByName(name);
        if (!addr) {
            console.log("[frida_hook] ERROR: IL2CPP export not found: " + name);
        }
        return addr;
    }

    // Wrap IL2CPP API functions
    var getDomain = new NativeFunction(ex("il2cpp_domain_get"), "pointer", []);
    var getAssemblies = new NativeFunction(
        ex("il2cpp_domain_get_assemblies"), "pointer", ["pointer", "pointer"]);
    var getImage = new NativeFunction(
        ex("il2cpp_assembly_get_image"), "pointer", ["pointer"]);
    var classFromName = new NativeFunction(
        ex("il2cpp_class_from_name"), "pointer", ["pointer", "pointer", "pointer"]);
    var getMethods = new NativeFunction(
        ex("il2cpp_class_get_methods"), "pointer", ["pointer", "pointer"]);
    var getMethodName = new NativeFunction(
        ex("il2cpp_method_get_name"), "pointer", ["pointer"]);

    // Step 1: Get domain and assemblies
    var domain = getDomain();
    var sizeOut = Memory.alloc(8);
    var assemblies = getAssemblies(domain, sizeOut);
    var asmCount = sizeOut.readU32();
    console.log("[frida_hook] IL2CPP assemblies: " + asmCount);

    // Step 2: Find TFW.NetMsgData class
    var nsPtr = Memory.allocUtf8String(NETMSGDATA_NAMESPACE);
    var clsPtr = Memory.allocUtf8String(NETMSGDATA_CLASS);
    var foundClass = null;

    for (var a = 0; a < asmCount; a++) {
        var assembly = assemblies.add(a * Process.pointerSize).readPointer();
        var image = getImage(assembly);
        var klass = classFromName(image, nsPtr, clsPtr);
        if (!klass.isNull()) {
            foundClass = klass;
            break;
        }
    }

    if (!foundClass) {
        // Fallback: try empty namespace (in case the namespace changed)
        var emptyNs = Memory.allocUtf8String("");
        for (var a2 = 0; a2 < asmCount; a2++) {
            var assembly2 = assemblies.add(a2 * Process.pointerSize).readPointer();
            var image2 = getImage(assembly2);
            var klass2 = classFromName(image2, emptyNs, clsPtr);
            if (!klass2.isNull()) {
                foundClass = klass2;
                console.log("[frida_hook] Found NetMsgData with empty namespace (fallback)");
                break;
            }
        }
    }

    if (!foundClass) {
        console.log("[frida_hook] ERROR: " + NETMSGDATA_NAMESPACE + "." +
                    NETMSGDATA_CLASS + " class not found in IL2CPP metadata!");
        return { fromByte: null, makeByte: null };
    }

    console.log("[frida_hook] Found " + NETMSGDATA_NAMESPACE + "." +
                NETMSGDATA_CLASS + " at " + foundClass);

    // Step 3: Enumerate methods to find FromByte and MakeByte
    var iter = Memory.alloc(Process.pointerSize);
    iter.writePointer(ptr(0));

    var addrFromByte = null;
    var addrMakeByte = null;
    var mi;

    while (!(mi = getMethods(foundClass, iter)).isNull()) {
        var namePtr = getMethodName(mi);
        if (namePtr.isNull()) continue;
        var name = namePtr.readUtf8String();

        // MethodInfo: first field is the native function pointer
        var funcPtr = mi.readPointer();

        if (name === "FromByte") {
            addrFromByte = funcPtr;
        } else if (name === "MakeByte") {
            addrMakeByte = funcPtr;
        }
    }

    return { fromByte: addrFromByte, makeByte: addrMakeByte };
}

// ------------------------------------------------------------------ //
//  Hook installation
// ------------------------------------------------------------------ //

function installHooks(mod) {
    var base = mod.base;
    console.log("[frida_hook] " + MODULE_NAME + " base address: " + base);
    console.log("[frida_hook] Module size: " + mod.size + " bytes");

    // Dynamically resolve method addresses
    console.log("[frida_hook] Resolving method addresses via IL2CPP runtime API...");
    var resolved = resolveNetMsgData(mod);

    var addrFromByte = resolved.fromByte;
    var addrMakeByte = resolved.makeByte;

    if (!addrFromByte) {
        console.log("[frida_hook] ERROR: NetMsgData.FromByte not found!");
        return;
    }
    if (!addrMakeByte) {
        console.log("[frida_hook] ERROR: NetMsgData.MakeByte not found!");
        return;
    }

    // Compute RVAs for logging
    var rvaFromByte = addrFromByte.sub(base);
    var rvaMakeByte = addrMakeByte.sub(base);

    console.log("[frida_hook] Hook addresses (dynamically resolved):");
    console.log("  NetMsgData.FromByte: " + addrFromByte +
                " (RVA 0x" + rvaFromByte.toString(16) + ")");
    console.log("  NetMsgData.MakeByte: " + addrMakeByte +
                " (RVA 0x" + rvaMakeByte.toString(16) + ")");

    // Store resolved addresses for status reporting
    resolvedFromByte = addrFromByte;
    resolvedMakeByte = addrMakeByte;

    // Verify the addresses are within the module
    var modEnd = base.add(mod.size);
    if (addrFromByte.compare(modEnd) >= 0 || addrMakeByte.compare(modEnd) >= 0) {
        console.log("[frida_hook] ERROR: hook addresses outside module bounds!");
        return;
    }

    // -------------------------------------------------------------- //
    //  Hook 1: NetMsgData.FromByte — inbound messages (server → client)
    // -------------------------------------------------------------- //
    //
    //  static NetMsgData FromByte(byte[] bytes, int offset, int len)
    //
    //  ARM64 IL2CPP static method calling convention:
    //    args[0] = byte[] bytes  (Il2CppArray*)
    //    args[1] = int offset
    //    args[2] = int len
    //    args[3] = MethodInfo* (hidden, ignored)
    //
    //  The wire frame at bytes[offset] is:
    //    [4 bytes: msg_id (uint32 big-endian)] [protobuf payload]
    //
    //  Returns: NetMsgData* with MsgID at +0x10

    Interceptor.attach(addrFromByte, {
        onEnter: function (args) {
            // Save args for onLeave
            this.bytesPtr = args[0];
            this.offset = args[1].toInt32();
            this.len = args[2].toInt32();
        },
        onLeave: function (retval) {
            try {
                if (retval.isNull()) {
                    return;
                }

                if (!rateLimiter.allow()) {
                    return;
                }

                // Read msg_id from the returned NetMsgData object
                var msgId = retval.add(0x10).readU32();

                // Extract protobuf payload: skip 4-byte msg_id header
                var payloadOffset = this.offset + 4;
                var payloadLen = this.len - 4;

                if (payloadLen <= 0) {
                    // No payload (just a msg_id header)
                    stats.totalRecv++;
                    send({
                        type: "recv",
                        msgId: msgId,
                        len: 0
                    });
                    return;
                }

                var payload = readIl2CppByteArraySlice(
                    this.bytesPtr, payloadOffset, payloadLen
                );

                stats.totalRecv++;

                send({
                    type: "recv",
                    msgId: msgId,
                    len: payloadLen
                }, payload);

            } catch (e) {
                console.log("[frida_hook] ERROR in FromByte hook: " + e.message);
                stats.totalErrors++;
            }
        }
    });
    console.log("[frida_hook] Hooked NetMsgData.FromByte at " + addrFromByte);

    // -------------------------------------------------------------- //
    //  Hook 2: NetMsgData.MakeByte — outbound messages (client → server)
    // -------------------------------------------------------------- //
    //
    //  static void MakeByte(Stream output, uint msgID, byte[] rawData,
    //                        int offset, int len)
    //
    //  ARM64 IL2CPP static method calling convention:
    //    args[0] = Stream output
    //    args[1] = uint msgID
    //    args[2] = byte[] rawData (Il2CppArray*)
    //    args[3] = int offset
    //    args[4] = int len
    //    args[5] = MethodInfo* (hidden, ignored)

    Interceptor.attach(addrMakeByte, {
        onEnter: function (args) {
            try {
                if (!rateLimiter.allow()) {
                    return;
                }

                var msgId = args[1].toUInt32();
                var rawData = args[2];
                var offset = args[3].toInt32();
                var len = args[4].toInt32();

                var payload = null;
                if (len > 0 && !rawData.isNull()) {
                    payload = readIl2CppByteArraySlice(rawData, offset, len);
                }

                stats.totalSend++;

                send({
                    type: "send",
                    msgId: msgId,
                    len: len
                }, payload);

            } catch (e) {
                console.log("[frida_hook] ERROR in MakeByte hook: " + e.message);
                stats.totalErrors++;
            }
        }
    });
    console.log("[frida_hook] Hooked NetMsgData.MakeByte at " + addrMakeByte);

    // -------------------------------------------------------------- //
    //  All hooks installed
    // -------------------------------------------------------------- //

    console.log("[frida_hook] All 2 hooks installed successfully");
    console.log("[frida_hook] Waiting for protocol traffic...");
}

// ------------------------------------------------------------------ //
//  RPC exports (callable from Python host)
// ------------------------------------------------------------------ //

rpc.exports = {
    /**
     * Return current hook status and statistics.
     */
    status: function () {
        var uptimeMs = Date.now() - stats.startTime;
        var uptimeSec = Math.floor(uptimeMs / 1000);
        var mod = Process.findModuleByName(MODULE_NAME);

        return {
            hooked: mod !== null,
            moduleBase: mod ? mod.base.toString() : null,
            fromByteAddr: resolvedFromByte ? resolvedFromByte.toString() : null,
            makeByteAddr: resolvedMakeByte ? resolvedMakeByte.toString() : null,
            uptimeSeconds: uptimeSec,
            counters: {
                recv: stats.totalRecv,
                send: stats.totalSend,
                errors: stats.totalErrors,
                skippedSize: stats.totalSkippedSize,
                skippedRate: stats.totalSkippedRate
            },
            rateLimit: {
                windowMs: RATE_LIMIT_WINDOW_MS,
                maxPerWindow: RATE_LIMIT_MAX,
                currentWindowCount: rateLimiter.count
            }
        };
    }
};

// ------------------------------------------------------------------ //
//  Entry point
// ------------------------------------------------------------------ //

console.log("[frida_hook] Kingdom Guard protocol capture script loaded");
console.log("[frida_hook] Mode: NetMsgData (message layer, above TLS)");
console.log("[frida_hook] Method resolution: dynamic (IL2CPP runtime API)");
console.log("[frida_hook] Waiting for " + MODULE_NAME + " to load...");

waitForModule(MODULE_NAME, function (mod) {
    console.log("[frida_hook] " + MODULE_NAME + " found, installing hooks...");
    installHooks(mod);
});
