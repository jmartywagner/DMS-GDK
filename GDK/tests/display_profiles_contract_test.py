#!/usr/bin/env python3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
h=(ROOT/'GDK/include/dms_video.h').read_text(encoding='utf-8')
c=(ROOT/'GDK/lib/src/dms_video.c').read_text(encoding='utf-8')
v=(ROOT/'RUNTIME/tools/dms_console90_vdp.py').read_text(encoding='utf-8')
host=(ROOT/'RUNTIME/frontends/runtime/dms1_native_host.cpp').read_text(encoding='utf-8')
bridge=(ROOT/'RUNTIME/tools/dms_native_host.py').read_text(encoding='utf-8')
for token in ('DMS_VIDEO_RAW','DMS_VIDEO_SCANLINES','DMS_VIDEO_CRT_SOFT','DMS_VIDEO_CRT_SCANLINES','DMS_VIDEO_COMPOSITE','VIDEO_setProfile'):
    assert token in h, token
assert 'vdp[5] = profile' in c
assert 'presentation_profile' in v and 'offset == 0x05' in v
for token in ('DISPLAY_SCANLINES','DISPLAY_CRT_SOFT','DISPLAY_CRT_SCANLINES','DISPLAY_COMPOSITE','VK_F11','PKT_DISPLAY_PROFILE'):
    assert token in host, token
assert 'FRAME_META = struct.Struct("<Q6iII")' in bridge
assert 'PKT_DISPLAY_PROFILE = 5' in bridge
print('DMS DISPLAY PROFILES V1.1 CONTRACT OK')
