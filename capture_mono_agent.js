'use strict';

const E_RUN_STATE = {0:"Choice",1:"Combat",2:"Encounter",3:"EndRunDefeat",4:"EndRunVictory",5:"LevelUp",6:"Loot",7:"NewRun",8:"Pedestal",9:"PVPCombat",10:"Shutdown"};
const E_HERO = {0:"Common",1:"Pygmalien",2:"Vanessa",3:"Dooley",4:"Jules",5:"Stelle",6:"Mak",7:"Karnok"};
const E_PLAYER_ATTRIBUTE = {0:"Burn",1:"CritChance",2:"DamageCrit",3:"Experience",4:"Gold",5:"Income",6:"Joy",8:"JoyCrit",9:"Prestige",10:"Health",11:"HealthMax",12:"HealthRegen",13:"HealAmount",14:"HealCrit",15:"Level",16:"Poison",17:"RerollCostModifier",19:"Shield",21:"ShieldCrit",22:"FlatDamageReduction",23:"PercentDamageReduction",24:"Custom_0",25:"Custom_1",26:"Custom_2",27:"Custom_3",28:"Custom_4",29:"Custom_5",30:"Custom_6",31:"Custom_7",32:"Custom_8",33:"Custom_9",34:"Rage",35:"RageMax",36:"Enraged",37:"EnragedDuration",38:"EnragedDurationMax"};
const E_CARD_TYPE = {0:"Item",1:"Skill",2:"Companion",3:"SocketEffect",4:"Encounter"};
const E_CARD_SIZE = {0:"Small",1:"Medium",2:"Large"};
const E_TIER = {0:"Bronze",1:"Silver",2:"Gold",3:"Diamond",4:"Legendary"};
const E_COMBATANT = {0:"Player",1:"Opponent"};
const E_INVENTORY_SECTION = {0:"Hand",1:"Stash"};
const KEEP_PLAYER_ATTR_IDS = {4:true,9:true,10:true,11:true,15:true};
const KEEP_PLAYER_ATTR_COUNT = 5;
const COMMAND_KIND = {
    SelectItemCommand: "buy",
    // MoveItemCommand: "move",
    SelectSkillCommand: "skill_select",
    SelectEncounterCommand: "event_choice",
    RerollCommand: "reroll",
    SellCardCommand: "sell",
    CommitToPedestalCommand: "pedestal_commit",
    ExitCurrentStateCommand: "exit_state"
};
const FULL_DELTA_CARDS = __FULL_DELTA_CARDS__;
const ENABLE_PROBES = __ENABLE_PROBES__;
const ENABLE_BROAD_HOOKS = __ENABLE_BROAD_HOOKS__;
const DELTA_PLAYER_ATTRS = __DELTA_PLAYER_ATTRS__;
const ACTION_EVENT_CARDS = __ACTION_EVENT_CARDS__;
const CAPTURE_OPPONENT_BOARD = __CAPTURE_OPPONENT_BOARD__;
const VERBOSE_HOOK_CALLS = __VERBOSE_HOOK_CALLS__;
const HEAVY_CARD_STATES = {Choice:true,Loot:true,LevelUp:true,Pedestal:true,EndRunVictory:true,EndRunDefeat:true};
const ACTION_CARD_STATES = {Choice:true,Encounter:true,Loot:true,LevelUp:true,Pedestal:true,Combat:true,PVPCombat:true,Replay:true,EndRunVictory:true,EndRunDefeat:true};
const ACTION_TEMPLATE_EVENT_STATES = {Choice:true,Encounter:true,Loot:true,LevelUp:true,Pedestal:true,EndRunVictory:true,EndRunDefeat:true};
const DISABLE_DICTIONARY_PROBING = false;
const MAX_INLINE_CARD_COUNT = 4;
const INLINE_CARD_STATES = {Loot:true,LevelUp:true,Choice:true,Encounter:true,Pedestal:true,EndRunVictory:true,EndRunDefeat:true};
const SLOW_HOOK_MS = 8;
const ATTRS_STAT_REPORT_INTERVAL_MS = 30000;
// QW9: Fast GameSim path — merges lean+payload into single pass, batches field
// reads, relaxes pointer validation inside known-good object graphs, caches
// SelectionSet by pointer identity, and throttles player attrs to state changes.
// Set to false to revert to the legacy double-read path.
const FAST_GAMESIM_PATH = true;
// QW9: Throttled sync attrs — only read player attributes when state changes
// or on the first snapshot. Eliminates the 87% deferred-attrs failure cascade.
const ATTRS_THROTTLE_ON_STATE_CHANGE = true;
// QW9: Minimum interval between sync attr reads (ms) as a safety valve.
const ATTRS_SYNC_MIN_INTERVAL_MS = 2000;
let _lastAttrsSyncMs = 0;
let _lastAttrsSyncState = null;
let _attrsSyncThrottledCount = 0;
let _attrsSyncReadCount = 0;
let _attrsSyncEmptyCount = 0;
// QW9: Cache last successful attrs result — readEnumIntDict fails ~90% of the
// time (the managed dict entries array is often mid-update when our hook fires).
// Cache the last good result and reuse it on failure. Gold/HP change infrequently
// enough that stale-by-one-snapshot is acceptable for the overlay.
let _lastGoodAttrs = null;
let _attrsFromCacheCount = 0;
// QW10: Cached dictionary layout for player attrs. Populated on first successful
// readEnumIntDict call, then _fastReadPlayerAttrs uses pure direct memory reads
// with ZERO NativeFunction calls. This eliminates the ~50-100ms readEnumIntDict cost.
let _playerAttrsDictLayout = null; // {entriesOff, countOff, entrySize, hashOff, keyOff, valueOff}
let _fastAttrsReadCount = 0;
let _fastAttrsFailCount = 0;
// QW9: SelectionSet cache (content-hash, see _readSelectionSetCached)
let _lastSelectionSetResult = [];
let _selectionSetCacheHits = 0;
let _selectionSetCacheMisses = 0;
// QW9: Batch field reader offset cache — keyed by className, maps field name to {offset, type}
const _batchFieldOffsetCache = {};
// QW10: Cache DataVersion string (static per run, avoids mono_string_to_utf8 on every hook)
let _cachedDataVersion = null;
let snapshotCounter = 0;
// Deferred Player.Attributes: enumerate off the game thread via setImmediate.
// If the deferred decode returns empty or throws, force a sync read on the next
// eligible hook so we recover the data at the cost of one stutter.
let _pendingSyncAttrsRead = false;
let _deferredAttrsSuccessCount = 0;
let _deferredAttrsFailureCount = 0;
let _syncAttrsFallbackCount = 0;
let _lastAttrsStatReportMs = 0;
const captureCallCounts = {};
const hookedCode = {};
const probeLogCounts = {};
const commandProbeLogCounts = {};
const argLogCounts = {};
const seenMessageIds = {};
const seenMessageOrder = [];
const MAX_SEEN_MESSAGE_IDS = 512;
const seenCommandKeys = {};
const seenCommandOrder = [];
const MAX_SEEN_COMMAND_KEYS = 512;
let commandCounter = 0;

const mono = Process.getModuleByName('mono-2.0-bdwgc.dll');
function monoExport(name, ret, args) {
    const addr = mono.getExportByName(name);
    if (!addr) { send({type:'error',msg:'Export not found: '+name}); return null; }
    return new NativeFunction(addr, ret, args);
}

function monoOptionalExport(name, ret, args) {
    try {
        const addr = mono.getExportByName(name);
        return addr ? new NativeFunction(addr, ret, args) : null;
    } catch (e) {
        return null;
    }
}

const mono_get_root_domain = monoExport('mono_get_root_domain','pointer',[]);
const mono_thread_attach = monoExport('mono_thread_attach','pointer',['pointer']);
const mono_assembly_foreach = monoExport('mono_assembly_foreach','void',['pointer','pointer']);
const mono_assembly_get_image = monoExport('mono_assembly_get_image','pointer',['pointer']);
const mono_image_get_name = monoExport('mono_image_get_name','pointer',['pointer']);
const mono_image_get_table_rows = monoExport('mono_image_get_table_rows','int',['pointer','int']);
const mono_class_from_name = monoExport('mono_class_from_name','pointer',['pointer','pointer','pointer']);
const mono_class_get = monoExport('mono_class_get','pointer',['pointer','uint32']);
const mono_class_get_name = monoExport('mono_class_get_name','pointer',['pointer']);
const mono_class_get_namespace = monoExport('mono_class_get_namespace','pointer',['pointer']);
const mono_class_get_methods = monoExport('mono_class_get_methods','pointer',['pointer','pointer']);
const mono_class_get_fields = monoExport('mono_class_get_fields','pointer',['pointer','pointer']);
const mono_class_get_method_from_name = monoExport('mono_class_get_method_from_name','pointer',['pointer','pointer','int']);
const mono_method_get_name = monoExport('mono_method_get_name','pointer',['pointer']);
const mono_method_signature = monoExport('mono_method_signature','pointer',['pointer']);
const mono_compile_method = monoExport('mono_compile_method','pointer',['pointer']);
const mono_signature_get_param_count = monoExport('mono_signature_get_param_count','uint32',['pointer']);
const mono_signature_get_params = monoExport('mono_signature_get_params','pointer',['pointer','pointer']);
const mono_signature_get_return_type = monoExport('mono_signature_get_return_type','pointer',['pointer']);
const mono_field_get_name = monoExport('mono_field_get_name','pointer',['pointer']);
const mono_field_get_type = monoExport('mono_field_get_type','pointer',['pointer']);
const mono_field_get_value = monoExport('mono_field_get_value','void',['pointer','pointer','pointer']);
const mono_field_get_offset = monoExport('mono_field_get_offset','int',['pointer']);
const mono_object_get_class = monoExport('mono_object_get_class','pointer',['pointer']);
const mono_type_get_name = monoExport('mono_type_get_name','pointer',['pointer']);
const mono_string_to_utf8 = monoExport('mono_string_to_utf8','pointer',['pointer']);
const mono_free = monoExport('mono_free','void',['pointer']);
const mono_class_get_element_class = monoOptionalExport('mono_class_get_element_class','pointer',['pointer']);
const mono_class_value_size = monoOptionalExport('mono_class_value_size','int',['pointer','pointer']);
const mono_class_get_parent = monoOptionalExport('mono_class_get_parent','pointer',['pointer']);

const domain = mono_get_root_domain();
if (!domain.isNull()) { mono_thread_attach(domain); send({type:'info',msg:'Attached to Mono domain'}); }

const assemblies = [];
const asmCb = new NativeCallback(function(a,u){assemblies.push(a);},'void',['pointer','pointer']);
mono_assembly_foreach(asmCb, ptr(0));
send({type:'info',msg:'Found '+assemblies.length+' assemblies'});

const imageMap = {};
for (const asm of assemblies) {
    const img = mono_assembly_get_image(asm);
    if (img.isNull()) continue;
    const np = mono_image_get_name(img);
    if (!np.isNull()) imageMap[np.readUtf8String()] = img;
}
send({type:'info',msg:'Images: '+Object.keys(imageMap).join(', ')});

function findClass(ns, cls) {
    const nsP = Memory.allocUtf8String(ns), clsP = Memory.allocUtf8String(cls);
    for (const n of ['TheBazaarRuntime','Assembly-CSharp','BazaarGameShared','BazaarGameClient']) {
        if (!imageMap[n]) continue;
        const k = mono_class_from_name(imageMap[n], nsP, clsP);
        if (!k.isNull()) { send({type:'info',msg:'Found '+ns+'.'+cls+' in '+n}); return k; }
    }
    for (const [n,img] of Object.entries(imageMap)) {
        const k = mono_class_from_name(img, nsP, clsP);
        if (!k.isNull()) { send({type:'info',msg:'Found '+ns+'.'+cls+' in '+n}); return k; }
    }
    return null;
}

function getMethods(klass) {
    const methods = [], iter = Memory.alloc(Process.pointerSize);
    iter.writePointer(ptr(0));
    while (true) {
        const m = mono_class_get_methods(klass, iter);
        if (m.isNull()) break;
        const np = mono_method_get_name(m);
        const sig = getMethodSignature(m);
        methods.push({
            ptr:m,
            name: np.isNull()?'?':np.readUtf8String(),
            paramCount: sig.paramCount,
            params: sig.params,
            ret: sig.ret,
        });
    }
    return methods;
}

function cloneMethodWithMeta(method, extra) {
    return Object.assign({}, method, extra || {});
}

// Read declared fields on a single klass (no inheritance walk).
function getDeclaredFields(klass) {
    const fields = [], iter = Memory.alloc(Process.pointerSize);
    iter.writePointer(ptr(0));
    while (true) {
        const f = mono_class_get_fields(klass, iter);
        if (f.isNull()) break;
        const np = mono_field_get_name(f);
        fields.push({
            ptr:f,
            name: np.isNull()?'?':np.readUtf8String(),
            offset: mono_field_get_offset(f),
            type: getFieldTypeName(f),
        });
    }
    return fields;
}

// mono_class_get_fields enumerates declared fields only — inherited fields are
// invisible unless we walk the parent chain. If The Bazaar moves Hero /
// UnlockedSlots / Attributes onto a base class in a patch, every Player field
// read silently returns null and the snapshot collapses to whatever the
// Attributes dict happens to surface that tick.
const _GET_FIELDS_MAX_PARENT_DEPTH = 6;
function getFields(klass) {
    const out = [];
    const seenNames = Object.create(null);
    let cur = klass;
    for (let depth = 0; depth <= _GET_FIELDS_MAX_PARENT_DEPTH && cur && !cur.isNull(); depth++) {
        const declared = getDeclaredFields(cur);
        for (const f of declared) {
            if (!f || !f.name) continue;
            if (seenNames[f.name]) continue; // child overrides parent of same name
            seenNames[f.name] = true;
            out.push(f);
        }
        if (!mono_class_get_parent) break;
        const parent = mono_class_get_parent(cur);
        if (!parent || parent.isNull() || parent.equals(cur)) break;
        const parentName = classFullName(parent);
        if (!parentName || parentName === 'System.Object' || parentName === 'System.ValueType') break;
        cur = parent;
    }
    return out;
}

function readOwnedUtf8(ptrValue) {
    if (!ptrValue || ptrValue.isNull()) return null;
    const s = ptrValue.readUtf8String();
    mono_free(ptrValue);
    return s;
}

function classFullName(klass) {
    if (!klass || klass.isNull()) return null;
    const nsPtr = mono_class_get_namespace(klass);
    const namePtr = mono_class_get_name(klass);
    if (namePtr.isNull()) return null;
    const ns = nsPtr.isNull() ? '' : nsPtr.readUtf8String();
    const name = namePtr.readUtf8String();
    return ns ? (ns + '.' + name) : name;
}

function getTypeName(typePtr) {
    try {
        if (!typePtr || typePtr.isNull()) return null;
        return readOwnedUtf8(mono_type_get_name(typePtr));
    } catch (e) {
        return null;
    }
}

function getFieldTypeName(fieldPtr) {
    try {
        return getTypeName(mono_field_get_type(fieldPtr));
    } catch (e) {
        return null;
    }
}

function getMethodSignature(methodPtr) {
    try {
        const sig = mono_method_signature(methodPtr);
        if (!sig || sig.isNull()) return {paramCount: 0, params: [], ret: '?'};
        const paramCount = mono_signature_get_param_count(sig);
        const iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(ptr(0));
        const params = [];
        for (let i = 0; i < paramCount; i++) {
            params.push(getTypeName(mono_signature_get_params(sig, iter)) || '?');
        }
        return {
            paramCount: paramCount,
            params: params,
            ret: getTypeName(mono_signature_get_return_type(sig)) || 'void',
        };
    } catch (e) {
        return {paramCount: 0, params: [], ret: '?'};
    }
}

function formatMethod(method) {
    return method.name + '(' + method.params.join(', ') + ') -> ' + method.ret;
}

function findFieldInfo(classKey, names) {
    const info = fieldInfoCache[classKey];
    if (!info) return null;
    for (const name of names) {
        if (info[name]) return info[name];
    }
    return null;
}

function getDynamicFieldsForKlass(klass) {
    const className = classFullName(klass);
    if (!className) return [];
    if (!dynamicFieldInfoCache[className]) {
        dynamicFieldInfoCache[className] = getFields(klass);
    }
    return dynamicFieldInfoCache[className];
}

function enumerateClassesInImage(image, assemblyName) {
    const classes = [];
    try {
        const MONO_TABLE_TYPEDEF = 2;
        const rows = mono_image_get_table_rows(image, MONO_TABLE_TYPEDEF);
        for (let i = 1; i <= rows; i++) {
            try {
                const token = (0x02000000 | i) >>> 0;
                const klass = mono_class_get(image, token);
                if (!klass || klass.isNull()) continue;
                const fullName = classFullName(klass);
                if (!fullName) continue;
                classes.push({
                    klass: klass,
                    assembly: assemblyName,
                    fullName: fullName,
                    name: fullName.split('.').pop(),
                });
            } catch (e) {}
        }
    } catch (e) {
        send({type:'debug',msg:'enumerateClassesInImage '+assemblyName+': '+e});
    }
    return classes;
}

function readObjectFieldByInfo(objPtr, field) {
    try {
        if (!objPtr || objPtr.isNull() || !field || !field.ptr) return null;
        const buf = Memory.alloc(Process.pointerSize);
        buf.writePointer(ptr(0));
        mono_field_get_value(objPtr, field.ptr, buf);
        const value = buf.readPointer();
        return isReadablePointer(value) ? value : null;
    } catch (e) {
        return null;
    }
}

function readObjectField(objPtr, classKey, fieldNames) {
    try {
        if (!objPtr || objPtr.isNull()) return null;
        const field = findFieldInfo(classKey, Array.isArray(fieldNames) ? fieldNames : [fieldNames]);
        if (!field) return null;
        return readObjectFieldByInfo(objPtr, field);
    } catch (e) {
        send({type:'debug',msg:'readObjectField '+classKey+'.'+fieldNames+': '+e});
        return null;
    }
}

function logNullField(key, detail) {
    fieldNullLogCounts[key] = (fieldNullLogCounts[key] || 0) + 1;
    if (fieldNullLogCounts[key] <= 5) {
        send({type:'debug',msg:key+' '+detail});
    }
}

function isKnownBadPointer(ptrValue) {
    try {
        if (!ptrValue || ptrValue.isNull()) return true;
        const s = ptrValue.toString().toLowerCase();
        return s === '0xffffffffffffffff' ||
               s === '0xcccccccccccccccc' ||
               s === '0xcdcdcdcdcdcdcdcd' ||
               s === '0xdddddddddddddddd' ||
               s === '0xfeeefeeefeeefeee';
    } catch (e) {
        return true;
    }
}

// QW2: Persist range cache across hook calls â€” managed heap pages are stable during a running game.
// Cache is no longer cleared per invocation; instead, a simple size cap evicts the oldest half
// when entries exceed 256. The existing try/catch in isReadableAddress handles unmapped-page edge cases.
const _rangeCache = {};
const _rangeCacheKeys = []; // insertion-order key list for LRU-style eviction
const _RANGE_CACHE_MAX = 256;
let _rangeCacheHits = 0;
let _rangeCacheMisses = 0;
function resetRangeCache() {
    // QW2: no-op â€” cache is now persistent across hook calls. Kept to avoid ReferenceErrors.
}

function isReadableAddress(ptrValue, size) {
    try {
        if (isKnownBadPointer(ptrValue)) return false;
        // Cache lookup on page-aligned address (4KB pages)
        const pageKey = ptrValue.and(ptr('0xFFFFFFFFFFFFF000')).toString();
        let range;
        if (pageKey in _rangeCache) {
            range = _rangeCache[pageKey];
            _rangeCacheHits++;
        } else {
            range = Process.findRangeByAddress(ptrValue);
            // QW2: cap cache at _RANGE_CACHE_MAX â€” evict oldest half when full
            if (_rangeCacheKeys.length >= _RANGE_CACHE_MAX) {
                const evict = _rangeCacheKeys.splice(0, _RANGE_CACHE_MAX >> 1);
                for (const k of evict) delete _rangeCache[k];
            }
            _rangeCache[pageKey] = range;
            _rangeCacheKeys.push(pageKey);
            _rangeCacheMisses++;
        }
        if (!range || String(range.protection || '').indexOf('r') === -1) return false;
        const bytes = size || 1;
        if (bytes <= 1) return true;
        const maxStart = range.base.add(Math.max(0, range.size - bytes));
        return ptrValue.compare(maxStart) <= 0;
    } catch (e) {
        return false;
    }
}

function isReadablePointer(ptrValue) {
    return isReadableAddress(ptrValue, 1);
}

function safeReadPointer(basePtr, offset) {
    try {
        if (!isReadablePointer(basePtr)) return null;
        const addr = offset === undefined ? basePtr : basePtr.add(offset);
        if (!isReadableAddress(addr, Process.pointerSize)) return null;
        const value = addr.readPointer();
        return isReadablePointer(value) ? value : null;
    } catch (e) {
        return null;
    }
}

function readMonoString(strObj) {
    if (!isReadablePointer(strObj)) return null;
    try {
        const p = mono_string_to_utf8(strObj);
        if (!p || p.isNull()) return null;
        const s = p.readUtf8String();
        mono_free(p);
        return s;
    } catch (e) {
        return null;
    }
}

// QW10: Direct managed string reader — reads UTF-16 chars from the Mono string's
// internal char buffer with ZERO NativeFunction calls. MonoString layout:
//   offset 0: MonoObject header (vtable ptr)
//   offset 8: int32 length (char count)
//   offset 12: padding (4 bytes on 64-bit)
//   offset 16: char[] chars (UTF-16LE, 2 bytes per char)  — NOTE: may be 12 on some builds
// Falls back to readMonoString on failure.
const MONO_STRING_LENGTH_OFFSET = 8;
const MONO_STRING_CHARS_OFFSET = 12; // Will try 12 first (common), then 16
let _monoStringCharsOffset = null; // auto-detected on first call
function _directReadMonoString(strPtr) {
    try {
        if (!strPtr || strPtr.isNull()) return null;
        const len = strPtr.add(MONO_STRING_LENGTH_OFFSET).readS32();
        if (len <= 0 || len > 4096) return null;
        // Auto-detect chars offset on first call by verifying against mono_string_to_utf8
        if (_monoStringCharsOffset === null) {
            // Try offset 12 (compact layout) — read first char
            const c12 = strPtr.add(12).readU16();
            // Verify: valid ASCII/printable char?
            if (c12 >= 0x20 && c12 < 0x7F) {
                _monoStringCharsOffset = 12;
            } else {
                // Try offset 16
                const c16 = strPtr.add(16).readU16();
                if (c16 >= 0x20 && c16 < 0x7F) {
                    _monoStringCharsOffset = 16;
                } else {
                    // Can't determine — fall back to slow path permanently
                    return readMonoString(strPtr);
                }
            }
            send({type:'info', msg:'QW10 mono string chars offset detected: ' + _monoStringCharsOffset});
        }
        const chars = strPtr.add(_monoStringCharsOffset).readUtf16String(len);
        return chars;
    } catch (e) {
        return readMonoString(strPtr);
    }
}

// QW4: Cache isCommandClassName results (5+ string ops + loop per call)
const _isCommandClassNameCache = new Map();
function isCommandClassName(className) {
    if (!className) return false;
    const cached = _isCommandClassNameCache.get(className);
    if (cached !== undefined) return cached;
    const simple = className.split('.').pop();
    let result = false;
    if (COMMAND_KIND[simple]) {
        result = true;
    } else {
        for (const key of Object.keys(COMMAND_KIND)) {
            if (simple === key || simple.startsWith(key + '`') || simple.endsWith(key) || simple.includes(key)) {
                result = true;
                break;
            }
        }
    }
    _isCommandClassNameCache.set(className, result);
    return result;
}

function resolveCommandKindInfo(className) {
    if (!className) return null;
    const simple = className.split('.').pop();
    if (COMMAND_KIND[simple]) {
        return { simpleName: simple, commandKey: simple, eventType: COMMAND_KIND[simple] };
    }
    for (const key of Object.keys(COMMAND_KIND)) {
        if (simple === key) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
        if (simple.startsWith(key + '`')) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
        if (simple.endsWith(key)) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
        if (simple.includes(key)) {
            return { simpleName: simple, commandKey: key, eventType: COMMAND_KIND[key] };
        }
    }
    return null;
}

function isCommandParamType(typeName) {
    if (!typeName) return false;
    if (isCommandClassName(typeName)) return true;
    if (typeName.includes('INetCommand')) return true;
    if (typeName.includes('.ICommand')) return true;
    if (typeName.endsWith('.ICommand')) return true;
    if (typeName.includes('Command')) return true;
    return false;
}

function readGuid(base, off) {
    try {
        const b = base.add(off).readByteArray(16);
        if (!b) return null;
        const a = new Uint8Array(b);
        const h = (x) => ('0'+x.toString(16)).slice(-2);
        return h(a[3])+h(a[2])+h(a[1])+h(a[0])+'-'+h(a[5])+h(a[4])+'-'+h(a[7])+h(a[6])+'-'+h(a[8])+h(a[9])+'-'+h(a[10])+h(a[11])+h(a[12])+h(a[13])+h(a[14])+h(a[15]);
    } catch(e) { return null; }
}

function findDynamicField(objPtr, names) {
    try {
        if (!objPtr || objPtr.isNull()) return null;
        const klass = mono_object_get_class(objPtr);
        if (!klass || klass.isNull()) return null;
        const fields = getDynamicFieldsForKlass(klass);
        for (const name of names) {
            const field = fields.find(f => f && f.name === name);
            if (field) return field;
        }
    } catch (e) {}
    return null;
}

function readDynamicObjectField(objPtr, names) {
    const field = findDynamicField(objPtr, names);
    if (!field) return null;
    return readObjectFieldByInfo(objPtr, field);
}

function readDynamicI32Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readS32();
    } catch (e) {
        return null;
    }
}

function readDynamicU32Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readU32();
    } catch (e) {
        return null;
    }
}

function readDynamicU16Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readU16();
    } catch (e) {
        return null;
    }
}

function readDynamicBoolField(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return objPtr.add(field.offset).readU8() !== 0;
    } catch (e) {
        return null;
    }
}

function readDynamicGuidField(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        return readGuid(objPtr, field.offset);
    } catch (e) {
        return null;
    }
}

function readDynamicStringField(objPtr, names) {
    const strPtr = readDynamicObjectField(objPtr, names);
    return strPtr && !strPtr.isNull() ? readMonoString(strPtr) : null;
}

function readDynamicNullableU32Field(objPtr, names) {
    try {
        const field = findDynamicField(objPtr, names);
        if (!field) return null;
        const base = objPtr.add(field.offset);
        return base.readU8() ? base.add(4).readU32() : null;
    } catch (e) {
        return null;
    }
}

function readManagedIntArray(arrayPtr, limit) {
    if (!arrayPtr || arrayPtr.isNull()) return [];
    try {
        const length = getManagedArrayLength(arrayPtr);
        if (length <= 0) return [];
        const count = Math.min(length, limit || length, 32);
        const base = getManagedArrayDataPtr(arrayPtr);
        if (!isReadablePointer(base)) return [];
        const values = [];
        for (let i = 0; i < count; i++) {
            const addr = base.add(i * 4);
            if (!isReadableAddress(addr, 4)) break;
            values.push(addr.readS32());
        }
        return values;
    } catch (e) {
        return [];
    }
}

function readDynamicIntListField(objPtr, names) {
    try {
        const listPtr = readDynamicObjectField(objPtr, names);
        if (!listPtr || listPtr.isNull()) return [];
        const klass = mono_object_get_class(listPtr);
        if (!klass || klass.isNull()) return [];
        const fields = getDynamicFieldsForKlass(klass);
        const itemsField = findNamedField(fields, ['_items', 'items']);
        if (itemsField) {
            const itemsPtr = readObjectFieldByInfo(listPtr, itemsField);
            if (!itemsPtr || itemsPtr.isNull()) return [];
            const sizeField = findNamedField(fields, ['_size', 'size', '_count', 'count']);
            const size = sizeField ? readScalarField(listPtr, sizeField) : getManagedArrayLength(itemsPtr);
            return readManagedIntArray(itemsPtr, size);
        }
        return readManagedIntArray(listPtr, getManagedArrayLength(listPtr));
    } catch (e) {
        return [];
    }
}

function readManagedObjectPtrArray(arrayPtr, limit) {
    if (!arrayPtr || arrayPtr.isNull()) return [];
    try {
        const length = getManagedArrayLength(arrayPtr);
        if (length <= 0) return [];
        const count = Math.min(length, limit || length, 128);
        const base = getManagedArrayDataPtr(arrayPtr);
        if (!isReadablePointer(base)) return [];
        const values = [];
        for (let i = 0; i < count; i++) {
            const addr = base.add(i * Process.pointerSize);
            if (!isReadableAddress(addr, Process.pointerSize)) break;
            const objPtr = addr.readPointer();
            if (objPtr && !objPtr.isNull()) values.push(objPtr);
        }
        return values;
    } catch (e) {
        return [];
    }
}

function readManagedObjectList(listPtr, limit) {
    try {
        if (!listPtr || listPtr.isNull()) return [];
        const klass = mono_object_get_class(listPtr);
        if (!klass || klass.isNull()) return [];
        const fields = getDynamicFieldsForKlass(klass);
        const itemsField = findNamedField(fields, ['_items', 'items']);
        if (itemsField) {
            const itemsPtr = readObjectFieldByInfo(listPtr, itemsField);
            if (!itemsPtr || itemsPtr.isNull()) return [];
            const sizeField = findNamedField(fields, ['_size', 'size', '_count', 'count']);
            const size = sizeField ? Math.max(0, readScalarField(listPtr, sizeField) || 0) : getManagedArrayLength(itemsPtr);
            return readManagedObjectPtrArray(itemsPtr, size);
        }
        return readManagedObjectPtrArray(listPtr, getManagedArrayLength(listPtr));
    } catch (e) {
        return [];
    }
}

function readGameSimTemplateEventsFromList(eventsPtr) {
    try {
        if (!eventsPtr || eventsPtr.isNull()) return [];
        const eventPtrs = readManagedObjectList(eventsPtr, 96);
        if (!eventPtrs.length) return [];
        const templateEvents = [];
        for (const eventPtr of eventPtrs) {
            if (!eventPtr || eventPtr.isNull()) continue;
            let className = null;
            try {
                const klass = mono_object_get_class(eventPtr);
                className = klass && !klass.isNull() ? classFullName(klass) : null;
            } catch (e) {
                className = null;
            }
            if (!className) continue;

            let eventType = null;
            if (className.includes('GameSimEventCardDealt')) eventType = 'card_dealt';
            else if (className.includes('GameSimEventCardSpawned')) eventType = 'card_spawned';
            if (!eventType) continue;

            const instanceId = readDynamicStringField(eventPtr, ['InstanceId', '<InstanceId>k__BackingField']);
            const templateId = readDynamicStringField(eventPtr, ['TemplateId', '<TemplateId>k__BackingField']);
            if (!instanceId || !templateId) continue;

            const typeInt = readDynamicI32Field(eventPtr, ['Type', '<Type>k__BackingField']);
            const cardType = typeInt !== null ? (E_CARD_TYPE[typeInt] || typeInt) : inferCardTypeFromInstanceId(instanceId);

            templateEvents.push({
                event_type: eventType,
                class_name: className,
                instance_id: instanceId,
                template_id: templateId,
                card_type: cardType,
            });
        }
        return templateEvents;
    } catch (e) {
        send({type:'debug',msg:'readGameSimTemplateEventsFromList:'+e});
        return [];
    }
}

function readMessageIdFromNetMessage(objPtr, classKey) {
    const msgPtr = readObjectField(objPtr, classKey, ['MessageId', '<MessageId>k__BackingField']);
    return msgPtr && !msgPtr.isNull() ? readMonoString(msgPtr) : null;
}

// QW10: Fast message ID reader — uses fieldInfoCache offsets + direct pointer read
// instead of readObjectField (which calls mono_field_get_value via NativeFunction).
// Saves ~2-3 NativeFunction calls per hook.
function _fastReadMessageId(objPtr, classKey) {
    try {
        const info = fieldInfoCache[classKey];
        if (!info) return readMessageIdFromNetMessage(objPtr, classKey); // fallback
        const field = info['MessageId'] || info['<MessageId>k__BackingField'];
        if (!field) return readMessageIdFromNetMessage(objPtr, classKey); // fallback
        const msgPtr = objPtr.add(field.offset).readPointer();
        if (!msgPtr || msgPtr.isNull()) return null;
        return _directReadMonoString(msgPtr);
    } catch (e) { return null; }
}

// QW10: Fast Data field reader — uses fieldInfoCache offsets + direct pointer read.
function _fastReadDataField(objPtr, classKey) {
    try {
        const info = fieldInfoCache[classKey];
        if (!info) return readObjectField(objPtr, classKey, ['Data', '<Data>k__BackingField']); // fallback
        const field = info['Data'] || info['<Data>k__BackingField'];
        if (!field) return readObjectField(objPtr, classKey, ['Data', '<Data>k__BackingField']); // fallback
        const dataPtr = objPtr.add(field.offset).readPointer();
        return (dataPtr && !dataPtr.isNull()) ? dataPtr : null;
    } catch (e) { return null; }
}

// Discovery
const searchTargets = [
    {ns:'TheBazaar',cls:'GameStateHandler'},{ns:'',cls:'GameStateHandler'},{ns:'TheBazaar.Runtime',cls:'GameStateHandler'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameStateSync'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameSim'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageCombatSim'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageRunInitialized'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'GameStateSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'RunSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'RunStateSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'PlayerSnapshotDTO'},
    {ns:'BazaarGameShared.Infra.Messages',cls:'CardSnapshotDTO'},
];

const foundClasses = {}, fieldCache = {}, fieldInfoCache = {}, fieldNullLogCounts = {}, dynamicFieldInfoCache = {}, graphSummaryLogCounts = {}, collectionLayoutLogCounts = {}, cardBucketLogCounts = {}, cardCollectionLogCounts = {};
for (const t of searchTargets) {
    const klass = findClass(t.ns, t.cls);
    if (klass) {
        foundClasses[t.cls] = {klass, ns:t.ns};
        if (t.cls === 'GameStateHandler') {
            const methods = getMethods(klass);
            send({type:'info',msg:'GameStateHandler methods ('+methods.length+'):'});
            for (const m of methods) send({type:'debug',msg:'  '+formatMethod(m)});
            const handlerFields = getFields(klass);
            send({type:'info',msg:'GameStateHandler fields ('+handlerFields.length+'):'});
            for (const f of handlerFields) {
                send({type:'debug',msg:'  '+f.name+'@'+f.offset+(f.type ? ' : '+f.type : '')});
            }
        }
        const fields = getFields(klass);
        const map = {};
        const info = {};
        for (const f of fields) {
            map[f.name] = f.offset;
            info[f.name] = f;
        }
        fieldCache[t.cls] = map;
        fieldInfoCache[t.cls] = info;
        if (t.cls.endsWith('DTO')||t.cls.endsWith('Sync')||t.cls.endsWith('Sim')||t.cls.endsWith('Initialized'))
            send({type:'debug',msg:t.cls+' fields: '+fields.map(f=>f.name+'@'+f.offset).join(', ')});
    }
}

// DTO readers
function readRunSnapshot(p){const o=fieldCache['RunSnapshotDTO'];if(!o||!p||p.isNull())return{};const r={};try{if('GameModeId'in o)r.game_mode_id=readGuid(p,o['GameModeId']);if('Day'in o)r.day=p.add(o['Day']).readU32();if('Hour'in o)r.hour=p.add(o['Hour']).readU32();if('Victories'in o)r.victories=p.add(o['Victories']).readU32();if('Defeats'in o)r.defeats=p.add(o['Defeats']).readU32();if('HasVisitedFates'in o)r.visited_fates=p.add(o['HasVisitedFates']).readU8()!==0;if('DataVersion'in o)r.data_version=readMonoString(p.add(o['DataVersion']).readPointer());}catch(e){send({type:'debug',msg:'readRun:'+e});}return r;}

function readRunStateSnapshot(p){const o=fieldCache['RunStateSnapshotDTO'];if(!o||!p||p.isNull())return{};const r={};try{if('StateName'in o){const v=p.add(o['StateName']).readS32();r.state=E_RUN_STATE[v]||('Unknown('+v+')');r.state_int=v;}
if('CurrentEncounterId'in o)r.current_encounter_id=readMonoString(p.add(o['CurrentEncounterId']).readPointer());
if('RerollCost'in o){try{const b=p.add(o['RerollCost']);if(b.readU8())r.reroll_cost=b.add(4).readU32();}catch(e){}}
if('RerollsRemaining'in o){try{const b=p.add(o['RerollsRemaining']);if(b.readU8())r.rerolls_remaining=b.add(4).readU32();}catch(e){}}
// F4: selection_set gated to interesting states (Choice/Loot/LevelUp/Pedestal/Encounter)
if('SelectionSet'in o&&ACTION_CARD_STATES[r.state])r.selection_set=readStringList(p.add(o['SelectionSet']).readPointer());
}catch(e){send({type:'debug',msg:'readState:'+e});}return r;}

function readPlayerSnapshot(p){const o=fieldCache['PlayerSnapshotDTO'];if(!o||!p||p.isNull())return{};const r={};try{if('Hero'in o){const v=p.add(o['Hero']).readS32();r.hero=E_HERO[v]||('Unknown('+v+')');}
if('UnlockedSlots'in o)r.unlocked_slots=p.add(o['UnlockedSlots']).readU16();
// Keep only the live HUD / enrichment attributes to reduce snapshot work.
if('Attributes'in o){const dp=readObjectField(p,'PlayerSnapshotDTO',['Attributes']);if(dp&&!dp.isNull()){const attrs=readEnumIntDict(dp,'PlayerSnapshotDTO.Attributes',KEEP_PLAYER_ATTR_IDS,KEEP_PLAYER_ATTR_COUNT);for(const[k,v]of Object.entries(attrs))r[E_PLAYER_ATTRIBUTE[parseInt(k)]||('attr_'+k)]=v;}else send({type:'debug',msg:'PlayerSnapshotDTO.Attributes pointer was null'});}}catch(e){send({type:'debug',msg:'readPlayer:'+e});}return r;}

function readCardSnapshot(p){const o=fieldCache['CardSnapshotDTO'];if(!o||!isReadablePointer(p))return{};const c={};try{if('InstanceId'in o)c.instance_id=readMonoString(safeReadPointer(p,o['InstanceId']));if('TemplateId'in o)c.template_id=readGuid(p,o['TemplateId']);if('Tier'in o){const v=p.add(o['Tier']).readS32();c.tier=E_TIER[v]||v;}if('Type'in o){const v=p.add(o['Type']).readS32();c.type=E_CARD_TYPE[v]||v;}if('Size'in o){const v=p.add(o['Size']).readS32();c.size=E_CARD_SIZE[v]||v;}if('Owner'in o){try{const b=p.add(o['Owner']);if(b.readU8()){const v=b.add(4).readS32();c.owner=E_COMBATANT[v]||v;}else c.owner=null;}catch(e){c.owner=null;}}if('Socket'in o){try{const b=p.add(o['Socket']);c.socket=b.readU8()?b.add(4).readS32():null;}catch(e){c.socket=null;}}if('Section'in o){try{const b=p.add(o['Section']);if(b.readU8()){const v=b.add(4).readS32();c.section=E_INVENTORY_SECTION[v]||v;}else c.section=null;}catch(e){c.section=null;}}if(isSuspiciousTemplateId(c.template_id)){const probe=buildCardDebugProbe(p,{info:fieldInfoCache['CardSnapshotDTO']},false);if(probe)c._debug_probe=probe;c._debug_source='CardSnapshotDTO';}}catch(e){send({type:'debug',msg:'readCard:'+e});}return c;}

// QW1: _fieldInfoPrewarmed â€” set to true after attach-time pre-warming; once set,
// getFieldInfoForTypeName skips cold class walks on the hook thread.
let _fieldInfoPrewarmed = false;

function getFieldInfoForTypeName(typeName){
    try{
        const parsed=parseTypeName(typeName);
        if(!parsed) return null;
        const fullName=(parsed.ns?parsed.ns+'.':'')+parsed.cls;
        if(fieldInfoCache[fullName]) return {map:fieldCache[fullName], info:fieldInfoCache[fullName], fullName:fullName};
        // QW1: after pre-warming, skip cold class walks on the hook thread to eliminate 50-100ms spikes
        if(_fieldInfoPrewarmed){
            send({type:'debug',msg:'skipping cold class: '+fullName});
            return null;
        }
        const klass=findClass(parsed.ns, parsed.cls);
        if(!klass || klass.isNull()) return null;
        const fields=getFields(klass);
        const map={};
        const info={};
        for(const f of fields){
            map[f.name]=f.offset;
            info[f.name]=f;
        }
        fieldCache[fullName]=map;
        fieldInfoCache[fullName]=info;
        logCardCollectionInfo('card-type:'+fullName,'Resolved card value type '+fullName+' fields: '+fields.map(f=>f.name+'@'+f.offset+(f.type?':'+f.type:'')).join(', '));
        return {map:map, info:info, fullName:fullName};
    }catch(e){
        return null;
    }
}

function isInlineCardValueType(typeName){
    const t=String(typeName||'');
    if(!t) return false;
    if(t.startsWith('valuetype ')) return true;
    if(t.includes('SimUpdateCard')) return true;
    return false;
}

function normalizeValueTypeOffset(offset){
    const headerSize = Process.pointerSize * 2;
    return offset >= headerSize ? (offset - headerSize) : offset;
}

function readEntryStringKey(entryBase, field){
    if(!field) return null;
    const offsets = [field.offset];
    const norm = normalizeValueTypeOffset(field.offset);
    if(norm !== field.offset) offsets.push(norm);
    for(const off of offsets){
        const ptrValue = safeReadPointer(entryBase, off);
        if(ptrValue && !ptrValue.isNull()){
            const s = readMonoString(ptrValue);
            if(s) return s;
        }
    }
    return null;
}

function getCandidateFieldOffsets(field){
    if(!field) return [];
    const norm = normalizeValueTypeOffset(field.offset);
    const offsets = [norm];
    if(norm !== field.offset) offsets.push(field.offset);
    return offsets;
}

function inferCardTypeFromInstanceId(instanceId){
    const value = String(instanceId || '');
    if(value.startsWith('skl_')) return 'Skill';
    if(value.startsWith('itm_')) return 'Item';
    if(value.startsWith('com_')) return 'Companion';
    if(value.startsWith('enc_') || value.startsWith('ste_') || value.startsWith('ped_')) return 'Encounter';
    return null;
}

function isSuspiciousTemplateId(templateId){
    const value = String(templateId || '').toLowerCase();
    if(!value) return false;
    if(value === '00000000-0000-0000-0000-000000000000') return true;
    return value.endsWith('-0000-0000-000000000000');
}

function shouldProbeCardField(field){
    if(!field) return false;
    const name = String(field.name || '').toLowerCase();
    const typeName = String(field.type || '');
    if(typeName.startsWith('System.String') || typeName.startsWith('System.Guid')) return true;
    return name.includes('template') || name.includes('instance') || name.endsWith('id') ||
           name.includes('name') || name.includes('slug') || name.includes('key') ||
           name.includes('type') || name.includes('card');
}

function isDebugProbePrimitiveType(typeName){
    typeName = String(typeName || '');
    return typeName.startsWith('System.String') ||
           typeName.startsWith('System.Guid') ||
           typeName.startsWith('System.Boolean') ||
           typeName.startsWith('System.Nullable<') ||
           typeName.startsWith('System.Byte') ||
           typeName.startsWith('System.SByte') ||
           typeName.startsWith('System.UInt16') ||
           typeName.startsWith('System.Int16') ||
           typeName.startsWith('System.UInt32') ||
           typeName.startsWith('System.Int32');
}

function shouldRecurseCardField(field){
    if(!field) return false;
    const name = String(field.name || '').toLowerCase();
    const typeName = String(field.type || '');
    if(!typeName || typeName.startsWith('System.')) return false;
    return name.includes('template') ||
           name.includes('instance') ||
           name.includes('name') ||
           name.includes('display') ||
           name.includes('definition') ||
           name.includes('skill') ||
           name.includes('card') ||
           name.includes('meta') ||
           name.includes('data');
}

function readDebugProbeField(basePtr, field, inlineValue){
    if(!field) return null;
    const offsets = getCandidateFieldOffsets(field);
    for(const rawOff of offsets){
        const off = inlineValue ? rawOff : field.offset;
        try{
            const typeName = String(field.type || '');
            if(typeName.startsWith('System.String')){
                const ptrValue = safeReadPointer(basePtr, off);
                if(ptrValue && !ptrValue.isNull()){
                    const strValue = readMonoString(ptrValue);
                    if(strValue) return strValue;
                }
                continue;
            }
            if(typeName.startsWith('System.Guid')){
                const guidValue = readGuid(basePtr, off);
                if(guidValue) return guidValue;
                continue;
            }
            if(typeName.startsWith('System.Boolean')){
                return !!basePtr.add(off).readU8();
            }
            if(typeName.startsWith('System.Nullable<')){
                const nullableValue = readMaybeNullableI32(basePtr, Object.assign({}, field, {offset: off}), inlineValue);
                if(nullableValue !== null) return nullableValue;
                continue;
            }
            if(typeName.startsWith('System.Byte')) return basePtr.add(off).readU8();
            if(typeName.startsWith('System.SByte')) return basePtr.add(off).readS8();
            if(typeName.startsWith('System.UInt16')) return basePtr.add(off).readU16();
            if(typeName.startsWith('System.Int16')) return basePtr.add(off).readS16();
            if(typeName.startsWith('System.UInt32')) return basePtr.add(off).readU32();
            if(typeName.startsWith('System.Int32')) return basePtr.add(off).readS32();
        }catch(e){}
    }
    return null;
}

function buildCardDebugProbe(basePtr, fieldMeta, inlineValue, depth, maxCount){
    if(!fieldMeta || !fieldMeta.info) return null;
    depth = depth || 0;
    maxCount = maxCount || 20;
    const probe = {};
    let count = 0;
    const nestedAttempts = [];
    const nestedTypes = [];
    const fields = Object.values(fieldMeta.info);
    for(const field of fields){
        if(!shouldProbeCardField(field)) continue;
        const value = readDebugProbeField(basePtr, field, inlineValue);
        if(value === null || value === undefined || value === '') continue;
        probe[field.name] = value;
        count++;
        if(count >= maxCount) break;
    }
    if(depth < 1 && count < maxCount){
        for(const field of fields){
            if(count >= maxCount) break;
            if(!shouldRecurseCardField(field)) continue;
            const offsets = getCandidateFieldOffsets(field);
            let fieldStatus = 'no_readable_pointer';
            for(const rawOff of offsets){
                if(count >= maxCount) break;
                const off = inlineValue ? rawOff : field.offset;
                try{
                    const nestedPtr = safeReadPointer(basePtr, off);
                    if(!nestedPtr || nestedPtr.isNull()){
                        fieldStatus = 'null_pointer';
                        continue;
                    }
                    const klass = mono_object_get_class(nestedPtr);
                    const className = klass && !klass.isNull() ? classFullName(klass) : null;
                    if(className) nestedTypes.push(field.name + ':' + className);
                    const nestedMeta = (className ? getFieldInfoForTypeName(className) : null) || getFieldInfoForTypeName(field.type);
                    if(!nestedMeta || !nestedMeta.info){
                        fieldStatus = className ? ('missing_meta:' + className) : 'missing_meta';
                        continue;
                    }
                    const nestedProbe = buildCardDebugProbe(nestedPtr, nestedMeta, false, depth + 1, maxCount - count);
                    if(!nestedProbe){
                        fieldStatus = className ? ('empty_probe:' + className) : 'empty_probe';
                        continue;
                    }
                    fieldStatus = className ? ('read:' + className) : 'read';
                    for(const [nestedKey, nestedValue] of Object.entries(nestedProbe)){
                        if(count >= maxCount) break;
                        const flatKey = field.name + '.' + nestedKey;
                        if(flatKey in probe) continue;
                        probe[flatKey] = nestedValue;
                        count++;
                    }
                    if(className && count < maxCount){
                        probe[field.name + '.__type'] = className;
                        count++;
                    }
                    break;
                }catch(e){}
            }
            if(nestedAttempts.length < 10){
                nestedAttempts.push(field.name + ':' + String(field.type || '') + ':' + fieldStatus);
            }
        }
    }
    const onlyPrimitiveIds =
        count > 0 &&
        Object.keys(probe).every(k => k === 'InstanceId' || k === 'TemplateId');
    if(onlyPrimitiveIds){
        if(nestedAttempts.length) probe.__nested_attempts = nestedAttempts;
        if(nestedTypes.length) probe.__nested_types = Array.from(new Set(nestedTypes)).slice(0, 8);
    }
    return count > 0 ? probe : null;
}

function readMaybeNullableI32(basePtr, field, inlineValue){
    if(!field) return null;
    const offsets = getCandidateFieldOffsets(field);
    for(const rawOff of offsets){
        const off = inlineValue ? rawOff : field.offset;
        try{
            if((field.type || '').startsWith('System.Nullable<')){
                const b = basePtr.add(off);
                if(b.readU8()) return b.add(4).readS32();
                continue;
            }
            return basePtr.add(off).readS32();
        }catch(e){}
    }
    return null;
}

function readPlacementField(basePtr, placementField, names){
    const placementMeta = placementField ? getFieldInfoForTypeName(placementField.type) : null;
    if(!placementMeta || !placementMeta.info) return null;

    for(const off of getCandidateFieldOffsets(placementField)){
        // Try object-reference layout first.
        const placementPtr = safeReadPointer(basePtr, off);
        if(placementPtr && !placementPtr.isNull()){
            try{
                const klass = mono_object_get_class(placementPtr);
                const className = klass && !klass.isNull() ? classFullName(klass) : null;
                if(!placementMeta.fullName || !className || className === placementMeta.fullName){
                    for(const name of names){
                        const nested = placementMeta.info[name];
                        if(!nested) continue;
                        const value = readMaybeNullableI32(placementPtr, nested, false);
                        if(value !== null && value !== undefined) return value;
                    }
                }
            }catch(e){}
        }

        // Fall back to inline valuetype layout.
        const placementBase = basePtr.add(off);
        for(const name of names){
            const nested = placementMeta.info[name];
            if(!nested) continue;
            const value = readMaybeNullableI32(placementBase, nested, true);
            if(value !== null && value !== undefined) return value;
        }
    }

    return null;
}
function readCardFromFieldMap(basePtr, fieldMeta, inlineValue){
    if(!fieldMeta || !fieldMeta.map || !basePtr) return {};
    const fieldMap = fieldMeta.map;
    const fieldInfo = fieldMeta.info || {};
    const fieldOffset = (name) => {
        const raw = fieldMap[name];
        return inlineValue ? normalizeValueTypeOffset(raw) : raw;
    };
    const c={};
    try{
        if('InstanceId' in fieldMap){
            const strPtr=safeReadPointer(basePtr, fieldOffset('InstanceId'));
            if(strPtr && !strPtr.isNull()) c.instance_id=readMonoString(strPtr);
        }
        if('TemplateId' in fieldMap) c.template_id=readGuid(basePtr, fieldOffset('TemplateId'));
        if('Tier' in fieldMap){
            const v=readMaybeNullableI32(basePtr, fieldInfo['Tier'] || {offset: fieldOffset('Tier'), type:''}, inlineValue);
            if(v !== null) c.tier=E_TIER[v]||v;
        }
        if('Type' in fieldMap){
            const v=readMaybeNullableI32(basePtr, fieldInfo['Type'] || {offset: fieldOffset('Type'), type:''}, inlineValue);
            if(v !== null) c.type=E_CARD_TYPE[v]||v;
        }
        if('Size' in fieldMap){
            const v=readMaybeNullableI32(basePtr, fieldInfo['Size'] || {offset: fieldOffset('Size'), type:''}, inlineValue);
            if(v !== null) c.size=E_CARD_SIZE[v]||v;
        }
        if('Owner' in fieldMap){
            try{
                const b=basePtr.add(fieldOffset('Owner'));
                if(b.readU8()){
                    const v=b.add(4).readS32();
                    c.owner=E_COMBATANT[v]||v;
                }else c.owner=null;
            }catch(e){ c.owner=null; }
        }
        if('Socket' in fieldMap){
            try{
                const b=basePtr.add(fieldOffset('Socket'));
                c.socket=b.readU8()?b.add(4).readS32():null;
            }catch(e){ c.socket=null; }
        }
        if('Section' in fieldMap){
            try{
                const b=basePtr.add(fieldOffset('Section'));
                if(b.readU8()){
                    const v=b.add(4).readS32();
                    c.section=E_INVENTORY_SECTION[v]||v;
                }else c.section=null;
            }catch(e){ c.section=null; }
        }
        if((c.owner === undefined || c.owner === null) && fieldInfo['Placement']){
            const ownerVal = readPlacementField(basePtr, fieldInfo['Placement'], ['Owner', '<Owner>k__BackingField', 'CardOwner', 'Combatant']);
            if(ownerVal !== null) c.owner = E_COMBATANT[ownerVal] || ownerVal;
        }
        if((c.socket === undefined || c.socket === null) && fieldInfo['Placement']){
            const socketVal = readPlacementField(basePtr, fieldInfo['Placement'], ['Socket', '<Socket>k__BackingField', 'SocketId', 'BoardSlot', 'Slot', 'Position', 'Index']);
            if(socketVal !== null) c.socket = socketVal;
        }
        if((c.section === undefined || c.section === null) && fieldInfo['Placement']){
            const sectionVal = readPlacementField(basePtr, fieldInfo['Placement'], ['Section', '<Section>k__BackingField', 'InventorySection']);
            if(sectionVal !== null) c.section = E_INVENTORY_SECTION[sectionVal] || sectionVal;
        }
        if((c.type === undefined || c.type === null) && c.instance_id){
            c.type = inferCardTypeFromInstanceId(c.instance_id);
        }
        if(isSuspiciousTemplateId(c.template_id)){
            const probe = buildCardDebugProbe(basePtr, fieldMeta, inlineValue);
            if(probe) c._debug_probe = probe;
            c._debug_source = fieldMeta.fullName || 'field_map';
        }
    }catch(e){
        send({type:'debug',msg:'readCardFromFieldMap:'+e});
    }
    return c;
}
function cardHasUsefulData(card){ return !!(card && (card.instance_id || card.template_id)); }
function readCardFromValueSlot(entryBase, valueField, valueFieldInfo, inlineValue){
    if(!valueField || !valueFieldInfo || !valueFieldInfo.map) return null;
    const offsets = getCandidateFieldOffsets(valueField);
    const expectedClass = valueFieldInfo.fullName || null;

    // First, try treating the slot as an object reference. The latest entry probe
    // suggests SimUpdateCard may be stored as a reference in Dictionary.Entry.
    for(const off of offsets){
        const ptrValue = safeReadPointer(entryBase, off);
        if(!ptrValue || ptrValue.isNull()) continue;
        try{
            const klass = mono_object_get_class(ptrValue);
            const className = klass && !klass.isNull() ? classFullName(klass) : null;
            if(expectedClass && className && className !== expectedClass) continue;
            const card = readCardFromFieldMap(ptrValue, valueFieldInfo, false);
            if(cardHasUsefulData(card)) return card;
        }catch(e){}
    }

    // Fall back to inline valuetype decoding if the slot isn't a reference.
    if(inlineValue){
        for(const off of offsets){
            const card = readCardFromFieldMap(entryBase.add(off), valueFieldInfo, true);
            if(cardHasUsefulData(card)) return card;
        }
    }

    return null;
}

function logCardBucketMiss(card){
    try{
        const key=[card.owner,card.section,card.socket,card.type].join('|');
        cardBucketLogCounts[key]=(cardBucketLogCounts[key]||0)+1;
        if(cardBucketLogCounts[key] <= 5){
            send({type:'debug',msg:'Uncategorized player card owner='+card.owner+' section='+card.section+' socket='+card.socket+' type='+card.type+' instance='+card.instance_id+' template='+card.template_id});
        }
    }catch(e){}
}

function normalizeCombatantOwner(owner){
    if(owner === undefined) return undefined;
    if(owner === null) return null;
    if(owner === 'Player' || owner === 'Opponent') return owner;
    if(owner === 0 || owner === '0') return 'Player';
    if(owner === 1 || owner === '1') return 'Opponent';
    return owner;
}

function logCardCollectionInfo(key, msg){
    try{
        cardCollectionLogCounts[key]=(cardCollectionLogCounts[key]||0)+1;
        if(cardCollectionLogCounts[key] <= 3){
            send({type:'info',msg:msg});
        }
    }catch(e){}
}

function logCardEntryProbe(key, msg){
    try{
        cardCollectionLogCounts[key]=(cardCollectionLogCounts[key]||0)+1;
        if(cardCollectionLogCounts[key] <= 1){
            send({type:'info',msg:msg});
        }
    }catch(e){}
}

function looksLikeCardCollectionField(field){
    if(!field) return false;
    const name=(field.name||'').toLowerCase();
    const type=field.type||'';
    if(name === 'cards' || name.endsWith('cards')) return true;
    if(name.includes('board') || name.includes('stash') || name.includes('skill')) return true;
    return type.includes('CardSnapshotDTO');
}

function findCardCollectionField(objPtr, preferredNames){
    try{
        if(!objPtr || objPtr.isNull()) return null;
        const klass=mono_object_get_class(objPtr);
        if(!klass || klass.isNull()) return null;
        const className=classFullName(klass) || '?';
        const fields=getDynamicFieldsForKlass(klass);
        for(const name of preferredNames || []){
            const field=fields.find(f=>f && f.name === name);
            if(!field) continue;
            const ptrValue=readObjectFieldByInfo(objPtr, field);
            if(ptrValue && !ptrValue.isNull()) return {ptr:ptrValue, field:field, className:className, fields:fields};
        }
        for(const field of fields){
            if(!looksLikeCardCollectionField(field)) continue;
            const ptrValue=readObjectFieldByInfo(objPtr, field);
            if(ptrValue && !ptrValue.isNull()) return {ptr:ptrValue, field:field, className:className, fields:fields};
        }
        logCardCollectionInfo('missing:'+className,'No readable card collection field on '+className+'; fields: '+describeFieldLayout(fields));
    }catch(e){}
    return null;
}

function bucketCard(card, snapshot){
    if(!card || !snapshot)return;
    const owner = normalizeCombatantOwner(card.owner);
    card.owner = owner;

    if(owner === 'Opponent'){
        if(CAPTURE_OPPONENT_BOARD){
            snapshot.opponent_board.push(card);
        }
        return;
    }
    if(owner === null || owner === undefined){
        if(card.type === 'Skill'){
            snapshot.player_skills.push(card);
            return;
        }
        if(card.section === 'Stash' && card.type !== 'Encounter'){
            snapshot.player_stash.push(card);
            return;
        }
        if(card.section === 'Hand'){
            snapshot.player_board.push(card);
            return;
        }
        if(card.type !== 'Encounter'){
            snapshot.offered.push(card);
            return;
        }
        snapshot.offered.push(card);
        return;
    }
    if(owner !== 'Player'){
        logCardBucketMiss(card);
        return;
    }
    if(card.type === 'Skill'){
        snapshot.player_skills.push(card);
        return;
    }
    if(card.section === 'Stash'){
        snapshot.player_stash.push(card);
        return;
    }
    if(card.section === 'Hand' || (card.socket !== null && card.socket !== undefined)){
        snapshot.player_board.push(card);
        return;
    }
    logCardBucketMiss(card);
}

function readStringList(lp){if(!lp||lp.isNull())return[];try{const sz=getManagedArrayLength(lp);if(sz<=0||sz>1000)return[];const base=getManagedArrayDataPtr(lp);const r=[];for(let i=0;i<sz;i++){const ep=base.add(i*Process.pointerSize).readPointer();if(ep&&!ep.isNull())r.push(readMonoString(ep));}return r;}catch(e){return[];}}

function describeFieldLayout(fields){return fields.map(f=>f.name+'@'+f.offset+(f.type?':'+f.type:'')).join(', ');}

function logCollectionLayoutOnce(prefix, klass, fields){try{const className=classFullName(klass)||prefix;const key=prefix+':'+className;collectionLayoutLogCounts[key]=(collectionLayoutLogCounts[key]||0)+1;if(collectionLayoutLogCounts[key]>1)return;send({type:'debug',msg:prefix+' '+className+' fields: '+describeFieldLayout(fields)});}catch(e){}}

function logDictionaryLayoutOnce(prefix, dictKlass, dictFields, entryKlass, entryFields, meta, sampleEntries){try{const dictName=classFullName(dictKlass)||'?';const entryName=classFullName(entryKlass)||'?';const key=prefix+':'+dictName+':'+entryName;collectionLayoutLogCounts[key]=(collectionLayoutLogCounts[key]||0)+1;if(collectionLayoutLogCounts[key]>1)return;let msg=prefix+' dict='+dictName+' {'+describeFieldLayout(dictFields)+'}';msg+=' entry='+entryName+' {'+describeFieldLayout(entryFields)+'}';if(meta){const countText=meta.count===undefined?'?':meta.count;const arrLenText=meta.arrLen===undefined?'?':meta.arrLen;const entrySizeText=meta.entrySize===undefined?'?':meta.entrySize;msg+=' count='+countText+' arrLen='+arrLenText+' entrySize='+entrySizeText;}if(sampleEntries&&sampleEntries.length>0)msg+=' sample=['+sampleEntries.join(', ')+']';send({type:'debug',msg:msg});}catch(e){}}

function findNamedField(fields, names){for(const name of names){const field=fields.find(f=>f.name===name);if(field)return field;}return null;}

function readScalarField(base, field){if(!field)return null;const typeName=field.type||'';if(typeName.startsWith('System.Byte'))return base.add(field.offset).readU8();if(typeName.startsWith('System.SByte'))return base.add(field.offset).readS8();if(typeName.startsWith('System.UInt16'))return base.add(field.offset).readU16();if(typeName.startsWith('System.Int16'))return base.add(field.offset).readS16();if(typeName.startsWith('System.UInt32'))return base.add(field.offset).readU32();return base.add(field.offset).readS32();}

function getManagedArrayLength(arrayPtr){if(!isReadablePointer(arrayPtr))return 0;try{const lenAddr=arrayPtr.add(3*Process.pointerSize);if(!isReadableAddress(lenAddr,4))return 0;return lenAddr.readS32();}catch(e){return 0;}}

function getManagedArrayDataPtr(arrayPtr){if(!isReadablePointer(arrayPtr))return ptr(0);try{const dataPtr=arrayPtr.add(4*Process.pointerSize);return isReadablePointer(dataPtr)?dataPtr:ptr(0);}catch(e){return ptr(0);}}
function readCardDictionary(sp, fields){try{if(DISABLE_DICTIONARY_PROBING)return[];if(!mono_class_get_element_class||!mono_class_value_size)return[];let eO=-1,cO=-1;for(const f of fields){if(f.name==='_entries'||f.name==='entries')eO=f.offset;if(f.name==='_count'||f.name==='count')cO=f.offset;}if(eO<0||cO<0)return[];const ea=safeReadPointer(sp,eO);const count=sp.add(cO).readS32();if(!isReadablePointer(ea)||count<=0)return[];const arrayKlass=mono_object_get_class(ea);if(!arrayKlass||arrayKlass.isNull())return[];const entryKlass=mono_class_get_element_class(arrayKlass);if(!entryKlass||entryKlass.isNull())return[];const entryFields=getFields(entryKlass);const hashField=entryFields.find(f=>f.name==='hashCode'||f.name==='_hashCode');const keyField=entryFields.find(f=>f.name==='key'||f.name==='Key');const valueField=entryFields.find(f=>f.name==='value'||f.name==='Value'||(f.type&&(f.type.includes('CardSnapshotDTO')||f.type.includes('SimUpdateCard'))));if(!valueField)return[];const inlineValue=isInlineCardValueType(valueField.type);const valueFieldInfo=inlineValue?getFieldInfoForTypeName(valueField.type):null;const align=Memory.alloc(4);align.writeU32(0);const entrySize=mono_class_value_size(entryKlass,align);if(!entrySize||entrySize<=0)return[];const arrLen=getManagedArrayLength(ea);const base=getManagedArrayDataPtr(ea);if(!isReadablePointer(base)||arrLen<=0)return[];const cards=[];const limit=Math.min(arrLen,Math.max(count+16,count),500);for(let i=0;i<limit&&cards.length<count;i++){try{const eb=base.add(i*entrySize);let hashValue=null;if(hashField){for(const off of getCandidateFieldOffsets(hashField)){if(isReadableAddress(eb.add(off),4)){hashValue=eb.add(off).readS32();break;}}if(hashValue!==null&&hashValue<0)continue;}const entryKey=keyField?readEntryStringKey(eb,keyField):null;if(cards.length===0&&keyField&&valueField){logCardEntryProbe('entry-probe:'+(valueField.type||'?'),'Entry probe first-live key='+entryKey+' hash='+hashValue+' keyOffset='+keyField.offset+' valueOffset='+valueField.offset+' entrySize='+entrySize+' entryFields='+describeFieldLayout(entryFields));}let card=null;if(inlineValue&&valueFieldInfo&&valueFieldInfo.map){card=readCardFromValueSlot(eb,valueField,valueFieldInfo,true);}else{for(const off of getCandidateFieldOffsets(valueField)){const vp=safeReadPointer(eb,off);if(vp){card=readCardSnapshot(vp);if(cardHasUsefulData(card))break;}}}if(!card&&entryKey){card={instance_id:entryKey,type:inferCardTypeFromInstanceId(entryKey)};}if(card&&(!card.instance_id)&&entryKey){card.instance_id=entryKey;}if(card&&(!card.type)&&card.instance_id){card.type=inferCardTypeFromInstanceId(card.instance_id);}if(card&&card.instance_id)cards.push(card);}catch(e){}}if(cards.length===0&&count>0){const dictKlass=mono_object_get_class(sp);const valueType=valueField.type||'?';logCardCollectionInfo('dict-empty:'+(classFullName(dictKlass)||'?')+':'+valueType,'Card dictionary '+(classFullName(dictKlass)||'?')+' value='+valueType+' count='+count+' yielded 0 cards; fields: '+describeFieldLayout(fields));}return cards;}catch(e){send({type:'debug',msg:'readCardDictionary:'+e});return[];}}
function readCardHashSet(sp){if(!isReadablePointer(sp))return[];try{const klass=mono_object_get_class(sp);const fields=getFields(klass);let sO=-1,cO=-1,lO=-1;for(const f of fields){if(f.name==='_slots'||f.name==='m_slots')sO=f.offset;if(f.name==='_count'||f.name==='m_count')cO=f.offset;if(f.name==='_lastIndex'||f.name==='m_lastIndex')lO=f.offset;}if(sO<0){const dictCards=readCardDictionary(sp,fields);if(dictCards.length>0)return dictCards;logCollectionLayoutOnce('Unsupported card collection',klass,fields);return[];}const sa=safeReadPointer(sp,sO);if(!isReadablePointer(sa))return[];const count=cO>=0?sp.add(cO).readS32():0;const lastIdx=lO>=0?sp.add(lO).readS32():count;if(count<=0)return[];const ml=getManagedArrayLength(sa);const ss=4+4+Process.pointerSize;const base=getManagedArrayDataPtr(sa);if(!isReadablePointer(base)||ml<=0)return[];const cards=[];const lim=Math.min(ml,Math.max(lastIdx,count)+16,500);for(let i=0;i<lim&&cards.length<count;i++){try{const sb=base.add(i*ss);if(isReadableAddress(sb,4)&&sb.readS32()<0)continue;const vp=safeReadPointer(sb,8);if(vp){const card=readCardSnapshot(vp);if(card&&card.instance_id)cards.push(card);}}catch(e){}}if(cards.length===0&&count>0){logCardCollectionInfo('hashset-empty:'+(classFullName(klass)||'?'),'Card set '+(classFullName(klass)||'?')+' count='+count+' lastIndex='+lastIdx+' yielded 0 cards; fields: '+describeFieldLayout(fields));}return cards;}catch(e){send({type:'debug',msg:'readCardHashSet:'+e});return[];}}

function readEnumIntDict(dp, debugLabel, keepKeys, keepCount){if(!isReadablePointer(dp))return{};try{if(!mono_class_get_element_class||!mono_class_value_size){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' missing mono helpers'});return{};}const dictKlass=mono_object_get_class(dp);const dictFields=getFields(dictKlass);const entriesField=findNamedField(dictFields,['_entries','entries']);const countField=findNamedField(dictFields,['_count','count']);if(!entriesField||!countField){if(debugLabel)logCollectionLayoutOnce('Unsupported enum-int dict '+debugLabel,dictKlass,dictFields);return{};}const entriesArray=safeReadPointer(dp,entriesField.offset);const count=dp.add(countField.offset).readS32();if(!isReadablePointer(entriesArray)){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' entries array was null (count='+count+')'});return{};}if(count<=0){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' count='+count});return{};}const arrayKlass=mono_object_get_class(entriesArray);if(!arrayKlass||arrayKlass.isNull()){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' array klass was null'});return{};}const entryKlass=mono_class_get_element_class(arrayKlass);if(!entryKlass||entryKlass.isNull()){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' entry klass was null'});return{};}const entryFields=getFields(entryKlass);const hashField=findNamedField(entryFields,['hashCode','_hashCode']);const keyField=findNamedField(entryFields,['key','Key']);const valueField=findNamedField(entryFields,['value','Value']);if(!keyField||!valueField){if(debugLabel)logDictionaryLayoutOnce('Unsupported enum-int dict '+debugLabel,dictKlass,dictFields,entryKlass,entryFields,{count:count,arrLen:'?',entrySize:'?'},[]);return{};}const align=Memory.alloc(4);align.writeU32(0);const entrySize=mono_class_value_size(entryKlass,align);if(!entrySize||entrySize<=0){if(debugLabel)send({type:'debug',msg:'enum-int dict '+debugLabel+' invalid entry size '+entrySize});return{};}const arrLen=getManagedArrayLength(entriesArray);const base=getManagedArrayDataPtr(entriesArray);if(!isReadablePointer(base)||arrLen<=0)return{};const result={};const samples=[];const limit=Math.min(arrLen,Math.max(count+16,count),500);const useFilter=!!keepKeys;let found=0;let kept=0;for(let i=0;i<limit&&found<count;i++){try{const entryBase=base.add(i*entrySize);if(hashField&&isReadableAddress(entryBase.add(hashField.offset),4)&&entryBase.add(hashField.offset).readS32()<0)continue;const key=readScalarField(entryBase,keyField);const value=readScalarField(entryBase,valueField);found++;if(debugLabel&&samples.length<8)samples.push(key+':'+value);if(useFilter&&!keepKeys[key])continue;result[key]=value;kept++;if(useFilter&&keepCount&&kept>=keepCount)break;}catch(e){}}if(debugLabel)logDictionaryLayoutOnce(debugLabel,dictKlass,dictFields,entryKlass,entryFields,{count:count,arrLen:arrLen,entrySize:entrySize},samples);if(!_playerAttrsDictLayout&&found>0&&entrySize>0){const headerAdj=Math.min(hashField?hashField.offset:999,keyField.offset,valueField.offset);_playerAttrsDictLayout={entriesOff:entriesField.offset,countOff:countField.offset,entrySize:entrySize,hashOff:hashField?(hashField.offset-headerAdj):null,keyOff:keyField.offset-headerAdj,valueOff:valueField.offset-headerAdj,headerAdj:headerAdj};send({type:'info',msg:'QW10 dict layout cached: entriesOff='+entriesField.offset+' countOff='+countField.offset+' entrySize='+entrySize+' hashOff='+(hashField?(hashField.offset-headerAdj):'null')+' keyOff='+(keyField.offset-headerAdj)+' valueOff='+(valueField.offset-headerAdj)+' headerAdj='+headerAdj});}return result;}catch(e){if(debugLabel)send({type:'debug',msg:'readEnumIntDict '+debugLabel+': '+e});return{};}}

function readDynamicRunSnapshot(p){if(!p||p.isNull())return{};const r={};try{const gameModeId=readDynamicGuidField(p,['GameModeId']);if(gameModeId)r.game_mode_id=gameModeId;const day=readDynamicU32Field(p,['Day']);if(day!==null)r.day=day;const hour=readDynamicU32Field(p,['Hour']);if(hour!==null)r.hour=hour;const victories=readDynamicU32Field(p,['Victories']);if(victories!==null)r.victories=victories;const defeats=readDynamicU32Field(p,['Defeats']);if(defeats!==null)r.defeats=defeats;const visitedFates=readDynamicBoolField(p,['HasVisitedFates']);if(visitedFates!==null)r.visited_fates=visitedFates;const dataVersion=readDynamicStringField(p,['DataVersion']);if(dataVersion!==null)r.data_version=dataVersion;}catch(e){send({type:'debug',msg:'readDynRun:'+e});}return r;}

function readDynamicRunStateSnapshot(p){if(!p||p.isNull())return{};const r={};try{const stateInt=readDynamicI32Field(p,['StateName']);if(stateInt!==null){r.state=E_RUN_STATE[stateInt]||('Unknown('+stateInt+')');r.state_int=stateInt;}
const encounterId=readDynamicStringField(p,['CurrentEncounterId']);if(encounterId!==null)r.current_encounter_id=encounterId;
const rerollCost=readDynamicNullableU32Field(p,['RerollCost']);if(rerollCost!==null)r.reroll_cost=rerollCost;
const rerollsRemaining=readDynamicNullableU32Field(p,['RerollsRemaining']);if(rerollsRemaining!==null)r.rerolls_remaining=rerollsRemaining;
// F4: selection_set gated to interesting states
if(ACTION_CARD_STATES[r.state]){const selectionSetPtr=readDynamicObjectField(p,['SelectionSet']);if(selectionSetPtr&&!selectionSetPtr.isNull())r.selection_set=readStringList(selectionSetPtr);}
}catch(e){send({type:'debug',msg:'readDynState:'+e});}return r;}

function readDynamicPlayerSnapshot(p, includeAttributes){if(!p||p.isNull())return{};const r={};try{const heroInt=readDynamicI32Field(p,['Hero']);if(heroInt!==null)r.hero=E_HERO[heroInt]||('Unknown('+heroInt+')');
const unlockedSlots=readDynamicU16Field(p,['UnlockedSlots']);if(unlockedSlots!==null)r.unlocked_slots=unlockedSlots;
// Keep only the live HUD / enrichment attributes to reduce delta cost.
if(includeAttributes){const attrsPtr=readDynamicObjectField(p,['Attributes']);if(attrsPtr&&!attrsPtr.isNull()){const attrs=readEnumIntDict(attrsPtr,'DynamicPlayer.Attributes',KEEP_PLAYER_ATTR_IDS,KEEP_PLAYER_ATTR_COUNT);for(const[k,v]of Object.entries(attrs))r[E_PLAYER_ATTRIBUTE[parseInt(k)]||('attr_'+k)]=v;}else send({type:'debug',msg:'DynamicPlayer.Attributes pointer was null'});}
}catch(e){send({type:'debug',msg:'readDynPlayer:'+e});}return r;}

function readDynamicPlayerLean(p){if(!p||p.isNull())return{};const r={};try{const heroInt=readDynamicI32Field(p,['Hero']);if(heroInt!==null)r.hero=E_HERO[heroInt]||('Unknown('+heroInt+')');const unlockedSlots=readDynamicU16Field(p,['UnlockedSlots']);if(unlockedSlots!==null)r.unlocked_slots=unlockedSlots;}catch(e){send({type:'debug',msg:'readDynPlayerLean:'+e});}return r;}

// =====================================================================
// QW9: Fast GameSim path — all five optimizations in one block
// =====================================================================

// QW9-FIX3: Batch field reader. Resolves all field offsets for a class once,
// caches them, and reads multiple fields from the same object in a single pass
// without repeated mono_object_get_class / getDynamicFieldsForKlass calls.
//
// QW10: Uses direct pointer read for class lookup instead of mono_object_get_class
// NativeFunction call. In Mono, the class pointer is stored at object+0 (the vtable
// pointer, first field of MonoObject). We read it directly to avoid the ~3-5ms
// NativeFunction bridge overhead.
const _klassNameCache = new Map(); // klass ptr → className string
function _getBatchOffsets(objPtr) {
    try {
        if (!objPtr || objPtr.isNull()) return null;
        // QW10: direct class pointer read — avoids mono_object_get_class NativeFunction.
        // MonoObject layout: { MonoVTable *vtable; ... }
        // MonoVTable layout: { MonoClass *klass; ... }
        // So klass = *(*(objPtr + 0) + 0) — double dereference.
        const vtable = objPtr.readPointer();
        if (!vtable || vtable.isNull()) return null;
        const klass = vtable.readPointer();
        if (!klass || klass.isNull()) return null;
        const klassKey = klass.toString();
        // Check if we already have the field map for this class pointer
        let className = _klassNameCache.get(klassKey);
        if (className === undefined) {
            // First time seeing this class pointer — resolve name via Mono API (once)
            className = classFullName(klass);
            _klassNameCache.set(klassKey, className || '');
        }
        if (!className) return null;
        if (_batchFieldOffsetCache[className]) return _batchFieldOffsetCache[className];
        const fields = getDynamicFieldsForKlass(klass);
        const map = {};
        for (const f of fields) {
            if (f && f.name) map[f.name] = f;
        }
        _batchFieldOffsetCache[className] = map;
        // One-time diagnostic: dump the resolved field map for any Player-related
        // class so we can verify Hero / UnlockedSlots / Attributes are present.
        // Fires once per className thanks to the cache check above.
        if (className.indexOf('Player') !== -1) {
            const summary = fields.map(function(f){ return f.name + '@' + f.offset + ':' + (f.type || '?'); }).join(', ');
            send({type:'info', msg:'player-class fields ' + className + ' [' + fields.length + ']: ' + summary});
        }
        return map;
    } catch (e) { return null; }
}

// QW10: Direct pointer reads — eliminates mono_field_get_value NativeFunction calls.
// For reference-type fields in Mono managed objects, the field value is a pointer
// stored at (objPtr + field.offset). We read it directly instead of calling
// mono_field_get_value which has ~3-5ms overhead per call on Windows.
// Safe because we're on the game thread (GC can't move objects during our hook).
function _fastReadI32(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readS32();
    } catch (e) { return null; }
}
function _fastReadU32(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readU32();
    } catch (e) { return null; }
}
function _fastReadU16(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readU16();
    } catch (e) { return null; }
}
function _fastReadBool(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return objPtr.add(f.offset).readU8() !== 0;
    } catch (e) { return null; }
}
function _fastReadObjPtr(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        // QW10: direct pointer read at offset — replaces mono_field_get_value + Memory.alloc
        const value = objPtr.add(f.offset).readPointer();
        return (value && !value.isNull()) ? value : null;
    } catch (e) { return null; }
}
function _fastReadString(objPtr, fieldMap, name) {
    try {
        const strPtr = _fastReadObjPtr(objPtr, fieldMap, name);
        return strPtr ? _directReadMonoString(strPtr) : null;
    } catch (e) { return null; }
}
function _fastReadGuid(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        return readGuid(objPtr, f.offset);
    } catch (e) { return null; }
}
function _fastReadNullableU32(objPtr, fieldMap, name) {
    try {
        const f = fieldMap[name];
        if (!f) return null;
        const base = objPtr.add(f.offset);
        return base.readU8() ? base.add(4).readU32() : null;
    } catch (e) { return null; }
}

// QW10: Fast player attrs reader — uses cached dictionary layout for pure direct
// memory reads with ZERO NativeFunction calls. After readEnumIntDict succeeds once
// and populates _playerAttrsDictLayout, this function replaces it entirely.
// Reads the managed Dictionary<PlayerAttribute, int> entries array directly:
//   dict + entriesOff → entries array pointer
//   dict + countOff → entry count
//   entries + MONO_ARRAY_HEADER → data start
//   data + i*entrySize + keyOff/valueOff → key/value pairs
// Skips tombstones (hashCode < 0). Returns the same {key: value} format.
const MONO_ARRAY_HEADER_64 = 16; // MonoArray header: 8 (vtable) + 4 (max_length) + 4 (pad)
function _fastReadPlayerAttrs(dictPtr, keepKeys, keepCount) {
    const layout = _playerAttrsDictLayout;
    if (!layout || !dictPtr) return null;
    try {
        // Read entries array pointer directly from dict object
        const entriesArray = dictPtr.add(layout.entriesOff).readPointer();
        if (!entriesArray || entriesArray.isNull()) { _fastAttrsFailCount++; return null; }
        const count = dictPtr.add(layout.countOff).readS32();
        if (count <= 0 || count > 500) { _fastAttrsFailCount++; return null; }
        // Array data starts after managed array header
        const base = entriesArray.add(MONO_ARRAY_HEADER_64);
        const result = {};
        let found = 0;
        let kept = 0;
        const limit = Math.min(count + 16, 500);
        for (let i = 0; i < limit && found < count; i++) {
            const entryBase = base.add(i * layout.entrySize);
            // Check tombstone
            if (layout.hashOff !== null) {
                const hash = entryBase.add(layout.hashOff).readS32();
                if (hash < 0) continue;
            }
            const key = entryBase.add(layout.keyOff).readS32();
            found++;
            if (keepKeys && !keepKeys[key]) continue;
            const value = entryBase.add(layout.valueOff).readS32();
            result[key] = value;
            kept++;
            if (keepCount && kept >= keepCount) break;
        }
        _fastAttrsReadCount++;
        return result;
    } catch (e) {
        _fastAttrsFailCount++;
        return null;
    }
}

// QW10: Content-hash SelectionSet cache. The game allocates a new array each
// tick (pointer always changes), so pointer-identity caching never hits.
// Instead, fingerprint the array by reading the raw element pointers — if the
// same string objects are referenced in the same order, the content is identical.
// This avoids mono_string_to_utf8 + mono_free calls (2 NativeFunction calls per string).
let _lastSelSetFingerprint = null;
function _readSelectionSetCached(statePtr, fieldMap) {
    try {
        const selPtr = _fastReadObjPtr(statePtr, fieldMap, 'SelectionSet');
        if (!selPtr || selPtr.isNull()) return [];
        // Managed array: header(16 bytes) then element pointers
        const len = selPtr.add(8).readS32(); // max_length at offset 8 in MonoArray
        if (len <= 0 || len > 1000) return [];
        // Build fingerprint from element pointers (8 bytes each on 64-bit)
        const dataStart = selPtr.add(MONO_ARRAY_HEADER_64);
        let fingerprint = '' + len;
        const fpLen = Math.min(len, 8); // fingerprint first 8 elements max
        for (let i = 0; i < fpLen; i++) {
            fingerprint += '|' + dataStart.add(i * Process.pointerSize).readPointer().toString();
        }
        if (fingerprint === _lastSelSetFingerprint) {
            _selectionSetCacheHits++;
            return _lastSelectionSetResult;
        }
        // Cache miss: decode strings directly (no readStringList → readMonoString)
        _selectionSetCacheMisses++;
        const result = [];
        for (let i = 0; i < len; i++) {
            const ep = dataStart.add(i * Process.pointerSize).readPointer();
            if (ep && !ep.isNull()) result.push(_directReadMonoString(ep));
        }
        _lastSelSetFingerprint = fingerprint;
        _lastSelectionSetResult = result;
        return result;
    } catch (e) { return []; }
}

// QW9-FIX1+2: Unified GameSim reader. Merges readDynamicStateLean +
// readDynamicStatePayload into a single pass. Reads Run/State/Player exactly
// once. Player attributes are read synchronously but throttled to state changes
// (eliminates the 87% deferred-failure cascade).
function readGameSimFast(dataPtr, includeCards, includePlayerAttrs, includeTemplateEvents) {
    if (!dataPtr || dataPtr.isNull()) return null;
    const r = {run:{}, state:{}, player:{}, offered:[], player_board:[], player_stash:[], player_skills:[], opponent_board:[]};
    let sawAny = false;
    try {
        // --- Batch-read dataPtr fields once ---
        const dataFields = _getBatchOffsets(dataPtr);
        if (!dataFields) return null;

        // --- Run ---
        const runPtr = _fastReadObjPtr(dataPtr, dataFields, 'Run');
        if (runPtr) {
            const runFields = _getBatchOffsets(runPtr);
            if (runFields) {
                const day = _fastReadU32(runPtr, runFields, 'Day');
                if (day !== null) r.run.day = day;
                const hour = _fastReadU32(runPtr, runFields, 'Hour');
                if (hour !== null) r.run.hour = hour;
                const victories = _fastReadU32(runPtr, runFields, 'Victories');
                if (victories !== null) r.run.victories = victories;
                const defeats = _fastReadU32(runPtr, runFields, 'Defeats');
                if (defeats !== null) r.run.defeats = defeats;
                const gameModeId = _fastReadGuid(runPtr, runFields, 'GameModeId');
                if (gameModeId) r.run.game_mode_id = gameModeId;
                const visitedFates = _fastReadBool(runPtr, runFields, 'HasVisitedFates');
                if (visitedFates !== null) r.run.visited_fates = visitedFates;
                // QW10: DataVersion is static per run — cache after first read
                if (_cachedDataVersion !== null) {
                    r.run.data_version = _cachedDataVersion;
                } else {
                    const dataVersion = _fastReadString(runPtr, runFields, 'DataVersion');
                    if (dataVersion !== null) { r.run.data_version = dataVersion; _cachedDataVersion = dataVersion; }
                }
                sawAny = true;
            }
        }

        // --- State ---
        const statePtr = _fastReadObjPtr(dataPtr, dataFields, 'CurrentState');
        if (statePtr) {
            const stateFields = _getBatchOffsets(statePtr);
            if (stateFields) {
                const stateInt = _fastReadI32(statePtr, stateFields, 'StateName');
                if (stateInt !== null) {
                    r.state.state = E_RUN_STATE[stateInt] || ('Unknown(' + stateInt + ')');
                    r.state.state_int = stateInt;
                }
                // QW10: Only read CurrentEncounterId string on action states (saves 2 NativeFunction calls)
                if (ACTION_CARD_STATES[r.state.state]) {
                    const encounterId = _fastReadString(statePtr, stateFields, 'CurrentEncounterId');
                    if (encounterId !== null) r.state.current_encounter_id = encounterId;
                }
                const rerollCost = _fastReadNullableU32(statePtr, stateFields, 'RerollCost');
                if (rerollCost !== null) r.state.reroll_cost = rerollCost;
                const rerollsRemaining = _fastReadNullableU32(statePtr, stateFields, 'RerollsRemaining');
                if (rerollsRemaining !== null) r.state.rerolls_remaining = rerollsRemaining;
                // QW9-FIX5: cached SelectionSet — only decode strings on pointer change
                if (ACTION_CARD_STATES[r.state.state]) {
                    r.state.selection_set = _readSelectionSetCached(statePtr, stateFields);
                }
                sawAny = true;
            }
        }

        // --- Player ---
        const playerPtr = _fastReadObjPtr(dataPtr, dataFields, 'Player');
        if (playerPtr) {
            const playerFields = _getBatchOffsets(playerPtr);
            if (playerFields) {
                const heroInt = _fastReadI32(playerPtr, playerFields, 'Hero');
                if (heroInt !== null) r.player.hero = E_HERO[heroInt] || ('Unknown(' + heroInt + ')');
                const unlockedSlots = _fastReadU16(playerPtr, playerFields, 'UnlockedSlots');
                if (unlockedSlots !== null) r.player.unlocked_slots = unlockedSlots;

                // QW10: Fast player attrs — uses cached dict layout for direct memory
                // reads (ZERO NativeFunction calls). Falls back to readEnumIntDict
                // only until the layout is cached on first successful read.
                // Throttled: only attempt on state change or interval expiry.
                // Cached attrs applied on every snapshot regardless.
                if (includePlayerAttrs && ATTRS_THROTTLE_ON_STATE_CHANGE) {
                    const now = Date.now();
                    const currStateName = r.state.state || null;
                    const stateChanged = currStateName !== _lastAttrsSyncState;
                    const intervalElapsed = (now - _lastAttrsSyncMs) >= ATTRS_SYNC_MIN_INTERVAL_MS;
                    let freshAttrs = null;
                    if (stateChanged || intervalElapsed) {
                        const attrsPtr = _fastReadObjPtr(playerPtr, playerFields, 'Attributes');
                        if (attrsPtr) {
                            let attrsDict = null;
                            // Use fast direct reader if layout is cached, else slow path
                            if (_playerAttrsDictLayout) {
                                attrsDict = _fastReadPlayerAttrs(attrsPtr, KEEP_PLAYER_ATTR_IDS, KEEP_PLAYER_ATTR_COUNT);
                            }
                            if (!attrsDict) {
                                // Slow path — also populates _playerAttrsDictLayout on success.
                                // Pass a debugLabel so the once-only sample logger fires; this
                                // is the only way to confirm whether keys 9 (Prestige) and 15
                                // (Level) are present in the managed dict during mid-run pickup.
                                attrsDict = readEnumIntDict(attrsPtr, 'fast-PlayerAttributes', KEEP_PLAYER_ATTR_IDS, KEEP_PLAYER_ATTR_COUNT);
                            }
                            if (attrsDict) {
                                const resolved = {};
                                for (const [k, v] of Object.entries(attrsDict)) {
                                    resolved[E_PLAYER_ATTRIBUTE[parseInt(k)] || ('attr_' + k)] = v;
                                }
                                if (Object.keys(resolved).length > 0) {
                                    freshAttrs = resolved;
                                    // Merge (don't overwrite): a single read can return a partial
                                    // subset of KEEP_PLAYER_ATTR_IDS — e.g. early in a run the
                                    // managed dict may not yet contain key 9 (Prestige) or 15 (Level).
                                    // Wholesale overwrite would lock in that partial shape forever.
                                    _lastGoodAttrs = Object.assign({}, _lastGoodAttrs || {}, resolved);
                                } else {
                                    _attrsSyncEmptyCount++;
                                }
                            }
                            _attrsSyncReadCount++;
                        }
                        _lastAttrsSyncMs = now;
                        _lastAttrsSyncState = currStateName;
                    } else {
                        _attrsSyncThrottledCount++;
                    }
                    // Apply: fresh result if we got one, otherwise last known good
                    const attrsToApply = freshAttrs || _lastGoodAttrs;
                    if (attrsToApply) {
                        for (const [k, v] of Object.entries(attrsToApply)) {
                            r.player[k] = v;
                        }
                        if (!freshAttrs) _attrsFromCacheCount++;
                    }
                } else if (includePlayerAttrs) {
                    // Legacy deferred path (ATTRS_THROTTLE_ON_STATE_CHANGE = false)
                    const wantAttrsSync = _pendingSyncAttrsRead;
                    if (wantAttrsSync) { _pendingSyncAttrsRead = false; _syncAttrsFallbackCount++; }
                    if (wantAttrsSync) {
                        const attrsPtr = _fastReadObjPtr(playerPtr, playerFields, 'Attributes');
                        if (attrsPtr) {
                            const attrs = readEnumIntDict(attrsPtr, 'legacy-PlayerAttributes', KEEP_PLAYER_ATTR_IDS, KEEP_PLAYER_ATTR_COUNT);
                            for (const [k, v] of Object.entries(attrs)) {
                                r.player[E_PLAYER_ATTRIBUTE[parseInt(k)] || ('attr_' + k)] = v;
                            }
                        }
                    } else {
                        dispatchDeferredPlayerAttrs(playerPtr, snapshotCounter + 1);
                    }
                }
                sawAny = true;
            }
        }

        // --- Template events (deferred, gated by state) ---
        if (includeTemplateEvents && shouldReadActionTemplateEvents(r)) {
            const eventsPtr = _fastReadObjPtr(dataPtr, dataFields, 'Events');
            if (eventsPtr) {
                const _snapshotId = snapshotCounter + 1;
                setImmediate(function() {
                    try {
                        const templateEvents = readGameSimTemplateEventsFromList(eventsPtr);
                        if (templateEvents.length > 0) {
                            send({type:'deferred_template_events', snapshot_id:_snapshotId, card_template_events:templateEvents});
                        }
                    } catch (e) { send({type:'debug', msg:'deferred-template-events:' + e}); }
                });
            }
        }

        // --- Cards (deferred, gated by state) ---
        const _wantCards = includeCards && (FULL_DELTA_CARDS ? shouldReadHeavyCards(r, false) : shouldReadActionCards(r));
        if (_wantCards) {
            const cardRef = findCardCollectionField(dataPtr, ['Cards']);
            const cardCollectionPtr = cardRef ? cardRef.ptr : null;
            if (cardCollectionPtr && !cardCollectionPtr.isNull()) {
                const _snapshotId = snapshotCounter + 1;
                setImmediate(function() {
                    try {
                        const cards = readCardHashSet(cardCollectionPtr);
                        if (cards.length > 0) {
                            const deferred = {offered:[], player_board:[], player_stash:[], player_skills:[], opponent_board:[]};
                            for (const c of cards) bucketCard(c, deferred);
                            send({type:'deferred_cards', snapshot_id:_snapshotId, cards:deferred});
                        } else {
                            logCardCollectionInfo('deferred-fast-empty:' + (cardRef.className || '?'),
                                'Fast-path deferred card collection ' + cardRef.className + ' yielded 0 cards');
                        }
                    } catch (e) { send({type:'debug', msg:'deferred-fast-cards:' + e}); }
                });
                sawAny = true;
            }
        }
    } catch (e) {
        send({type:'debug', msg:'readGameSimFast:' + e});
    }
    return sawAny ? r : null;
}

// =====================================================================
// End QW9
// =====================================================================

function cloneDynamicSnapshotBase(baseSnapshot){return{run:Object.assign({},(baseSnapshot&&baseSnapshot.run)||{}),state:Object.assign({},(baseSnapshot&&baseSnapshot.state)||{}),player:Object.assign({},(baseSnapshot&&baseSnapshot.player)||{}),offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};}

function hasInterestingSelectionSet(state){return !!(state&&state.selection_set&&state.selection_set.some(v=>v!==null&&v!==''));}
function selectionSetValues(stateObj){
    return (stateObj&&Array.isArray(stateObj.selection_set)) ? stateObj.selection_set : [];
}
function isMapLikeSelectionId(value){
    const v=String(value||'');
    return v.startsWith('enc_')||v.startsWith('ste_')||v.startsWith('com_')||v.startsWith('ped_')||v.startsWith('pvp_');
}
function isCardLikeSelectionId(value){
    const v=String(value||'');
    return v.startsWith('itm_')||v.startsWith('skl_')||v.startsWith('com_');
}

function shouldAllowInlineCardRead(snapshot, forceFull){
    if(FULL_DELTA_CARDS) return true;
    if(!snapshot) return false;
    const stateName=snapshot.state&&snapshot.state.state;
    if(!stateName||!INLINE_CARD_STATES[stateName]) return false;
    const selection=selectionSetValues(snapshot.state);
    if(selection.length===0) return false;
    if(selection.length>MAX_INLINE_CARD_COUNT) return false;
    const hasMapLike=selection.some(isMapLikeSelectionId);
    if(hasMapLike) return false;
    const hasCardLike=selection.some(isCardLikeSelectionId);
    if(!hasCardLike && stateName !== "LevelUp" && stateName !== "Loot") return false;
    if(forceFull && (stateName === "EndRunVictory" || stateName === "EndRunDefeat")) return true;
    return true;
}
function shouldForceHeavySnapshot(snapshot){
    if(!snapshot) return false;
    const stateName=snapshot.state&&snapshot.state.state;
    if(stateName&&HEAVY_CARD_STATES[stateName]) return true;
    if(hasInterestingSelectionSet(snapshot.state)) return true;
    return false;
}
function shouldReadHeavyCards(snapshot, forceFull){
    if(forceFull) return true;
    if(!snapshot||!snapshot.state) return false;
    const stateName=snapshot.state.state||snapshot.state;
    return !!(HEAVY_CARD_STATES[stateName]);
}
function shouldReadActionCards(snapshot){
    if(!ACTION_EVENT_CARDS) return false;
    if(!snapshot||!snapshot.state) return false;
    const stateName=snapshot.state.state||snapshot.state;
    return !!(ACTION_CARD_STATES[stateName]);
}
function shouldReadActionTemplateEvents(snapshot){
    if(!snapshot||!snapshot.state) return false;
    const stateName=snapshot.state.state||snapshot.state;
    return !!(ACTION_TEMPLATE_EVENT_STATES[stateName]);
}
function readDynamicStateLean(dataPtr){if(!dataPtr||dataPtr.isNull())return null;const r={run:{},state:{},player:{},offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};let sawAny=false;try{const runPtr=readDynamicObjectField(dataPtr,['Run']);if(runPtr&&!runPtr.isNull()){const day=readDynamicU32Field(runPtr,['Day']);if(day!==null)r.run.day=day;const hour=readDynamicU32Field(runPtr,['Hour']);if(hour!==null)r.run.hour=hour;const victories=readDynamicU32Field(runPtr,['Victories']);if(victories!==null)r.run.victories=victories;const defeats=readDynamicU32Field(runPtr,['Defeats']);if(defeats!==null)r.run.defeats=defeats;sawAny=true;}const statePtr=readDynamicObjectField(dataPtr,['CurrentState']);if(statePtr&&!statePtr.isNull()){const stateInt=readDynamicI32Field(statePtr,['StateName']);if(stateInt!==null){r.state.state=E_RUN_STATE[stateInt]||('Unknown('+stateInt+')');r.state.state_int=stateInt;}const rerollCost=readDynamicNullableU32Field(statePtr,['RerollCost']);if(rerollCost!==null)r.state.reroll_cost=rerollCost;const rerollsRemaining=readDynamicNullableU32Field(statePtr,['RerollsRemaining']);if(rerollsRemaining!==null)r.state.rerolls_remaining=rerollsRemaining;const selectionSetPtr=readDynamicObjectField(statePtr,['SelectionSet']);if(selectionSetPtr&&!selectionSetPtr.isNull())r.state.selection_set=readStringList(selectionSetPtr);sawAny=true;}const playerPtr=readDynamicObjectField(dataPtr,['Player']);if(playerPtr&&!playerPtr.isNull()){r.player=readDynamicPlayerLean(playerPtr);sawAny=true;}}catch(e){send({type:'debug',msg:'readDynamicStateLean:'+e});}return sawAny?r:null;}

// Defer heavy Player.Attributes enumeration off the game thread.
// The managed dict walk (20-38 entries * range-checked memory reads) dominates
// hook latency on NetMessageGameSim — 91% of slow hooks. Capture the pointer
// cheaply on the game thread, decode in setImmediate, ship as deferred_player_attrs.
// On empty/exception, flip _pendingSyncAttrsRead so the next hook falls back to
// sync and we don't silently drop attrs forever.
//
// HITCHING FOLLOW-UP (roadmap section 5, Short-Term): if live runs show
// remaining GameSim hitching even with this deferred path in place,
// throttle the dispatch here — e.g., skip calling dispatchDeferredPlayerAttrs
// when (Date.now() - _lastDeferredAttrsDispatchMs) < N ms, or coalesce
// pointer captures by snapshot state. Do not speculatively throttle
// without a reproducible hitch — KEEP_PLAYER_ATTR_IDS already narrows the
// managed dict walk to 5 live attrs (Gold, Health, HealthMax, Level,
// Prestige), which is the cheapest read that still feeds the overlay.
function dispatchDeferredPlayerAttrs(playerPtr,snapshotId){try{const attrsPtr=readDynamicObjectField(playerPtr,['Attributes']);if(!attrsPtr||attrsPtr.isNull())return false;setImmediate(function(){try{const attrsDict=readEnumIntDict(attrsPtr,null,KEEP_PLAYER_ATTR_IDS,KEEP_PLAYER_ATTR_COUNT);const attrs={};for(const[k,v]of Object.entries(attrsDict))attrs[E_PLAYER_ATTRIBUTE[parseInt(k)]||('attr_'+k)]=v;const attrCount=Object.keys(attrs).length;if(attrCount>0){_deferredAttrsSuccessCount++;send({type:'deferred_player_attrs',snapshot_id:snapshotId,attrs:attrs});}else{_deferredAttrsFailureCount++;_pendingSyncAttrsRead=true;}maybeReportAttrsStats();}catch(e){_deferredAttrsFailureCount++;_pendingSyncAttrsRead=true;send({type:'debug',msg:'deferred-player-attrs:'+e});}});return true;}catch(e){return false;}}

function maybeReportAttrsStats(){const now=Date.now();if(now-_lastAttrsStatReportMs<ATTRS_STAT_REPORT_INTERVAL_MS)return;_lastAttrsStatReportMs=now;if(FAST_GAMESIM_PATH&&ATTRS_THROTTLE_ON_STATE_CHANGE){send({type:'info',msg:'QW10 attrs stats: sync_reads='+_attrsSyncReadCount+' throttled='+_attrsSyncThrottledCount+' empty='+_attrsSyncEmptyCount+' from_cache='+_attrsFromCacheCount+' fast_dict='+_fastAttrsReadCount+' fast_dict_fail='+_fastAttrsFailCount+' dict_layout='+(!!_playerAttrsDictLayout)+' selset_hits='+_selectionSetCacheHits+' selset_misses='+_selectionSetCacheMisses});return;}const total=_deferredAttrsSuccessCount+_deferredAttrsFailureCount;if(total===0)return;const failureRate=(_deferredAttrsFailureCount/total*100).toFixed(1);send({type:'info',msg:'deferred_player_attrs stats: success='+_deferredAttrsSuccessCount+' failure='+_deferredAttrsFailureCount+' sync_fallback='+_syncAttrsFallbackCount+' failure_rate='+failureRate+'%'});}
// includeCards=true now grabs only the collection pointer on the game thread, then defers heavy decode
function readDynamicStatePayload(dataPtr, includeCards, includePlayerAttrs, includeTemplateEvents, baseSnapshot){if(!dataPtr||dataPtr.isNull())return null;const r=cloneDynamicSnapshotBase(baseSnapshot);let sawAny=!!baseSnapshot;try{const runPtr=readDynamicObjectField(dataPtr,['Run']);if(runPtr&&!runPtr.isNull()){if(!baseSnapshot||!baseSnapshot.run||Object.keys(baseSnapshot.run).length===0)r.run=readDynamicRunSnapshot(runPtr);sawAny=true;}const statePtr=readDynamicObjectField(dataPtr,['CurrentState']);if(statePtr&&!statePtr.isNull()){if(!baseSnapshot||!baseSnapshot.state||Object.keys(baseSnapshot.state).length===0){r.state=readDynamicRunStateSnapshot(statePtr);}else{const encounterId=readDynamicStringField(statePtr,['CurrentEncounterId']);if(encounterId!==null)r.state.current_encounter_id=encounterId;}sawAny=true;}const playerPtr=readDynamicObjectField(dataPtr,['Player']);if(playerPtr&&!playerPtr.isNull()){const needFullPlayer=includePlayerAttrs||!baseSnapshot||!baseSnapshot.player||Object.keys(baseSnapshot.player).length===0;if(needFullPlayer){const wantAttrsSync=includePlayerAttrs&&_pendingSyncAttrsRead;if(wantAttrsSync){_pendingSyncAttrsRead=false;_syncAttrsFallbackCount++;}r.player=Object.assign({},r.player,readDynamicPlayerSnapshot(playerPtr,wantAttrsSync));if(includePlayerAttrs&&!wantAttrsSync)dispatchDeferredPlayerAttrs(playerPtr,snapshotCounter+1);}sawAny=true;}if(includeTemplateEvents){const eventsPtr=readDynamicObjectField(dataPtr,['Events']);if(eventsPtr&&!eventsPtr.isNull()){const _snapshotId=snapshotCounter+1;setImmediate(function(){try{const templateEvents=readGameSimTemplateEventsFromList(eventsPtr);if(templateEvents.length>0){send({type:'deferred_template_events',snapshot_id:_snapshotId,card_template_events:templateEvents});}}catch(e){send({type:'debug',msg:'deferred-template-events:'+e});}});}}if(includeCards){// Capture-and-release: grab pointer on game thread, decode in setImmediate
const cardRef=findCardCollectionField(dataPtr,['Cards']);const cardCollectionPtr=cardRef?cardRef.ptr:null;if(cardCollectionPtr&&!cardCollectionPtr.isNull()){const _snapshotId=snapshotCounter+1;// will match snap.id after hookMethod increments
setImmediate(function(){try{const cards=readCardHashSet(cardCollectionPtr);if(cards.length>0){const deferred={offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};for(const c of cards)bucketCard(c,deferred);send({type:'deferred_cards',snapshot_id:_snapshotId,cards:deferred});}else{logCardCollectionInfo('deferred-dynamic-empty:'+(cardRef.className||'?'),'Deferred dynamic card collection '+cardRef.className+' yielded 0 cards');}}catch(e){send({type:'debug',msg:'deferred-dynamic-cards:'+e});}});sawAny=true;}}}catch(e){send({type:'debug',msg:'readDynamicStatePayload:'+e});}return sawAny?r:null;}
function readGameStateSnapshot(sp, includeCards){if(!sp||sp.isNull())return null;const r={run:{},state:{},player:{},offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};try{const runPtr=readObjectField(sp,'GameStateSnapshotDTO','Run');const statePtr=readObjectField(sp,'GameStateSnapshotDTO','CurrentState');const playerPtr=readObjectField(sp,'GameStateSnapshotDTO','Player');if(runPtr&&!runPtr.isNull())r.run=readRunSnapshot(runPtr);else logNullField('GameStateSnapshotDTO.Run','was null');if(statePtr&&!statePtr.isNull())r.state=readRunStateSnapshot(statePtr);else logNullField('GameStateSnapshotDTO.CurrentState','was null');if(playerPtr&&!playerPtr.isNull())r.player=readPlayerSnapshot(playerPtr);else logNullField('GameStateSnapshotDTO.Player','was null');if(includeCards){// Capture-and-release: grab pointer on game thread, decode in setImmediate
const cardRef=findCardCollectionField(sp,['Cards']);const cardCollectionPtr=cardRef?cardRef.ptr:null;if(cardCollectionPtr&&!cardCollectionPtr.isNull()){const _snapshotId=snapshotCounter+1;// will match snap.id after hookMethod increments
setImmediate(function(){try{const cards=readCardHashSet(cardCollectionPtr);if(cards.length>0){const deferred={offered:[],player_board:[],player_stash:[],player_skills:[],opponent_board:[]};for(const c of cards)bucketCard(c,deferred);send({type:'deferred_cards',snapshot_id:_snapshotId,cards:deferred});}else{logCardCollectionInfo('deferred-snapshot-empty:'+(cardRef.className||'?'),'Deferred snapshot card collection '+(cardRef.className||'?')+' yielded 0 cards');}}catch(e){send({type:'debug',msg:'deferred-snapshot-cards:'+e});}});}else logNullField('GameStateSnapshotDTO.Cards','was null');}}catch(e){send({type:'debug',msg:'readSnapshot:'+e});}return r;}

// Hooking

// QW3: Cache getArgClassName by argPtr string (same method always has same param types)
const _argClassNameCache = new Map();
function getArgClassName(argPtr){
    try{
        if(!argPtr||argPtr.isNull())return null;
        const key=argPtr.toString();
        const cached=_argClassNameCache.get(key);
        if(cached!==undefined)return cached||null;
        if(Process.findRangeByAddress(argPtr)===null){_argClassNameCache.set(key,'');return null;}
        const klass=mono_object_get_class(argPtr);
        if(!klass||klass.isNull()){_argClassNameCache.set(key,'');return null;}
        if(Process.findRangeByAddress(klass)===null){_argClassNameCache.set(key,'');return null;}
        const name=classFullName(klass);
        _argClassNameCache.set(key,name||'');
        return name;
    }catch(e){return null;}
}

function inspectArgs(method,args){const matches=[];const maxArgs=Math.min(6,Math.max(3,method.paramCount+2));for(let i=0;i<maxArgs;i++){const argPtr=args[i];const className=getArgClassName(argPtr);if(className)matches.push({index:i,ptr:argPtr,className:className});}const key=method.name+'/'+method.paramCount;if(matches.length>0&&(argLogCounts[key]||0)<5){argLogCounts[key]=(argLogCounts[key]||0)+1;send({type:'debug',msg:formatMethod(method)+' arg objects: '+matches.map(m=>'arg'+m.index+'='+m.className).join(', ')});}return matches;}

function buildSnapshotHints(method){
    const hints=[];
    for(let i=0;i<method.params.length;i++){
        const paramType=method.params[i]||'';
        if(paramType.includes('NetMessageGameStateSync')||paramType.includes('GameStateSnapshot')||paramType.includes('NetMessageCombatSim')||paramType.includes('NetMessageGameSim')||paramType.includes('NetMessageRunInitialized')){
            hints.push({runtimeIndex:i+1,paramType:paramType});
        }
    }
    return hints;
}

function buildCommandHints(method){
    const hints=[];
    for(let i=0;i<method.params.length;i++){
        const paramType=method.params[i]||'';
        if(isCommandParamType(paramType)){
            hints.push({runtimeIndex:i+1,paramType:paramType});
        }
    }
    return hints;
}

function matchHintedArgs(args,hints,isRelevant){
    const matches=[];
    const seen={};
    for(const hint of hints||[]){
        const candidateIndexes=[hint.runtimeIndex];
        if(hint.runtimeIndex>0)candidateIndexes.push(hint.runtimeIndex-1);
        for(const runtimeIndex of candidateIndexes){
            if(runtimeIndex<0)continue;
            const argPtr=args[runtimeIndex];
            if(!argPtr||argPtr.isNull())continue;
            const className=getArgClassName(argPtr);
            if(!className)continue;
            if(!isRelevant(className,hint.paramType))continue;
            const key=runtimeIndex+'|'+className+'|'+argPtr.toString();
            if(seen[key])break;
            seen[key]=true;
            matches.push({index:runtimeIndex,ptr:argPtr,className:className});
            break;
        }
    }
    return matches;
}

function isRelevantSnapshotArg(className,paramType){
    const expected=paramType||'';
    if(expected.includes('NetMessageGameStateSync'))return className.includes('NetMessageGameStateSync');
    if(expected.includes('GameStateSnapshot'))return className.includes('GameStateSnapshot');
    if(expected.includes('NetMessageCombatSim'))return className.includes('NetMessageCombatSim');
    if(expected.includes('NetMessageGameSim'))return className.includes('NetMessageGameSim');
    if(expected.includes('NetMessageRunInitialized'))return className.includes('NetMessageRunInitialized');
    return className.includes('NetMessageGameStateSync')||className.includes('GameStateSnapshot')||className.includes('NetMessageCombatSim')||className.includes('NetMessageGameSim')||className.includes('NetMessageRunInitialized');
}

function getSnapshotMatches(method, args) {
    if (method.snapshotHints && method.snapshotHints.length > 0) {
        // QW10: fast path — trust the hint paramType, skip getArgClassName validation.
        // The hints were built from the method signature at attach time, so the arg
        // at hint.runtimeIndex IS the expected type. Skipping getArgClassName saves
        // 5 NativeFunction calls (2x Process.findRangeByAddress + mono_object_get_class
        // + mono_class_get_namespace + mono_class_get_name) per hint per hook.
        if (FAST_GAMESIM_PATH) {
            const matches = [];
            for (const hint of method.snapshotHints) {
                const argPtr = args[hint.runtimeIndex];
                if (!argPtr || argPtr.isNull()) continue;
                matches.push({index: hint.runtimeIndex, ptr: argPtr, className: hint.paramType});
            }
            return matches;
        }
        return matchHintedArgs(args, method.snapshotHints, isRelevantSnapshotArg);
    }
    return [];
}


function getCommandMatches(method,args){
    if(method.commandHints&&method.commandHints.length>0){
        return matchHintedArgs(args,method.commandHints,(className,paramType)=>isCommandClassName(className)||isCommandParamType(paramType));
    }
    return [];
}

function emitCommandProbe(method, reason, matches, args) {
    try {
        // TODO: Remove this early-return once sell command extraction is resolved.
        // The no-matches path calls inspectArgs which reads class names from
        // process memory on the game thread, causing visible lag during combat
        // when OnAuraEffectExecuted and HandleMessage fire dozens of times/sec.
        if (reason === 'no-matches') return;

        const key = formatMethod(method) + '|' + reason;
        commandProbeLogCounts[key] = (commandProbeLogCounts[key] || 0) + 1;
        if (commandProbeLogCounts[key] > 2) return;

        let detail = '';
        if (matches && matches.length > 0) {
            detail = matches.map(m => 'arg' + m.index + '=' + m.className).join(', ');
        } else {
            const inspected = inspectArgs(method, args);
            detail = inspected.length > 0
                ? inspected.map(m => 'arg' + m.index + '=' + m.className).join(', ')
                : 'no object args';
        }

        send({type:'info', msg:'Command probe ' + reason + ' ' + formatMethod(method) + ' :: ' + detail});
    } catch (e) {}
}

function readEmbeddedSnapshotFromObject(objPtr,classKey){try{const info=fieldInfoCache[classKey];if(!info)return null;for(const field of Object.values(info)){if(!field||!field.type)continue;if(field.type.includes('GameStateSnapshotDTO')){const sp=readObjectField(objPtr,classKey,[field.name]);if(sp&&!sp.isNull())return sp;}}}catch(e){send({type:'debug',msg:'readEmbeddedSnapshotFromObject '+classKey+': '+e});}return null;}

function isSnapshotSearchableType(typeName){if(!typeName)return false;if(typeName==='System.String')return false;if(typeName.startsWith('System.Boolean')||typeName.startsWith('System.Int')||typeName.startsWith('System.UInt')||typeName.startsWith('System.Single')||typeName.startsWith('System.Double')||typeName.startsWith('System.Byte')||typeName.startsWith('System.SByte')||typeName.startsWith('System.Char')||typeName.startsWith('System.Guid'))return false;if(typeName.includes('GameStateSnapshotDTO'))return true;if(typeName.startsWith('BazaarGameShared.Infra.Messages.')||typeName.startsWith('TheBazaar.')||typeName.startsWith('BazaarGameClient.'))return true;return false;}

function findSnapshotInObjectGraph(objPtr,depth,seen){try{if(!objPtr||objPtr.isNull()||depth<0)return null;const ptrKey=objPtr.toString();if(seen.has(ptrKey))return null;seen.add(ptrKey);const klass=mono_object_get_class(objPtr);if(!klass||klass.isNull())return null;const className=classFullName(klass);if(className&&className.includes('GameStateSnapshotDTO'))return{ptr:objPtr,source:className};const fields=getDynamicFieldsForKlass(klass);for(const field of fields){if(!field||!field.type)continue;if(field.type.includes('GameStateSnapshotDTO')){const sp=readObjectFieldByInfo(objPtr,field);if(sp&&!sp.isNull())return{ptr:sp,source:(className||'?')+'.'+field.name};}}if(depth===0)return null;for(const field of fields){if(!isSnapshotSearchableType(field.type))continue;const child=readObjectFieldByInfo(objPtr,field);if(!child||child.isNull())continue;const found=findSnapshotInObjectGraph(child,depth-1,seen);if(found)return found;}return null;}catch(e){return null;}}

function summarizeObjectGraph(objPtr,depth,maxEntries,seen,path,out){try{if(!objPtr||objPtr.isNull()||depth<0||out.length>=maxEntries)return;const ptrKey=objPtr.toString();if(seen.has(ptrKey))return;seen.add(ptrKey);const klass=mono_object_get_class(objPtr);if(!klass||klass.isNull())return;const className=classFullName(klass)||'?';const fields=getDynamicFieldsForKlass(klass);for(const field of fields){if(out.length>=maxEntries)break;if(!field||!field.type||!isSnapshotSearchableType(field.type))continue;const child=readObjectFieldByInfo(objPtr,field);const fieldPath=path?path+'.'+field.name:field.name;if(!child||child.isNull()){out.push(fieldPath+': '+field.type+' = null');continue;}const childKlass=mono_object_get_class(child);const childName=childKlass&&!childKlass.isNull()?classFullName(childKlass):'?';out.push(fieldPath+': '+field.type+' -> '+childName);if(depth>0&&childName&&!childName.startsWith('System.'))summarizeObjectGraph(child,depth-1,maxEntries,seen,fieldPath,out);}}catch(e){}}

function emitObjectGraphSummary(label,objPtr,depth){try{graphSummaryLogCounts[label]=(graphSummaryLogCounts[label]||0)+1;if(graphSummaryLogCounts[label]>1)return;const parts=[];summarizeObjectGraph(objPtr,depth,10,new Set(),'',parts);if(parts.length>0)send({type:'debug',msg:'graph:'+label+' '+parts.join(' | ')});}catch(e){}}

function hasSeenMessageId(messageId){return !!(messageId&&seenMessageIds[messageId]);}

function rememberMessageId(messageId){if(!messageId||seenMessageIds[messageId])return;seenMessageIds[messageId]=true;seenMessageOrder.push(messageId);if(seenMessageOrder.length>MAX_SEEN_MESSAGE_IDS){const expired=seenMessageOrder.shift();if(expired)delete seenMessageIds[expired];}}

function hasSeenCommandKey(commandKey){return !!(commandKey&&seenCommandKeys[commandKey]);}

function rememberCommandKey(commandKey){if(!commandKey||seenCommandKeys[commandKey])return;seenCommandKeys[commandKey]=true;seenCommandOrder.push(commandKey);if(seenCommandOrder.length>MAX_SEEN_COMMAND_KEYS){const expired=seenCommandOrder.shift();if(expired)delete seenCommandKeys[expired];}}

function readCommandEventFromMatch(match){const className=match.className||'';const commandInfo=resolveCommandKindInfo(className);if(!commandInfo)return null;const objPtr=match.ptr;const instanceId=readDynamicStringField(objPtr,['InstanceId','CardInstanceId','EncounterId']);const targetSockets=readDynamicIntListField(objPtr,['TargetSockets','TargetSocketIds','Targets']);const singleSocket=readDynamicI32Field(objPtr,['TargetSocket','Socket']);if(singleSocket!==null&&targetSockets.indexOf(singleSocket)<0)targetSockets.push(singleSocket);const section=readDynamicI32Field(objPtr,['Section']);const commandKey=[commandInfo.commandKey,instanceId||'',targetSockets.join(','),section===null?'':String(section),objPtr.toString()].join('|');if(hasSeenCommandKey(commandKey))return null;rememberCommandKey(commandKey);return{command_id:++commandCounter,event_type:commandInfo.eventType,command_class:commandInfo.simpleName,instance_id:instanceId||null,target_sockets:targetSockets,section:section,hook_source:'arg'+match.index+':'+className,timestamp:Date.now()};} // QW5: Date.now() avoids string alloc + Intl plumbing on game thread

function tryExtractCommandEvent(method,args){const matches=getCommandMatches(method,args);if(matches.length===0){emitCommandProbe(method,'no-matches',matches,args);return null;}let sawCommandLike=false;for(const match of matches){if(!isCommandClassName(match.className))continue;sawCommandLike=true;const event=readCommandEventFromMatch(match);if(event)return event;}emitCommandProbe(method,sawCommandLike?'decode-failed':'non-command-matches',matches,args);return null;}

function tryExtractSnapshot(method,args){
    const matches=getSnapshotMatches(method,args);
    let sawSync=false;
    let sawDataNull=false;
    let sawSnapshotArg=false;
    let sawCombatSim=false;
    let sawGameSim=false;
    let sawRunInitialized=false;
    for(const m of matches){
        let sp=null;
        let source='arg'+m.index+':'+m.className;
        let messageId=null;
        let forceFull=false;
        let allowHeavyCards=true;
        let includePlayerAttrs=true;
        if(m.className.includes('NetMessageGameStateSync')){
            sawSync=true;
            messageId=readMessageIdFromNetMessage(m.ptr,'NetMessageGameStateSync');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            sp=readObjectField(m.ptr,'NetMessageGameStateSync',['Data','<Data>k__BackingField']);
            forceFull=false;
            if(!sp||sp.isNull()){
                sawDataNull=true;
                logNullField('NetMessageGameStateSync.Data','was null from '+m.className+' via arg'+m.index);
            }
        }else if(m.className.includes('GameStateSnapshot')){
            sawSnapshotArg=true;
            sp=m.ptr;
            forceFull=false;
        }else if(m.className.includes('NetMessageCombatSim')){
            sawCombatSim=true;
            allowHeavyCards=FULL_DELTA_CARDS;
            includePlayerAttrs=DELTA_PLAYER_ATTRS;
            messageId=FAST_GAMESIM_PATH?_fastReadMessageId(m.ptr,'NetMessageCombatSim'):readMessageIdFromNetMessage(m.ptr,'NetMessageCombatSim');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            const dataPtr=FAST_GAMESIM_PATH?_fastReadDataField(m.ptr,'NetMessageCombatSim'):readObjectField(m.ptr,'NetMessageCombatSim',['Data','<Data>k__BackingField']);
            if(FAST_GAMESIM_PATH){
                const dynSnap=readGameSimFast(dataPtr,allowHeavyCards,includePlayerAttrs,true);
                if(dynSnap){
                    if(messageId)dynSnap.message_id=messageId;
                    maybeReportAttrsStats();
                    return{snapshot:dynSnap,source:source+' -> dynamic-data(fast-combatsim)',reason:'snapshot',message_id:messageId};
                }
            }else{
            let dynSnap=readDynamicStateLean(dataPtr);
            const wantCombatCards=dynSnap&&(allowHeavyCards?shouldReadHeavyCards(dynSnap,false):shouldReadActionCards(dynSnap));
            const wantCombatTemplateEvents=dynSnap&&shouldReadActionTemplateEvents(dynSnap);
            if(includePlayerAttrs||wantCombatCards||wantCombatTemplateEvents){
                const richerDynSnap=readDynamicStatePayload(dataPtr,wantCombatCards,includePlayerAttrs,wantCombatTemplateEvents,dynSnap);
                if(richerDynSnap)dynSnap=richerDynSnap;
            }
            if(dynSnap){
                if(messageId)dynSnap.message_id=messageId;
                return{snapshot:dynSnap,source:source+' -> dynamic-data',reason:'snapshot',message_id:messageId};
            }
            }
            // Hot path: do not crawl the object graph on CombatSim misses.
            // The lean dynamic payload is enough for correlation/state, and the
            // fallback graph walk was a major source of game-thread hitching.
        }else if(m.className.includes('NetMessageGameSim')){
            sawGameSim=true;
            allowHeavyCards=FULL_DELTA_CARDS;
            includePlayerAttrs=DELTA_PLAYER_ATTRS;
            messageId=FAST_GAMESIM_PATH?_fastReadMessageId(m.ptr,'NetMessageGameSim'):readMessageIdFromNetMessage(m.ptr,'NetMessageGameSim');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            const dataPtr=FAST_GAMESIM_PATH?_fastReadDataField(m.ptr,'NetMessageGameSim'):readObjectField(m.ptr,'NetMessageGameSim',['Data','<Data>k__BackingField']);
            // QW9: Fast single-pass path (replaces lean+payload double-read)
            if(FAST_GAMESIM_PATH){
                // Single read with all needed flags — readGameSimFast handles
                // attrs throttling internally, so we always pass includePlayerAttrs
                // and let the function decide whether to actually read them.
                const wantCardsEager=allowHeavyCards;// will be filtered by state inside
                const dynSnap=readGameSimFast(dataPtr,wantCardsEager,includePlayerAttrs,true);
                if(dynSnap){
                    if(messageId)dynSnap.message_id=messageId;
                    maybeReportAttrsStats();
                    return{snapshot:dynSnap,source:source+' -> dynamic-data(fast-gamesim)',reason:'snapshot',message_id:messageId};
                }
            }else{
            // Legacy double-read path
            let dynSnap=readDynamicStateLean(dataPtr);
            const wantGameCards=dynSnap&&(allowHeavyCards?shouldReadHeavyCards(dynSnap,false):shouldReadActionCards(dynSnap));
            const wantGameTemplateEvents=dynSnap&&shouldReadActionTemplateEvents(dynSnap);
            if(includePlayerAttrs||wantGameCards||wantGameTemplateEvents){
                const richerDynSnap=readDynamicStatePayload(dataPtr,wantGameCards,includePlayerAttrs,wantGameTemplateEvents,dynSnap);
                if(richerDynSnap)dynSnap=richerDynSnap;
            }
            if(dynSnap){
                if(messageId)dynSnap.message_id=messageId;
                return{snapshot:dynSnap,source:source+' -> dynamic-data',reason:'snapshot',message_id:messageId};
            }
            }
            // Hot path: do not crawl the object graph on GameSim misses.
            // Keep the lean dynamic snapshot and skip expensive fallback probing.
        }else if(m.className.includes('NetMessageRunInitialized')){
            sawRunInitialized=true;
            messageId=readMessageIdFromNetMessage(m.ptr,'NetMessageRunInitialized');
            if(hasSeenMessageId(messageId))return{snapshot:null,reason:'duplicate-message',message_id:messageId};
            const dataPtr=readObjectField(m.ptr,'NetMessageRunInitialized',['Data','<Data>k__BackingField']);
            let dynSnap=readDynamicStatePayload(dataPtr,false,true,false);
            if(dynSnap&&shouldAllowInlineCardRead(dynSnap,true)){
                const fullDynSnap=readDynamicStatePayload(dataPtr,true,true,false);
                if(fullDynSnap)dynSnap=fullDynSnap;
            }
            if(dynSnap){
                if(messageId)dynSnap.message_id=messageId;
                return{snapshot:dynSnap,source:source+' -> dynamic-data',reason:'snapshot',message_id:messageId};
            }
            const found=readEmbeddedSnapshotFromObject(m.ptr,'NetMessageRunInitialized');
            if(found){
                sp=found.ptr||found;
                source+=' -> '+(found.source||'embedded');
                forceFull=true;
            }
        }else if(m.className.endsWith('GameSimHandler')||m.className.endsWith('CombatSimHandler')||m.className.endsWith('RunInitializedHandler')){
            allowHeavyCards=FULL_DELTA_CARDS;
            // Hot path: skip handler object-graph crawling. These handlers are
            // useful hook surfaces, but walking their graphs on every message is too expensive.
        }
        if(sp&&!sp.isNull()){
            let snap=readGameStateSnapshot(sp,false);
            const wantSnapshotCards=snap&&(allowHeavyCards?shouldReadHeavyCards(snap,forceFull):shouldReadActionCards(snap));
            if(wantSnapshotCards){
                const fullSnap=readGameStateSnapshot(sp,true);
                if(fullSnap)snap=fullSnap;
            }
            if(snap){
                if(messageId)snap.message_id=messageId;
                return{snapshot:snap,source:source,reason:'snapshot',message_id:messageId};
            }
        }
    }
    if(sawDataNull)return{snapshot:null,reason:'data-null'};
    if(sawSync)return{snapshot:null,reason:'sync-without-snapshot'};
    if(sawSnapshotArg)return{snapshot:null,reason:'snapshot-arg-read-failed'};
    if(sawCombatSim)return{snapshot:null,reason:'combat-sim'};
    if(sawGameSim)return{snapshot:null,reason:'game-sim'};
    if(sawRunInitialized)return{snapshot:null,reason:'run-initialized'};
    if(matches.length===0)return{snapshot:null,reason:'no-object-args'};
    return{snapshot:null,reason:'no-matching-arg'};
}

function hookMethod(method){const c=mono_compile_method(method.ptr);if(!c||c.isNull()){send({type:'error',msg:'JIT fail: '+formatMethod(method)});return false;}const codeKey=c.toString();if(hookedCode[codeKey])return hookedCode[codeKey]==='capture';hookedCode[codeKey]='capture';send({type:'info',msg:'Hooking '+formatMethod(method)+' at '+c});Interceptor.attach(c,{onEnter:function(args){const t0=Date.now();let t1=t0;try{resetRangeCache();const methodKey=formatMethod(method);captureCallCounts[methodKey]=(captureCallCounts[methodKey]||0)+1;const callCount=captureCallCounts[methodKey];// QW8: skip tryExtractCommandEvent on methods with no command hints (saves ~0.1ms per call)
const commandEvent=(method.captureCommands&&method.commandHints&&method.commandHints.length>0)?tryExtractCommandEvent(method,args):null;if(method.commandOnly){t1=Date.now();if(commandEvent){commandEvent.t_hook=t0;commandEvent.hook_duration=t1-t0;commandEvent.hook_method=method.name;send({type:'command_event',data:commandEvent});}if((t1-t0)>=SLOW_HOOK_MS){send({type:'perf',stage:'hook',hook:methodKey,hook_duration:t1-t0,call_count:callCount,status:'command-only'});}return;}const hit=tryExtractSnapshot(method,args);t1=Date.now();let status=hit&&hit.reason?hit.reason:'no-result';if(commandEvent&&status==='no-result')status='command';if(VERBOSE_HOOK_CALLS&&(callCount<=5||callCount%10===0)){send({type:'capture_call',method:methodKey,count:callCount,status:status,hook_duration:t1-t0});}if((t1-t0)>=SLOW_HOOK_MS){send({type:'perf',stage:'hook',hook:methodKey,hook_duration:t1-t0,call_count:callCount,status:status});}const snap=hit&&hit.snapshot?hit.snapshot:null;if(commandEvent){commandEvent.t_hook=t0;commandEvent.hook_duration=t1-t0;commandEvent.hook_method=method.name;}if(!snap&&!commandEvent)return;if(snap){const messageId=hit&&hit.message_id?hit.message_id:snap.message_id;if(messageId)rememberMessageId(messageId);snapshotCounter++;snap.id=snapshotCounter;snap.hook=method.name;snap.hook_source=hit&&hit.source?hit.source:null;snap.timestamp=Date.now();snap.t_hook=t0;snap.hook_duration=t1-t0;snap.hook_method=method.name;}if(commandEvent&&snap){send({type:'batch',items:[{type:'command_event',data:commandEvent},{type:'game_state',data:snap}]});}else if(snap){send({type:'game_state',data:snap});}else if(commandEvent){send({type:'command_event',data:commandEvent});}}catch(e){t1=Date.now();send({type:'error',msg:method.name+': '+e+' (hook_duration='+(t1-t0)+'ms)'});}}});return true;}

function attachProbe(method,prefix){if(!ENABLE_PROBES)return false;try{const c=mono_compile_method(method.ptr);if(!c||c.isNull())return false;const codeKey=c.toString();if(hookedCode[codeKey])return hookedCode[codeKey]==='probe';hookedCode[codeKey]='probe';Interceptor.attach(c,{onEnter:function(args){const key=prefix+'.'+method.name+'/'+method.paramCount;probeLogCounts[key]=(probeLogCounts[key]||0)+1;if(probeLogCounts[key]<=4)send({type:'probe',msg:prefix+'.'+formatMethod(method)+' fired (#'+probeLogCounts[key]+')',method:key});}});send({type:'debug',msg:'Probe: '+prefix+'.'+formatMethod(method)});return true;}catch(e){return false;}}

function parseTypeName(typeName){if(!typeName)return null;let t=typeName.trim();if(t.startsWith('class '))t=t.slice(6);if(t.startsWith('valuetype '))t=t.slice(10);const comma=t.indexOf(',');if(comma>=0)t=t.slice(0,comma);const lt=t.indexOf('<');if(lt>=0)t=t.slice(0,lt);const lastDot=t.lastIndexOf('.');if(lastDot<0)return{ns:'',cls:t};return{ns:t.slice(0,lastDot),cls:t.slice(lastDot+1)};}

function hookDataUpdater(handlerKlass){const fields=getFields(handlerKlass);const dataField=fields.find(f=>f.name==='Data'||f.name==='<Data>k__BackingField');if(!dataField||!dataField.type){send({type:'debug',msg:'GameStateHandler Data field not found or has no type info'});return 0;}send({type:'info',msg:'GameStateHandler Data field type: '+dataField.type});const parsed=parseTypeName(dataField.type);if(!parsed)return 0;const dataKlass=findClass(parsed.ns,parsed.cls);if(!dataKlass){send({type:'debug',msg:'Could not resolve data class '+parsed.ns+'.'+parsed.cls});return 0;}const methods=getMethods(dataKlass);send({type:'info',msg:parsed.cls+' methods ('+methods.length+'):'});for(const m of methods)send({type:'debug',msg:'  '+formatMethod(m)});let hooked=0;for(const m of methods){if(m.name.includes('UpdateFromStateSync')||m.name.includes('HandleStateSync')||m.name.includes('ApplyState')||m.name.includes('SyncState')){if(hookMethod(m))hooked++;}}if(ENABLE_PROBES){const skip=['ToString','GetHashCode','Equals','Finalize','MemberwiseClone','GetType','.ctor','.cctor'];for(const m of methods){if(skip.includes(m.name)||m.name.startsWith('get_')||m.name.startsWith('set_'))continue;attachProbe(m,parsed.cls);}}return hooked;}

function hookAllCandidates(klass){const methods=getMethods(klass);const cands=['UpdateFromStateSync','HandleStateSync','OnStateSync','HandleMessage','OnMessage','ProcessMessage','UpdateFromState','OnGameState','HandleGameState','UpdateState','SyncState','ApplyState'];let hooked=0;for(const m of methods){if(cands.some(c=>m.name.includes(c))){if(hookMethod(m))hooked++;}}if(ENABLE_PROBES){const skip=['ToString','GetHashCode','Equals','Finalize','MemberwiseClone','GetType','.ctor','.cctor'];let probes=0;for(const m of methods){if(skip.includes(m.name)||m.name.startsWith('get_')||m.name.startsWith('set_'))continue;if(attachProbe(m,'GameStateHandler'))probes++;}send({type:'info',msg:'Attached '+probes+' passive GameStateHandler probe(s).'});}return hooked;}

function methodHasRelevantParam(method){for(const p of method.params){if(p.includes('NetMessageGameStateSync')||p.includes('GameStateSnapshotDTO')||p.includes('NetMessageCombatSim')||p.includes('NetMessageGameSim')||p.includes('NetMessageRunInitialized'))return true;}return false;}

function methodHasCommandParam(method){for(const p of method.params){if(isCommandParamType(p))return true;}return false;}

function isRelevantGlobalClass(fullName){const exact=['TheBazaar.Data','TheBazaar.NetMessageProcessor','TheBazaar.AppState','TheBazaar.StartRunAppState','TheBazaar.GameStateHandler','TheBazaar.CombatSimHandler','TheBazaar.GameSimHandler','TheBazaar.RunInitializedHandler'];return exact.includes(fullName);}

function isRelevantGlobalMethod(cls,method){
    if(method.name.startsWith('add_')||method.name.startsWith('remove_')||method.name.startsWith('<'))return false;
    if(method.name==='CanProcessMessages')return false;
    if(!methodHasRelevantParam(method))return false;
    const className=cls.fullName;
    if(!ENABLE_BROAD_HOOKS){
        // Keep the default hook set lean, but include the alternate router/state-sync
        // entrypoints that broad mode already trusts. Mak run 339 showed the
        // previous 4-method set can go completely silent while API state traffic
        // still exists, so we need a little more coverage by default.
        if(className==='TheBazaar.NetMessageProcessor'&&method.name==='Handle')return true;
        if(className==='TheBazaar.AppState'&&(method.name==='OnGameStateSyncMessageReceived'||method.name==='OnStateSyncMessage'))return true;
        if(className==='TheBazaar.StartRunAppState'&&method.name==='OnStateSyncMessage')return true;
        if(className==='TheBazaar.GameSimHandler'&&method.name==='HandleMessage')return true;
        if(className==='TheBazaar.CombatSimHandler'&&method.name==='HandleMessage')return true;
        if(className==='TheBazaar.RunInitializedHandler'&&method.name==='HandleMessage')return true;
        return false;
    }
    if(className==='TheBazaar.Data'&&method.name==='UpdateFromStateSync')return true;
    if(className==='TheBazaar.NetMessageProcessor'&&method.name==='Handle')return true;
    if(className==='TheBazaar.AppState'&&(method.name==='OnGameStateSyncMessageReceived'||method.name==='OnStateSyncMessage'))return true;
    if(className==='TheBazaar.StartRunAppState'&&method.name==='OnStateSyncMessage')return true;
    if(className==='TheBazaar.GameStateHandler'&&method.name==='HandleMessage')return true;
    if(className==='TheBazaar.CombatSimHandler'&&method.name==='HandleMessage')return true;
    if(className==='TheBazaar.GameSimHandler'&&method.name==='HandleMessage')return true;
    if(className==='TheBazaar.RunInitializedHandler'&&method.name==='HandleMessage')return true;
    return false;
}

function isRelevantCommandMethod(cls,method){if(method.name.startsWith('add_')||method.name.startsWith('remove_')||method.name.startsWith('<'))return false;if(method.name.startsWith('get_')||method.name.startsWith('set_'))return false;if(method.name==='CanProcessMessages')return false;const className=cls.fullName||'';const hasCommandParam=methodHasCommandParam(method);const commandishName=method.name.includes('Send')||method.name.includes('Handle')||method.name.includes('Execute')||method.name.includes('Process')||method.name.includes('Dispatch')||method.name.includes('Queue');const commandishClass=className.includes('Command')||className.includes('Network')||className.includes('Client')||className.includes('Handler')||className.includes('State')||className.includes('Controller');if(hasCommandParam)return true;if(commandishName&&commandishClass)return true;return false;}

function hookGlobalSearchCandidates(){const assemblies=['TheBazaarRuntime','BazaarGameClient','Assembly-CSharp'];let classCount=0;let methodCount=0;let hooked=0;for(const assemblyName of assemblies){const image=imageMap[assemblyName];if(!image)continue;const classes=enumerateClassesInImage(image,assemblyName);send({type:'info',msg:'Scanning '+classes.length+' classes in '+assemblyName+' for additional state-sync hooks...'});for(const cls of classes){classCount++;if(!isRelevantGlobalClass(cls.fullName))continue;let methods=[];try{methods=getMethods(cls.klass);}catch(e){continue;}const hits=methods.filter(m=>isRelevantGlobalMethod(cls,m));if(hits.length===0)continue;send({type:'debug',msg:'Global candidate '+cls.fullName+' in '+assemblyName+': '+hits.map(formatMethod).join(' | ')});for(const method of hits){
const configured=cloneMethodWithMeta(method,{ownerClass:cls.fullName,snapshotHints:buildSnapshotHints(method),commandHints:buildCommandHints(method),captureCommands:true,commandOnly:false});methodCount++;if(hookMethod(configured))hooked++;}}}send({type:'info',msg:'Global scan checked '+classCount+' classes and '+methodCount+' focused candidate method(s); hooked '+hooked+'.'});return hooked;}
const COMMAND_HOOK_ALLOWLIST = {
    SelectItemCommand: true,
    SelectSkillCommand: true,
    SellCardCommand: true,
    RerollCommand: true,
    SelectEncounterCommand: true,
    CommitToPedestalCommand: true,
    ExitCurrentStateCommand: true,
};
function hookCommandSearchCandidates(){const assemblies=['TheBazaarRuntime','BazaarGameClient','Assembly-CSharp'];let classCount=0;let methodCount=0;let hooked=0;for(const assemblyName of assemblies){const image=imageMap[assemblyName];if(!image)continue;const classes=enumerateClassesInImage(image,assemblyName);send({type:'info',msg:'Scanning '+classes.length+' classes in '+assemblyName+' for command hooks...'});for(const cls of classes){classCount++;let methods=[];try{methods=getMethods(cls.klass);}catch(e){continue;}const hits=methods.filter(m=>isRelevantCommandMethod(cls,m));if(hits.length===0)continue;// F7: filter to allowed command types only
const allowedHits=hits.filter(m=>{for(const p of m.params){const simple=(p.split('.').pop()||'').split('`')[0];if(COMMAND_HOOK_ALLOWLIST[simple])return true;}return false;});if(allowedHits.length===0)continue;send({type:'debug',msg:'Command candidate '+cls.fullName+' in '+assemblyName+': '+allowedHits.map(formatMethod).join(' | ')});for(const method of allowedHits){const configured=cloneMethodWithMeta(method,{ownerClass:cls.fullName,commandHints:buildCommandHints(method),captureCommands:true,commandOnly:true});methodCount++;if(hookMethod(configured))hooked++;}}}send({type:'info',msg:'Command scan checked '+classCount+' classes and '+methodCount+' candidate method(s); hooked '+hooked+'.'});return hooked;}

// QW1: Pre-warm DTO field caches at attach time to eliminate first-encounter 50-100ms spikes.
// Called before hooks are installed so the hook thread never hits a cold class walk.
function prewarmFieldInfoCache(){
    // Known DTO types from searchTargets and message types
    const prewarmTypes=[
        {ns:'BazaarGameShared.Infra.Messages',cls:'GameStateSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'RunSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'PlayerSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'RunStateSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameStateSync'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageCombatSim'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageGameSim'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'NetMessageRunInitialized'},
        {ns:'BazaarGameShared.Infra.Messages',cls:'CardSnapshotDTO'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'GameSim'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdateRun'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdateRunState'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdatePlayer'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'SimUpdateCard'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'CardDeltaPlacement'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'GameSimEventCardDealt'},
        {ns:'BazaarGameShared.Infra.Messages.GameSimEvents',cls:'GameSimEventCardSpawned'},
    ];
    let warmed=0;
    for(const t of prewarmTypes){
        try{
            const fullName=(t.ns?t.ns+'.':'')+t.cls;
            if(fieldInfoCache[fullName]){warmed++;continue;}
            // Check if already cached under short key from searchTargets loop
            if(fieldInfoCache[t.cls]){
                // Re-register under fullName key so getFieldInfoForTypeName finds it
                fieldCache[fullName]=fieldCache[t.cls];
                fieldInfoCache[fullName]=fieldInfoCache[t.cls];
                warmed++;continue;
            }
            // Otherwise do a cold walk now (at attach time, not on hook thread)
            const klass=foundClasses[t.cls]?foundClasses[t.cls].klass:findClass(t.ns,t.cls);
            if(!klass||klass.isNull())continue;
            const fields=getFields(klass);
            const map={};const info={};
            for(const f of fields){map[f.name]=f.offset;info[f.name]=f;}
            fieldCache[fullName]=map;fieldInfoCache[fullName]=info;
            dynamicFieldInfoCache[fullName]=fields;
            warmed++;
        }catch(e){send({type:'debug',msg:'prewarm '+t.cls+': '+e});}
    }
    send({type:'info',msg:'QW1: pre-warmed '+warmed+'/'+prewarmTypes.length+' DTO field caches.'});
}

// Execute
const __captureMonoInitialized=(function(){
    // QW1: pre-warm before hooks fire to eliminate cold-walk spikes
    prewarmFieldInfoCache();
    _fieldInfoPrewarmed=true;
    const gh=hookGlobalSearchCandidates();const ch=hookCommandSearchCandidates();if(ENABLE_BROAD_HOOKS&&foundClasses['GameStateHandler']){const handlerKlass=foundClasses['GameStateHandler'].klass;const h=hookAllCandidates(handlerKlass);const dh=hookDataUpdater(handlerKlass);const total=h+dh+gh+ch;if(total>0)send({type:'ready',msg:'Mono hooks active. '+total+' capture method(s) hooked.'});else send({type:'info',msg:'Probes attached - play to identify methods.'});}else if(gh+ch>0){send({type:'ready',msg:'Mono hooks active. '+(gh+ch)+' capture method(s) hooked.'});}else if(foundClasses['GameStateHandler']){send({type:'info',msg:'Searching broader namespaces...'});const nsG=['TheBazaar','TheBazaar.Runtime','TheBazaar.Game','TheBazaar.Infra','TheBazaar.Network','TheBazaar.State','Bazaar','Game','','Runtime'];let found=false;for(const[an,img]of Object.entries(imageMap)){if(!['TheBazaarRuntime','Assembly-CSharp','BazaarGameClient'].includes(an))continue;for(const ns of nsG){const k=mono_class_from_name(img,Memory.allocUtf8String(ns),Memory.allocUtf8String('GameStateHandler'));if(!k.isNull()){send({type:'info',msg:'FOUND at ns="'+ns+'" in '+an});foundClasses['GameStateHandler']={klass:k,ns};if(ENABLE_BROAD_HOOKS){const h=hookAllCandidates(k);const dh=hookDataUpdater(k);const gh2=hookGlobalSearchCandidates();const ch2=hookCommandSearchCandidates();if(h+dh+gh2+ch2>0)send({type:'ready',msg:'Mono hooks active. '+(h+dh+gh2+ch2)+' capture method(s) hooked.'});}found=true;break;}}if(found)break;}if(!found)send({type:'error',msg:'GameStateHandler not found. Assemblies: '+Object.keys(imageMap).join(', ')});}else{send({type:'error',msg:'No preferred capture hooks resolved. Assemblies: '+Object.keys(imageMap).join(', ')});}return true;})();
if(false&&foundClasses['GameStateHandler']){const h=hookAllCandidates(foundClasses['GameStateHandler'].klass);if(h>0)send({type:'ready',msg:'Mono hooks active. '+h+' method(s) hooked.'});else send({type:'info',msg:'Probes attached - play to identify methods.'});}else if(false){send({type:'info',msg:'Searching broader namespaces...'});const nsG=['TheBazaar','TheBazaar.Runtime','TheBazaar.Game','TheBazaar.Infra','TheBazaar.Network','TheBazaar.State','Bazaar','Game','','Runtime'];let found=false;for(const[an,img]of Object.entries(imageMap)){if(!['TheBazaarRuntime','Assembly-CSharp','BazaarGameClient'].includes(an))continue;for(const ns of nsG){const k=mono_class_from_name(img,Memory.allocUtf8String(ns),Memory.allocUtf8String('GameStateHandler'));if(!k.isNull()){send({type:'info',msg:'FOUND at ns="'+ns+'" in '+an});foundClasses['GameStateHandler']={klass:k,ns};hookAllCandidates(k);found=true;break;}}if(found)break;}if(!found)send({type:'error',msg:'GameStateHandler not found. Assemblies: '+Object.keys(imageMap).join(', ')});}
