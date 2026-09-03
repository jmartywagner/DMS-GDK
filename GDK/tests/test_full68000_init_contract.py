from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "RUNTIME" / "native_cpu" / "dms1_m68k_bridge.c"
text = BRIDGE.read_text(encoding="utf-8")
init = text.split("DMS_EXPORT int dms68k_init(void)",1)[1].split("DMS_EXPORT int dms68k_load_rom",1)[0]
assert "m68k_init();" in init, "Musashi opcode table init missing"
assert init.index("m68k_init();") < init.index("m68k_set_cpu_type"), "m68k_init must precede CPU type selection"
print("PASS: Musashi init contract (m68k_init -> set_cpu_type) present")
