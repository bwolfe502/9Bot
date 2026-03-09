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

// Injection support: substitute next matching outgoing message
// pendingInject = {triggerMsgId, replaceMsgId, payload: [bytes], once: bool}
var pendingInject = null;
var injectApplyCount = 0;

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
                var msgId = args[1].toUInt32();
                var rawData = args[2];
                var offset = args[3].toInt32();
                var len = args[4].toInt32();

                // ---- Injection: rewrite this call's args in-place ----
                if (pendingInject !== null) {
                    var inj = pendingInject;
                    if (inj.once) {
                        pendingInject = null;
                    }

                    var injPayload = inj.payload;
                    var injLen = injPayload.length;
                    var arrMaxLen = rawData.add(0x18).readU32();

                    if (injLen <= arrMaxLen) {
                        // Overwrite msgId arg
                        args[1] = ptr(inj.replaceMsgId >>> 0);

                        // Write injection payload into existing byte array at offset 0
                        var dataPtr = rawData.add(0x20);
                        for (var i = 0; i < injLen; i++) {
                            dataPtr.add(i).writeU8(injPayload[i]);
                        }

                        // Fix offset and len args
                        args[3] = ptr(0);
                        args[4] = ptr(injLen);

                        injectApplyCount++;
                        console.log("[frida_hook] INJECTED: replaced msgId=0x" +
                                    msgId.toString(16) + " with 0x" +
                                    inj.replaceMsgId.toString(16) +
                                    " (" + injLen + " bytes)");

                        // Report the injected message (not the original)
                        msgId = inj.replaceMsgId;
                        offset = 0;
                        len = injLen;
                    } else {
                        console.log("[frida_hook] INJECT SKIP: payload " + injLen +
                                    " > array capacity " + arrMaxLen);
                    }
                }

                if (!rateLimiter.allow()) {
                    return;
                }

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
    },

    /**
     * Queue a message substitution on the next outgoing MakeByte call.
     *
     * The next outgoing message will have its msgId and payload replaced
     * in-place, reusing the game's own Stream and byte array.
     *
     * @param {number} replaceMsgId - Wire msg_id for the injected message.
     * @param {number[]} payloadBytes - Protobuf payload as byte array.
     * @param {boolean} once - If true (default), clear after one use.
     * @returns {{ok: boolean, queued: boolean}}
     */
    injectSend: function (replaceMsgId, payloadBytes, once) {
        if (once === undefined) once = true;
        pendingInject = {
            replaceMsgId: replaceMsgId,
            payload: payloadBytes,
            once: once
        };
        console.log("[frida_hook] Queued inject: msgId=0x" +
                    replaceMsgId.toString(16) + " (" +
                    payloadBytes.length + " bytes), once=" + once);
        return { ok: true, queued: true };
    },

    /**
     * Clear any pending injection.
     */
    clearInject: function () {
        pendingInject = null;
        return { ok: true };
    },

    /**
     * Get detailed method signatures for a specific class.
     * Returns method names with parameter counts and types.
     */
    getMethodSignatures: function (namespace, className) {
        var mod = Process.findModuleByName(MODULE_NAME);
        if (!mod) return { error: "module not found" };

        var getDomain = new NativeFunction(mod.findExportByName("il2cpp_domain_get"), "pointer", []);
        var getAssemblies = new NativeFunction(mod.findExportByName("il2cpp_domain_get_assemblies"), "pointer", ["pointer", "pointer"]);
        var getImage = new NativeFunction(mod.findExportByName("il2cpp_assembly_get_image"), "pointer", ["pointer"]);
        var classFromName = new NativeFunction(mod.findExportByName("il2cpp_class_from_name"), "pointer", ["pointer", "pointer", "pointer"]);
        var getMethods = new NativeFunction(mod.findExportByName("il2cpp_class_get_methods"), "pointer", ["pointer", "pointer"]);
        var getMethodName = new NativeFunction(mod.findExportByName("il2cpp_method_get_name"), "pointer", ["pointer"]);
        var getMethodParamCount = new NativeFunction(mod.findExportByName("il2cpp_method_get_param_count"), "uint32", ["pointer"]);
        var getMethodParamName = new NativeFunction(mod.findExportByName("il2cpp_method_get_param_name"), "pointer", ["pointer", "uint32"]);
        var getMethodParam = new NativeFunction(mod.findExportByName("il2cpp_method_get_param"), "pointer", ["pointer", "uint32"]);
        var getTypeName = new NativeFunction(mod.findExportByName("il2cpp_type_get_name"), "pointer", ["pointer"]);
        var getMethodReturnType = new NativeFunction(mod.findExportByName("il2cpp_method_get_return_type"), "pointer", ["pointer"]);

        var domain = getDomain();
        var sizeOut = Memory.alloc(8);
        var assemblies = getAssemblies(domain, sizeOut);
        var asmCount = sizeOut.readU32();

        var nsPtr = Memory.allocUtf8String(namespace);
        var clsPtr = Memory.allocUtf8String(className);
        var foundClass = null;

        for (var a = 0; a < asmCount; a++) {
            var assembly = assemblies.add(a * Process.pointerSize).readPointer();
            var image = getImage(assembly);
            var klass = classFromName(image, nsPtr, clsPtr);
            if (!klass.isNull()) { foundClass = klass; break; }
        }

        if (!foundClass) return { error: "Class not found: " + namespace + "." + className };

        var methods = [];
        var iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(ptr(0));
        var mi;
        while (!(mi = getMethods(foundClass, iter)).isNull()) {
            var mnP = getMethodName(mi);
            if (mnP.isNull()) continue;
            var name = mnP.readUtf8String();

            var paramCount = getMethodParamCount(mi);
            var params = [];
            for (var p = 0; p < paramCount; p++) {
                var pnP = getMethodParamName(mi, p);
                var pName = pnP.isNull() ? "?" : pnP.readUtf8String();
                var pType = getMethodParam(mi, p);
                var tName = "?";
                if (!pType.isNull()) {
                    var tnP = getTypeName(pType);
                    if (!tnP.isNull()) tName = tnP.readUtf8String();
                }
                params.push(tName + " " + pName);
            }

            var retType = getMethodReturnType(mi);
            var retName = "void";
            if (!retType.isNull()) {
                var rtP = getTypeName(retType);
                if (!rtP.isNull()) retName = rtP.readUtf8String();
            }

            var funcPtr = mi.readPointer();
            methods.push({
                name: name,
                returnType: retName,
                params: params,
                paramCount: paramCount,
                address: funcPtr.toString()
            });
        }

        return { className: namespace + "." + className, methods: methods };
    },

    /**
     * Inspect a class: parent chain, fields (including static), and field values.
     * Useful for finding singleton instances and understanding object layout.
     */
    getClassInfo: function (namespace, className) {
        var mod = Process.findModuleByName(MODULE_NAME);
        if (!mod) return { error: "module not found" };

        var getDomain = new NativeFunction(mod.findExportByName("il2cpp_domain_get"), "pointer", []);
        var getAssemblies = new NativeFunction(mod.findExportByName("il2cpp_domain_get_assemblies"), "pointer", ["pointer", "pointer"]);
        var getImage = new NativeFunction(mod.findExportByName("il2cpp_assembly_get_image"), "pointer", ["pointer"]);
        var classFromName = new NativeFunction(mod.findExportByName("il2cpp_class_from_name"), "pointer", ["pointer", "pointer", "pointer"]);
        var getParent = new NativeFunction(mod.findExportByName("il2cpp_class_get_parent"), "pointer", ["pointer"]);
        var getClassNameFn = new NativeFunction(mod.findExportByName("il2cpp_class_get_name"), "pointer", ["pointer"]);
        var getClassNamespace = new NativeFunction(mod.findExportByName("il2cpp_class_get_namespace"), "pointer", ["pointer"]);
        var getFields = new NativeFunction(mod.findExportByName("il2cpp_class_get_fields"), "pointer", ["pointer", "pointer"]);
        var getFieldName = new NativeFunction(mod.findExportByName("il2cpp_field_get_name"), "pointer", ["pointer"]);
        var getFieldType = new NativeFunction(mod.findExportByName("il2cpp_field_get_type"), "pointer", ["pointer"]);
        var getTypeName = new NativeFunction(mod.findExportByName("il2cpp_type_get_name"), "pointer", ["pointer"]);
        var getFieldOffset = new NativeFunction(mod.findExportByName("il2cpp_field_get_offset"), "int32", ["pointer"]);
        var fieldStaticGetValue = new NativeFunction(mod.findExportByName("il2cpp_field_static_get_value"), "void", ["pointer", "pointer"]);
        var typeGetAttrs = new NativeFunction(mod.findExportByName("il2cpp_type_get_attrs"), "uint32", ["pointer"]);

        var domain = getDomain();
        var sizeOut = Memory.alloc(8);
        var assemblies = getAssemblies(domain, sizeOut);
        var asmCount = sizeOut.readU32();

        var nsPtr = Memory.allocUtf8String(namespace);
        var clsPtr = Memory.allocUtf8String(className);
        var foundClass = null;

        for (var a = 0; a < asmCount; a++) {
            var assembly = assemblies.add(a * Process.pointerSize).readPointer();
            var image = getImage(assembly);
            var klass = classFromName(image, nsPtr, clsPtr);
            if (!klass.isNull()) { foundClass = klass; break; }
        }

        if (!foundClass) return { error: "Class not found: " + namespace + "." + className };

        // Walk parent chain
        var parents = [];
        var p = getParent(foundClass);
        while (!p.isNull()) {
            var pnP = getClassNameFn(p);
            var pnsP = getClassNamespace(p);
            var pName = pnP.isNull() ? "?" : pnP.readUtf8String();
            var pNs = pnsP.isNull() ? "" : pnsP.readUtf8String();
            parents.push(pNs ? pNs + "." + pName : pName);
            p = getParent(p);
        }

        // Get fields
        var fields = [];
        var iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(ptr(0));
        var fi;
        while (!(fi = getFields(foundClass, iter)).isNull()) {
            var fnP = getFieldName(fi);
            if (fnP.isNull()) continue;
            var fName = fnP.readUtf8String();

            var ft = getFieldType(fi);
            var fTypeName = "?";
            if (!ft.isNull()) {
                var tnP = getTypeName(ft);
                if (!tnP.isNull()) fTypeName = tnP.readUtf8String();
            }

            var offset = getFieldOffset(fi);
            // Check if static: FieldAttributes.Static = 0x10
            var attrs = ft.isNull() ? 0 : typeGetAttrs(ft);
            var isStatic = (attrs & 0x10) !== 0;

            var fieldInfo = {
                name: fName,
                type: fTypeName,
                offset: offset,
                isStatic: isStatic
            };

            // Try to read static pointer fields
            if (isStatic) {
                try {
                    var valBuf = Memory.alloc(8);
                    fieldStaticGetValue(fi, valBuf);
                    var ptrVal = valBuf.readPointer();
                    fieldInfo.staticValue = ptrVal.toString();
                    fieldInfo.isNull = ptrVal.isNull();
                } catch (e) {
                    fieldInfo.staticReadError = e.message;
                }
            }

            fields.push(fieldInfo);
        }

        return {
            className: namespace + "." + className,
            parents: parents,
            fields: fields
        };
    },

    /**
     * Move the game camera to target coordinates using MapCameraMgr.MoveCameraToTargetInstantly.
     * MapCameraMgr is a fully static class (all fields static, parent = System.Object),
     * so MoveCameraToTargetInstantly is called as a static method.
     *
     * @param {number} x - World coordinate X
     * @param {number} z - World coordinate Z
     * @returns {{ok: boolean, ...}}
     */
    moveCamera: function (x, z) {
        var mod = Process.findModuleByName(MODULE_NAME);
        if (!mod) return { error: "module not found" };

        var getDomain = new NativeFunction(mod.findExportByName("il2cpp_domain_get"), "pointer", []);
        var getAssemblies = new NativeFunction(mod.findExportByName("il2cpp_domain_get_assemblies"), "pointer", ["pointer", "pointer"]);
        var getImage = new NativeFunction(mod.findExportByName("il2cpp_assembly_get_image"), "pointer", ["pointer"]);
        var classFromName = new NativeFunction(mod.findExportByName("il2cpp_class_from_name"), "pointer", ["pointer", "pointer", "pointer"]);
        var getMethods = new NativeFunction(mod.findExportByName("il2cpp_class_get_methods"), "pointer", ["pointer", "pointer"]);
        var getMethodName = new NativeFunction(mod.findExportByName("il2cpp_method_get_name"), "pointer", ["pointer"]);
        var getMethodParamCount = new NativeFunction(mod.findExportByName("il2cpp_method_get_param_count"), "uint32", ["pointer"]);
        var getMethodFlags = new NativeFunction(mod.findExportByName("il2cpp_method_get_flags"), "uint32", ["pointer", "pointer"]);

        var domain = getDomain();
        var sizeOut = Memory.alloc(8);
        var assemblies = getAssemblies(domain, sizeOut);
        var asmCount = sizeOut.readU32();

        // Find MapCameraMgr class
        var nsPtr = Memory.allocUtf8String("TFW.Map");
        var clsPtr = Memory.allocUtf8String("MapCameraMgr");
        var cameraMgrClass = null;

        for (var a = 0; a < asmCount; a++) {
            var assembly = assemblies.add(a * Process.pointerSize).readPointer();
            var image = getImage(assembly);
            var klass = classFromName(image, nsPtr, clsPtr);
            if (!klass.isNull()) { cameraMgrClass = klass; break; }
        }

        if (!cameraMgrClass) return { error: "MapCameraMgr class not found" };

        // Find MoveCameraToTargetInstantly method
        var moveMethod = null;
        var iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(ptr(0));
        var mi;
        while (!(mi = getMethods(cameraMgrClass, iter)).isNull()) {
            var mnP = getMethodName(mi);
            if (mnP.isNull()) continue;
            var name = mnP.readUtf8String();
            if (name === "MoveCameraToTargetInstantly") {
                var pc = getMethodParamCount(mi);
                if (pc === 3) { // float x, float z, Action callback
                    moveMethod = mi;
                    break;
                }
            }
        }

        if (!moveMethod) return { error: "MoveCameraToTargetInstantly method not found" };

        var funcAddr = moveMethod.readPointer();

        // Check if static: MethodAttributes.Static = 0x0010
        var iflags = Memory.alloc(4);
        var flags = getMethodFlags(moveMethod, iflags);
        var isStatic = (flags & 0x10) !== 0;

        console.log("[frida_hook] MoveCameraToTargetInstantly at " + funcAddr +
                    " static=" + isStatic + " flags=0x" + flags.toString(16));

        var xFloat = x;
        var zFloat = z;

        try {
            if (isStatic) {
                // Static: (float x, float z, Action callback, MethodInfo*)
                var moveFunc = new NativeFunction(funcAddr, "void", ["float", "float", "pointer", "pointer"]);
                moveFunc(xFloat, zFloat, ptr(0), moveMethod);
            } else {
                // Instance: (this, float x, float z, Action callback, MethodInfo*)
                // Pass NULL for this — all state is static anyway
                var moveFunc = new NativeFunction(funcAddr, "void", ["pointer", "float", "float", "pointer", "pointer"]);
                moveFunc(ptr(0), xFloat, zFloat, ptr(0), moveMethod);
            }
            console.log("[frida_hook] MoveCameraToTargetInstantly(" + xFloat + ", " + zFloat + ") called OK");
            return {
                ok: true,
                address: funcAddr.toString(),
                isStatic: isStatic,
                x: xFloat,
                z: zFloat
            };
        } catch (e) {
            return {
                error: "Call failed: " + e.message,
                address: funcAddr.toString(),
                isStatic: isStatic
            };
        }
    },

    /**
     * Search IL2CPP classes for names matching a keyword (case-insensitive).
     * Returns class names + their methods.
     */
    searchClasses: function (keyword) {
        var mod = Process.findModuleByName(MODULE_NAME);
        if (!mod) return { error: "module not found" };

        var getDomain = new NativeFunction(mod.findExportByName("il2cpp_domain_get"), "pointer", []);
        var getAssemblies = new NativeFunction(mod.findExportByName("il2cpp_domain_get_assemblies"), "pointer", ["pointer", "pointer"]);
        var getImage = new NativeFunction(mod.findExportByName("il2cpp_assembly_get_image"), "pointer", ["pointer"]);
        var getImageName = new NativeFunction(mod.findExportByName("il2cpp_image_get_name"), "pointer", ["pointer"]);
        var getClassCount = new NativeFunction(mod.findExportByName("il2cpp_image_get_class_count"), "uint32", ["pointer"]);
        var getClass = new NativeFunction(mod.findExportByName("il2cpp_image_get_class"), "pointer", ["pointer", "uint32"]);
        var getClassName = new NativeFunction(mod.findExportByName("il2cpp_class_get_name"), "pointer", ["pointer"]);
        var getClassNamespace = new NativeFunction(mod.findExportByName("il2cpp_class_get_namespace"), "pointer", ["pointer"]);
        var getMethods = new NativeFunction(mod.findExportByName("il2cpp_class_get_methods"), "pointer", ["pointer", "pointer"]);
        var getMethodName = new NativeFunction(mod.findExportByName("il2cpp_method_get_name"), "pointer", ["pointer"]);

        var domain = getDomain();
        var sizeOut = Memory.alloc(8);
        var assemblies = getAssemblies(domain, sizeOut);
        var asmCount = sizeOut.readU32();

        var kw = keyword.toLowerCase();
        var results = [];

        for (var a = 0; a < asmCount && results.length < 50; a++) {
            var assembly = assemblies.add(a * Process.pointerSize).readPointer();
            var image = getImage(assembly);
            var classCount = getClassCount(image);

            for (var c = 0; c < classCount && results.length < 50; c++) {
                var klass = getClass(image, c);
                if (klass.isNull()) continue;

                var nameP = getClassName(klass);
                if (nameP.isNull()) continue;
                var name = nameP.readUtf8String();

                if (name.toLowerCase().indexOf(kw) === -1) continue;

                var nsP = getClassNamespace(klass);
                var ns = nsP.isNull() ? "" : nsP.readUtf8String();

                // Get methods
                var methods = [];
                var iter = Memory.alloc(Process.pointerSize);
                iter.writePointer(ptr(0));
                var mi;
                while (!(mi = getMethods(klass, iter)).isNull()) {
                    var mnP = getMethodName(mi);
                    if (!mnP.isNull()) {
                        methods.push(mnP.readUtf8String());
                    }
                }

                results.push({
                    namespace: ns,
                    name: name,
                    methods: methods
                });
            }
        }

        return { count: results.length, classes: results };
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
